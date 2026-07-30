#!/usr/bin/env bash
#
# ab-boot-switch.sh — switch which root partition the Pi boots from next.
#
# THIS RUNS ON THE PI. It edits root=PARTUUID=... in cmdline.txt and nothing
# else; the running system is untouched until you reboot.
#
#     sudo ./ab-boot-switch.sh              # toggle to the other slot
#     sudo ./ab-boot-switch.sh --to b       # boot slot B next, whatever is set now
#     sudo ./ab-boot-switch.sh --status     # show current state, change nothing
#
# Slots are identified by partition number: partition 2 is slot A, partition 3
# is slot B. Boot references use PARTUUID, which the Raspberry Pi kernel
# resolves by itself. root=LABEL= and root=UUID= would need an initramfs to
# resolve them in userspace first.
#
set -euo pipefail

TARGET=""
STATUS_ONLY=0
FORCE=0
REBOOT=0

usage() {
	cat <<EOF
Usage: sudo $0 [options]

With no options, toggles between slot A (partition 2) and slot B (partition 3).

      --to a|b     boot that slot next, regardless of what is set now
      --status     print the current state and exit without changing anything
      --force      switch even if the target slot looks empty or unbootable
      --reboot     reboot afterwards, only if the switch actually succeeded
  -h, --help       this text
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--to)
		TARGET="$2"
		shift 2
		;;
	--status)
		STATUS_ONLY=1
		shift
		;;
	--force)
		FORCE=1
		shift
		;;
	--reboot)
		REBOOT=1
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

# ----------------------------------------------------------------- helpers --
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

# /dev/mmcblk0 -> /dev/mmcblk0p2 ; /dev/sda -> /dev/sda2
part_dev() {
	if [[ "$1" =~ [0-9]$ ]]; then echo "${1}p${2}"; else echo "${1}${2}"; fi
}

partuuid() { [[ -n "${1:-}" ]] && blkid -s PARTUUID -o value "$1" 2>/dev/null || true; }
fslabel() { [[ -n "${1:-}" ]] && blkid -s LABEL -o value "$1" 2>/dev/null || true; }

MODEL="$(require_raspberry_pi)"
[[ $EUID -eq 0 ]] || die "Run as root: sudo $0"
command -v blkid >/dev/null || die "missing tool: blkid"

# --------------------------------------------------------- locate cmdline --
CMDLINE=""
for c in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
	[[ -f "$c" ]] && CMDLINE="$c" && break
done
[[ -n "$CMDLINE" ]] || die "cmdline.txt not found — is the boot partition mounted?"

# -------------------------------------------------------------- the disk --
RUNNING_DEV="$(findmnt -nro SOURCE / || true)"
[[ -n "$RUNNING_DEV" ]] || die "could not determine the running root device"

DISK="$(lsblk -nro PKNAME "$RUNNING_DEV" 2>/dev/null | head -1 || true)"
[[ -n "$DISK" ]] || die "could not determine the disk holding $RUNNING_DEV"
DISK="/dev/$DISK"

SLOT_A_DEV="$(part_dev "$DISK" 2)"
SLOT_B_DEV="$(part_dev "$DISK" 3)"
[[ -b "$SLOT_A_DEV" ]] || die "$SLOT_A_DEV does not exist — is this an A/B card?"
[[ -b "$SLOT_B_DEV" ]] || die "$SLOT_B_DEV does not exist — is this an A/B card?"

PU_A="$(partuuid "$SLOT_A_DEV")"
PU_B="$(partuuid "$SLOT_B_DEV")"
[[ -n "$PU_A" && -n "$PU_B" ]] || die "could not read PARTUUIDs from $DISK"

# ------------------------------------------------------------ read state --
# Exactly one root= must be present; with two the kernel takes the last, and a
# substitution on the first would silently do nothing useful.
ROOT_COUNT="$(grep -o 'root=[^[:space:]]*' "$CMDLINE" | wc -l)"
((ROOT_COUNT == 1)) || die "expected exactly one root= parameter in $CMDLINE, found $ROOT_COUNT"

CURRENT_ROOT="$(grep -o 'root=[^[:space:]]*' "$CMDLINE")"

case "$CURRENT_ROOT" in
"root=PARTUUID=$PU_A") CURRENT_SLOT="a" ;;
"root=PARTUUID=$PU_B") CURRENT_SLOT="b" ;;
*) CURRENT_SLOT="" ;;
esac

case "$RUNNING_DEV" in
"$SLOT_A_DEV") RUNNING_SLOT="a" ;;
"$SLOT_B_DEV") RUNNING_SLOT="b" ;;
*) RUNNING_SLOT="?" ;;
esac

log "Current state"
info "model        : $MODEL"
info "file         : $CMDLINE"
info "disk         : $DISK"
info "slot A       : $SLOT_A_DEV  PARTUUID=$PU_A  ($(fslabel "$SLOT_A_DEV"))"
info "slot B       : $SLOT_B_DEV  PARTUUID=$PU_B  ($(fslabel "$SLOT_B_DEV"))"
info "running from : $RUNNING_DEV (slot ${RUNNING_SLOT})"
info "next boot    : $CURRENT_ROOT${CURRENT_SLOT:+  (slot $CURRENT_SLOT)}"

if [[ -z "$CURRENT_SLOT" ]]; then
	warn "cmdline.txt does not point at either slot on this disk."
	warn "A card that has never been switched may still carry a label, or a"
	warn "PARTUUID inherited from the image it was built from."
	warn "Set it explicitly once:  sudo $0 --to a"
	((STATUS_ONLY)) && exit 0
	[[ -n "$TARGET" ]] || die "refusing to guess which slot you meant"
fi

((STATUS_ONLY)) && exit 0

# --------------------------------------------------------- pick a target --
if [[ -n "$TARGET" ]]; then
	case "$TARGET" in
	a | A) TARGET="a" ;;
	b | B) TARGET="b" ;;
	*) die "--to takes 'a' or 'b', not '$TARGET'" ;;
	esac
else
	[[ "$CURRENT_SLOT" == "a" ]] && TARGET="b" || TARGET="a"
fi

if [[ "$TARGET" == "a" ]]; then
	TARGET_DEV="$SLOT_A_DEV"
	TARGET_PU="$PU_A"
else
	TARGET_DEV="$SLOT_B_DEV"
	TARGET_PU="$PU_B"
fi

if [[ "$TARGET" == "$CURRENT_SLOT" ]]; then
	ok "Already set to boot slot $TARGET ($TARGET_DEV) — nothing to do."
	((REBOOT)) && info "not rebooting: cmdline.txt was already correct"
	exit 0
fi

# ------------------------------------------------- is the target usable? --
# prepare-ab-card.sh leaves slot B deliberately empty, so switching to it before
# it has been populated leaves the Pi with no root filesystem and no console to
# fix it from. Check before writing rather than after the reboot.
log "Checking slot $TARGET ($TARGET_DEV)"

CHECK_DIR=""
TEMP_MNT=""
EXISTING_MP="$(findmnt -nro TARGET --source "$TARGET_DEV" 2>/dev/null | head -1 || true)"
if [[ -n "$EXISTING_MP" ]]; then
	CHECK_DIR="$EXISTING_MP"
	info "already mounted at $CHECK_DIR"
else
	TEMP_MNT="$(mktemp -d)"
	if mount -o ro "$TARGET_DEV" "$TEMP_MNT" 2>/dev/null; then
		CHECK_DIR="$TEMP_MNT"
	else
		rmdir "$TEMP_MNT"
		TEMP_MNT=""
		warn "could not mount $TARGET_DEV read-only to inspect it"
	fi
fi

release_check_dir() {
	if [[ -n "$TEMP_MNT" ]]; then
		umount "$TEMP_MNT" 2>/dev/null || true
		rmdir "$TEMP_MNT" 2>/dev/null || true
		TEMP_MNT=""
	fi
}

if [[ -n "$CHECK_DIR" ]]; then
	if [[ ! -e "$CHECK_DIR/sbin/init" && ! -e "$CHECK_DIR/usr/lib/systemd/systemd" ]]; then
		warn "slot $TARGET has no /sbin/init — it looks empty or incomplete."
		warn "Booting it would leave the Pi unable to start userspace."
		release_check_dir
		((FORCE)) || exit 1
		warn "--force given, continuing anyway"
	else
		ok "slot $TARGET contains a root filesystem"

		FSTAB_ROOT="$(awk '$2 == "/" && $1 !~ /^#/ {print $1}' \
			"$CHECK_DIR/etc/fstab" 2>/dev/null || true)"
		FSTAB_BOOT="$(awk '$2 == "/boot/firmware" && $1 !~ /^#/ {print $1}' \
			"$CHECK_DIR/etc/fstab" 2>/dev/null || true)"
		BOOT_SRC="$(findmnt -nro SOURCE /boot/firmware 2>/dev/null || true)"
		BOOT_PU="$(partuuid "$BOOT_SRC")"

		[[ "$FSTAB_ROOT" == "PARTUUID=$TARGET_PU" ]] ||
			warn "slot $TARGET fstab has '/' as '${FSTAB_ROOT:-missing}', expected PARTUUID=$TARGET_PU"

		# The classic silent failure: / mounts, then systemd blocks forever on a
		# /boot/firmware device that does not exist on this card.
		if [[ -n "$FSTAB_BOOT" && -n "$BOOT_PU" &&
			"$FSTAB_BOOT" != "PARTUUID=$BOOT_PU" && "$FSTAB_BOOT" != "LABEL=bootfs" ]]; then
			warn "slot $TARGET mounts /boot/firmware from '$FSTAB_BOOT',"
			warn "but this card's boot partition is PARTUUID=$BOOT_PU."
			warn "Boot would mount / and then hang waiting for a device that is"
			warn "not on this card — with 'quiet' set, completely silently."
			release_check_dir
			((FORCE)) || die "fix that fstab entry first (--force overrides)"
			warn "--force given, continuing anyway"
		fi
	fi
	release_check_dir
fi

# ---------------------------------------------------------------- switch --
log "Switching to slot $TARGET (PARTUUID=$TARGET_PU)"

cp -a "$CMDLINE" "${CMDLINE}.bak"
info "backup : ${CMDLINE}.bak"

sed -i "s#root=[^[:space:]]*#root=PARTUUID=${TARGET_PU}#" "$CMDLINE"

# ---------------------------------------------------------------- verify --
NEW_ROOT="$(grep -o 'root=[^[:space:]]*' "$CMDLINE" || true)"
NEW_LINES="$(grep -c '' "$CMDLINE")"

if [[ "$NEW_ROOT" != "root=PARTUUID=$TARGET_PU" ]]; then
	cp -a "${CMDLINE}.bak" "$CMDLINE"
	sync
	die "edit did not take (got '${NEW_ROOT:-nothing}') — restored from backup"
fi
((NEW_LINES == 1)) || warn "$CMDLINE is now $NEW_LINES lines; it should be exactly one"

# cmdline.txt lives on the FAT boot partition, which the firmware reads before
# Linux starts — make sure it is on the card before anyone reboots.
sync
mountpoint -q "$(dirname "$CMDLINE")" &&
	mount -o remount "$(dirname "$CMDLINE")" 2>/dev/null || true
sync

log "Done"
info "$(cat "$CMDLINE")"
ok "Next boot: slot $TARGET  ($TARGET_DEV, PARTUUID=$TARGET_PU)"

# Only reached once the new root= has been written and read back. Every check
# and every failure above either dies or exits, so there is no path to here
# that leaves cmdline.txt unchanged.
if ((REBOOT)); then
	log "Rebooting into slot $TARGET"
	for i in 5 4 3 2 1; do
		printf '\r    rebooting in %ds  (Ctrl-C to cancel) ' "$i"
		sleep 1
	done
	printf '\n'
	reboot
else
	info "Reboot to apply:  sudo reboot"
fi
