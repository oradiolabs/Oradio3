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
# signs with; HW_REVISION must match the hardware-compatibility list in the
# package.
CERT="oradio3-signing.cert.pem"
BOARD_NAME="oradio3"
HW_REVISION="1.0"

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

[[ -f "$CERT" ]] || die "signing certificate not found: $CERT
    Copy it from the build host; swupdate rejects unsigned packages."

# swupdate refuses to install with "HW compatibility not found" if this file is
# missing, whatever the package says.
if [[ ! -f /etc/hwrevision ]]; then
	warn "/etc/hwrevision missing — creating it as '$BOARD_NAME $HW_REVISION'"
	printf '%s %s\n' "$BOARD_NAME" "$HW_REVISION" >/etc/hwrevision
fi
info "hwrevision: $(cat /etc/hwrevision)"

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
# Look for the switcher next to this script, then in the working directory, then
# on the usual install path. Test for existence rather than the execute bit and
# run it through bash: a script copied over scp or from a Windows-side path
# routinely arrives without +x, which says nothing about whether it is usable.
SELF_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SWITCH_CANDIDATES=(
	"$SELF_DIR/ab-boot-switch.sh"
	"./ab-boot-switch.sh"
	"/usr/local/sbin/ab-boot-switch.sh"
	"/usr/local/bin/ab-boot-switch.sh"
)

SWITCH=""
for c in "${SWITCH_CANDIDATES[@]}"; do
	[[ -f "$c" ]] && SWITCH="$c" && break
done

if [[ -z "$SWITCH" ]]; then
	warn "ab-boot-switch.sh not found. Looked in:"
	for c in "${SWITCH_CANDIDATES[@]}"; do warn "  $c"; done
	warn "The slot is installed but the Pi will still boot $RUNNING_DEV."
	warn "Set the pointer manually, then reboot."
	exit 1
fi

log "Pointing next boot at slot $TARGET_SLOT and rebooting"
info "using $SWITCH"

# Printed before the handover, because --reboot does not return.
cat <<EOF

    If the Pi does not come back, put the card in a reader and restore
    cmdline.txt.bak from the boot partition — that returns you to
    $RUNNING_DEV, the slot that is running now.

EOF

# --reboot fires only once the new root= has been written and read back. If
# ab-boot-switch.sh refuses — an incomplete slot, or an fstab naming a boot
# partition that is not on this card — nothing is changed, nothing reboots, and
# the Pi stays up on $RUNNING_DEV.
bash "$SWITCH" --to "$TARGET_SLOT" --reboot
