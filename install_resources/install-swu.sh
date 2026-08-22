#!/usr/bin/env bash
#
# install-swu.sh — install an Oradio3 .swu into the INACTIVE A/B slot.
#
# THIS RUNS ON THE PI.
#
# SWUpdate does not know which slot is standby: detecting it and passing
# -e <selection,mode> is the caller's job. This script works out which slot is
# running, installs into the other one, moves the boot pointer, and reboots.
# Each step happens only if the one before it succeeded.
#
set -euo pipefail

SWU=""
DRY_RUN=0

LABEL_A="rootfs_a"
LABEL_B="rootfs_b"

# Distro swupdate builds require signed packages and check hardware
# compatibility. The certificate is the public half of the key build-swu.sh
# signs with.
CERT="oradio3-signing.cert.pem"

# Written by ab-boot-trial.sh. Read here only, to refuse an install that would
# overwrite the slot an undecided trial falls back to.
TRIAL_STATE="/boot/firmware/oradio3-boot.state"

# The rest of the toolkit. Installed together, so their location is known rather
# than searched for.
SWITCH_SCRIPT="/usr/local/sbin/ab-boot-switch.sh"
TRIAL_SCRIPT="/usr/local/sbin/ab-boot-trial.sh"

# Per-unit hardware identity. NOT a constant here: see the check below for why
# this script will not supply a value of its own.
HWREVISION_FILE="/etc/hwrevision"

usage() {
	cat <<EOF
Usage: sudo $0 -i PACKAGE.swu [options]

  -i, --image FILE   the .swu package to install
  -k, --cert FILE    signing certificate to verify against (default: ${CERT})
  -n, --dry-run      let swupdate parse and check the package, install nothing
  -h, --help         this text
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	-i | --image)
		SWU="$2"
		shift 2
		;;
	-k | --cert)
		CERT="$2"
		shift 2
		;;
	-n | --dry-run)
		DRY_RUN=1
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '\033[1;32m[ok] %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*" >&2; }
die() {
	printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2
	exit 1
}

# `systemctl enable` creates an ABSOLUTE symlink, e.g.
#   .../multi-user.target.wants/ab-boot-check.service
#     -> /etc/systemd/system/ab-boot-check.service
# Read from outside the rootfs that target resolves against OUR root, not the
# image's, so -e is always false here however correctly the unit was enabled.
# Test the link itself with -L, then resolve its target under the mount point.
unit_enabled() { # unit_enabled <root> <wants-dir> <unit>
	local root="$1" link="$1/etc/systemd/system/$2/$3" target
	[[ -L "$link" || -e "$link" ]] || return 1
	if [[ -L "$link" ]]; then
		target="$(readlink "$link")"
		# absolute inside the image -> re-root it; relative -> resolve as-is
		[[ "$target" == /* ]] && target="$root$target" ||
			target="$1/etc/systemd/system/$2/$target"
		[[ -e "$target" ]] || return 1
	fi
	return 0
}

require_raspberry_pi() {
	local model="" f
	for f in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
		[[ -r "$f" ]] && model="$(tr -d '\0' <"$f")" && break
	done
	if [[ -z "$model" && -r /proc/cpuinfo ]]; then
		model="$(sed -n 's/^Model[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo | head -1)"
	fi
	if [[ "$model" != *"Raspberry Pi"* ]]; then
		warn "detected system: ${model:-unknown}"
		die "This script must run on a Raspberry Pi, over SSH — not on the WSL host."
	fi
	printf '%s' "$model"
}

MODEL="$(require_raspberry_pi)"
[[ $EUID -eq 0 ]] || die "Run as root: sudo $0 -i <package.swu>"
[[ -n "$SWU" ]] || {
	usage >&2
	exit 2
}
[[ -f "$SWU" ]] || die "no such file: $SWU"

command -v swupdate >/dev/null || die "swupdate is not installed. Run: apt install swupdate"
command -v mkfs.ext4 >/dev/null || die "missing tool: mkfs.ext4 (apt install e2fsprogs)"

[[ -f "$SWITCH_SCRIPT" ]] || die "$SWITCH_SCRIPT not found — is the toolkit installed?"
# Rollback is not optional: without this nothing could ever commit a trial.
[[ -f "$TRIAL_SCRIPT" ]] || die "$TRIAL_SCRIPT not found — is the toolkit installed?"

[[ -f "$CERT" ]] || die "signing certificate not found: $CERT
    Copy it from the build host; swupdate rejects unsigned packages."

# --------------------------------------------------- hardware identity --
# swupdate matches this file against the package's hardware-compatibility list
# and refuses with "HW compatibility not found" when it is absent, whatever the
# package says. That is the right behaviour and this script must not paper over
# it.
#
# The file is per-unit: it describes the board, not the software. No package
# carries one — build-swu.sh excludes it from the payload precisely so a package
# cannot supply it, and swu-slot.sh copies THIS board's value into each slot it
# installs. Inventing a value here would defeat all of that, in the worst way:
# a guessed revision either happens to match the package, making the
# compatibility check meaningless, or quietly records this board as hardware it
# may not be — and once written it persists, travelling into every slot
# installed afterwards.
#
# A board with no recorded revision is a provisioning mistake, not something to
# repair mid-install. Stop, and say what to write.
[[ -f "$HWREVISION_FILE" ]] || die "$HWREVISION_FILE not found.

    It records what this board IS, so nothing but provisioning may write it —
    this script will not guess a value, and no package can supply one.

    Check what the package expects:
      cpio -i --to-stdout sw-description <'$SWU' | grep hardware-compatibility

    Then write the matching revision on this board, once:
      printf 'oradio3 <revision>\\n' | sudo tee $HWREVISION_FILE"

HWREVISION="$(grep -v '^[[:space:]]*$' "$HWREVISION_FILE" | head -1 || true)"
[[ -n "$HWREVISION" ]] || die "$HWREVISION_FILE is empty.
    swupdate would refuse this install with 'HW compatibility not found'.
    Write the board's revision, as above."
info "hwrevision: $HWREVISION"

# ------------------------------------------------- which slot is running? --
# Slot identity is the partition number: 2 is slot A, 3 is slot B. Labels are
# still applied for readability but nothing depends on them.
part_dev() {
	if [[ "$1" =~ [0-9]$ ]]; then echo "${1}p${2}"; else echo "${1}${2}"; fi
}

RUNNING_DEV="$(findmnt -nro SOURCE / || true)"
[[ -n "$RUNNING_DEV" ]] || die "could not determine the running root device"

DISK="$(lsblk -nro PKNAME "$RUNNING_DEV" 2>/dev/null | head -1 || true)"
[[ -n "$DISK" ]] || die "could not determine the disk holding $RUNNING_DEV"
DISK="/dev/$DISK"

SLOT_A_DEV="$(part_dev "$DISK" 2)"
SLOT_B_DEV="$(part_dev "$DISK" 3)"
[[ -b "$SLOT_B_DEV" ]] ||
	die "$SLOT_B_DEV does not exist, so there is nowhere to install.
    Slot B is created on first boot by ab-expand.service. Check whether it ran:
      systemctl status ab-expand.service
      cat /var/log/ab-expand.log"

case "$RUNNING_DEV" in
"$SLOT_A_DEV")
	TARGET_SLOT="b"
	TARGET_DEV="$SLOT_B_DEV"
	TARGET_LABEL="$LABEL_B"
	SELECTION="stable,copy-b"
	;;
"$SLOT_B_DEV")
	TARGET_SLOT="a"
	TARGET_DEV="$SLOT_A_DEV"
	TARGET_LABEL="$LABEL_A"
	SELECTION="stable,copy-a"
	;;
*)
	warn "running root is $RUNNING_DEV, which is neither $SLOT_A_DEV nor $SLOT_B_DEV"
	die "This card is not set up for A/B. Build it with prepare-ab-card.sh first."
	;;
esac

# ------------------------------------------------ the package must stream --
# A streamed package goes straight from the cpio into the handler and needs no
# scratch space. Anything else is staged in TMPDIR first, which on Raspberry Pi
# OS is a ~200MB tmpfs — far too small for a rootfs tarball. Catch it here so
# the message names the cause.
if command -v cpio >/dev/null; then
	if ! cpio -i --to-stdout sw-description <"$SWU" 2>/dev/null |
		grep -q 'installed-directly'; then
		warn "$SWU does not stream its payload."
		die "SWUpdate would stage $(($(stat -c %s "$SWU") / 1024 / 1024))MB in
    \$TMPDIR, a small tmpfs here, and fail. Rebuild it with build-swu.sh,
    which marks the payload installed-directly."
	fi
fi

log "Plan"
info "model    : $MODEL"
info "package  : $SWU ($(stat -c %s "$SWU") bytes)"
info "cert     : $CERT"
info "running  : $RUNNING_DEV (slot $([[ "$TARGET_SLOT" == "b" ]] && echo a || echo b))"
info "target   : $TARGET_DEV (slot $TARGET_SLOT)"
info "selection: $SELECTION"
info "payload  : streamed straight to the slot, no scratch space needed"

[[ "$TARGET_DEV" != "$RUNNING_DEV" ]] ||
	die "target and running device are both $RUNNING_DEV — refusing to overwrite the running system"

# ------------------------------------------------------------- dry run --
if ((DRY_RUN)); then
	log "Dry run"
	# -c checks that everything sw-description references is present in the SWU
	swupdate -c -i "$SWU" -e "$SELECTION" -k "$CERT"
	ok "Package is well-formed and the selection resolves."
	exit 0
fi

warn "Everything on $TARGET_DEV (slot $TARGET_SLOT) will be overwritten."
info "The running system on $RUNNING_DEV is not touched."

# ------------------------------------------- is a trial still in progress? --
# While a trial is uncommitted, the "inactive" slot is the committed one — the
# slot the Pi falls back to. Installing into it destroys the only known-good
# system on the card, and the trial timeout may reboot part-way through, which
# leaves it neither the old release nor the new one.
#
# Commit the trial or let it roll back first. Either way the card then has one
# slot that is known good, which is what makes an install safe.
if [[ -r "$TRIAL_STATE" ]] &&
	[[ "$(sed -n 's/^state=//p' "$TRIAL_STATE" | tail -1)" == "trying" ]]; then
	TRIAL_SLOT="$(sed -n 's/^trial=//p' "$TRIAL_STATE" | tail -1)"
	warn "A trial boot of slot ${TRIAL_SLOT:-?} is still uncommitted."
	warn ""
	warn "Installing now would write to slot $TARGET_SLOT, which is the slot this"
	warn "trial falls back to — the only known-good system on this card. The trial"
	warn "timeout could also reboot part-way through the install."
	warn ""
	warn "Settle the trial first:"
	warn "  sudo ab-boot-trial.sh commit    # keep the slot running now"
	warn "  sudo reboot                     # or roll back to slot ${TRIAL_SLOT:+$([[ $TRIAL_SLOT == a ]] && echo b || echo a)}"
	die "refusing to install while a trial is undecided"
fi

# ------------------------------------------------- kernel compatibility --
# The kernel lives in /boot/firmware, which both slots share and no package
# carries. A rootfs whose modules target a different kernel release will boot
# and then load none of them: no wifi, no audio, and nothing obvious in the
# logs. Refuse before writing anything.
#
# The declaration is inside the signed sw-description, so it cannot be altered
# without invalidating the signature. Packages built before this field existed
# simply do not have it, and are allowed through with a warning.
# What matters is the kernel that will BOOT the installed slot, which is not
# always the one running now: after an apt upgrade the new kernel is already in
# /boot/firmware while uname -r still reports the old one until a reboot.
#
# So accept a package matching either:
#   - the running kernel                    (nothing has changed)
#   - any kernel installed in this rootfs    (an upgrade is pending a reboot)
# and refuse only when it matches neither.
RUNNING_KERNEL="$(uname -r)"
mapfile -t LOCAL_KERNELS < <(ls -1 /lib/modules 2>/dev/null || true)

PKG_KERNELS="$(head -c 65536 -- "$SWU" 2>/dev/null |
	cpio -i --to-stdout sw-description 2>/dev/null |
	sed -n 's/.*kernel-release:[[:space:]]*\[\(.*\)\].*/\1/p' |
	tr -d '" ' | tr ',' ' ' || true)"

if [[ -z "$PKG_KERNELS" ]]; then
	warn "package declares no kernel-release; cannot check module compatibility"
	warn "  running kernel is $RUNNING_KERNEL"

elif [[ " $PKG_KERNELS " == *" $RUNNING_KERNEL "* ]]; then
	info "kernel   : $RUNNING_KERNEL (package carries modules for it)"

else
	# Not the running kernel. If it is one this rootfs already has modules for,
	# an upgrade has been installed and is waiting for a reboot — the package is
	# built for what will actually boot.
	PENDING=""
	for k in "${LOCAL_KERNELS[@]}"; do
		[[ " $PKG_KERNELS " == *" $k "* ]] && PENDING="$k" && break
	done

	if [[ -n "$PENDING" ]]; then
		warn "package targets $PENDING but this Pi is still running $RUNNING_KERNEL"
		warn "  $PENDING is installed here, so a kernel upgrade is pending a reboot."
		warn "  The installed slot will be correct once the Pi reboots into it."
		warn ""
		warn "  Reboot into the new kernel BEFORE relying on this slot: the trial"
		warn "  boot will load $PENDING, and the slot you would roll back to still"
		warn "  has to work with it."
	else
		warn "This package carries modules for: $PKG_KERNELS"
		warn "but this Pi boots kernel: $RUNNING_KERNEL"
		warn "and has modules only for: ${LOCAL_KERNELS[*]:-none}"
		warn ""
		warn "The kernel is in /boot/firmware, shared by both slots and not carried"
		warn "by any package, so the installed slot would boot this kernel and find"
		warn "no modules for it — no wifi, no audio, no clear error."
		warn ""
		warn "Either rebuild the package from a rootfs matching this kernel, or"
		warn "re-image the card, which replaces kernel and rootfs together."
		die "refusing: package modules do not match any kernel on this Pi"
	fi
fi

# ------------------------------------------------------- format the slot --
# The tarball is streamed, so it is unpacked while SWUpdate reads the cpio —
# before any preinstall script in the package could run. That means the slot has
# to be a mounted-able, empty ext4 filesystem before SWUpdate starts.
#
# Formatting also matters for correctness: a tarball unpacks ONTO a filesystem
# rather than replacing one, so without this every file the new release deleted
# would survive from whatever was in the slot before.
log "Formatting $TARGET_DEV as $TARGET_LABEL"
umount "$TARGET_DEV" 2>/dev/null || true
if ! mkfs_out="$(mkfs.ext4 -q -F -L "$TARGET_LABEL" "$TARGET_DEV" 2>&1)"; then
	printf '%s\n' "$mkfs_out" >&2
	die "could not format $TARGET_DEV"
fi
info "fresh ext4, label $TARGET_LABEL"

# --------------------------------------------------------------- install --
# -M and -m disable SWUpdate's bootloader transaction and state markers. Those
# expect U-Boot, GRUB or EFI Boot Guard; the Pi's firmware bootloader is none of
# them, so leaving them on makes the run fail at the last step. The boot pointer
# is moved by ab-boot-switch.sh below instead.
log "Installing into $TARGET_LABEL"
if ! swupdate -v -i "$SWU" -e "$SELECTION" -k "$CERT" -M -m; then
	die "swupdate failed — slot $TARGET_SLOT is formatted but incomplete. The running
    system on $RUNNING_DEV is untouched, so the Pi still boots. Re-running
    reformats the slot and starts over."
fi

ok "Installed into slot $TARGET_SLOT ($TARGET_DEV)."

# ------------------------------------------------------- move the pointer --
# Everything that ends a trial lives inside the slot being trialled, so the slot
# has to carry all of it:
#
#   ab-boot-trial.sh       the only thing that can commit
#   ab-boot-timeout.timer  the deadline that undoes an uncommitted trial
#   ab-boot-check.service  notices a rollback and records the failed version
#
# Enabled, not merely installed: a unit file with no .wants symlink never runs.
# Miss the timer and a slot that boots but never commits would run until the
# next reboot and then silently revert — an update that appears to work and then
# disappears days later.
SLOT_MNT="$(mktemp -d)"
MISSING=()
if mount -o ro "$TARGET_DEV" "$SLOT_MNT" 2>/dev/null; then
	[[ -f "$SLOT_MNT$TRIAL_SCRIPT" ]] ||
		MISSING+=("$TRIAL_SCRIPT (nothing could commit the trial)")

	unit_enabled "$SLOT_MNT" timers.target.wants ab-boot-timeout.timer ||
		MISSING+=("ab-boot-timeout.timer, enabled (nothing would end an uncommitted trial)")

	unit_enabled "$SLOT_MNT" multi-user.target.wants ab-boot-check.service ||
		MISSING+=("ab-boot-check.service, enabled (a rollback would go unrecorded)")

	umount "$SLOT_MNT"
else
	MISSING+=("could not mount $TARGET_DEV to check it")
fi
rmdir "$SLOT_MNT"

if ((${#MISSING[@]})); then
	warn "The installed slot cannot see a trial through. Missing:"
	for m in "${MISSING[@]}"; do warn "  - $m"; done
	warn ""
	warn "Install and enable the pi/ scripts and units on the reference Pi"
	warn "BEFORE building the release, so every package carries them."
	die "refusing to start a trial that cannot be committed or timed out"
fi

log "Starting a trial boot of slot $TARGET_SLOT"

# Printed before the handover, because the reboot does not return.
cat <<EOF

    The new slot is booted ONCE, on trial. config.txt still points at
    $RUNNING_DEV, so a panic, a hang, a reset, or the trial timeout
    returns the Pi to the slot running now — no action needed.

    Once the system is healthy, make it permanent:
      sudo ab-boot-trial.sh commit

EOF

# --trial writes tryboot.txt and reboots with the firmware's one-shot flag, so
# config.txt keeps pointing at the running slot. If ab-boot-switch.sh refuses —
# an incomplete slot, or an fstab naming a boot partition that is not on this
# card — nothing is changed and the Pi stays up on $RUNNING_DEV.
bash "$SWITCH_SCRIPT" --to "$TARGET_SLOT" --trial --reboot
