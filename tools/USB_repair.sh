#!/bin/bash
#
# USB_repair.sh - check and repair the FAT32 USB stick used by Oradio,
#                 log what happened, then reboot.
#
# Lives at:  /home/pi/Oradio3/tools/USB_repair.sh   (mode 664, pi:pi)
# Run with:  cd /home/pi/Oradio3/tools && bash ./USB_repair.sh
#
# Runs as pi, not root. Each privileged step calls sudo on its own, so the
# log stays pi:pi and the script itself never needs to be root-owned.
# Requires /etc/sudoers.d/oradio-repair granting NOPASSWD for:
#   umount, fsck.fat, blkid, systemctl reboot
#

set -u

MOUNTPOINT="/media/oradio"
USB_LABEL="ORADIO"      # your stick's label; check with: lsblk -o NAME,LABEL,FSTYPE
LOGFILE="/home/pi/Oradio3/logging/repair.log"
MAXLOGLINES=2000        # keep the log from growing forever
REBOOT_DELAY=5          # seconds to wait before rebooting

# --- logging helper -------------------------------------------------------

mkdir -p "$(dirname "$LOGFILE")"
touch "$LOGFILE" 2>/dev/null

# Written as pi, so it stays pi:pi. A root-owned log left over from an
# earlier run would make every append below fail silently.
if [ ! -w "$LOGFILE" ]; then
    echo "Cannot write $LOGFILE (owned by someone else?). Fix with:" >&2
    echo "  sudo chown pi:pi $LOGFILE" >&2
    exit 1
fi

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" >> "$LOGFILE"
}

log "==================== repair run started ===================="

# --- find the device ------------------------------------------------------

DEV="$(findmnt -n -o SOURCE --target "$MOUNTPOINT" 2>/dev/null)"
WAS_MOUNTED=1

if [ -z "$DEV" ]; then
    # A FAT too corrupt to mount is exactly the case this script exists for,
    # so fall back to finding the stick by its filesystem label.
    WAS_MOUNTED=0
    DEV="$(sudo blkid -L "$USB_LABEL" 2>/dev/null)"
    if [ -z "$DEV" ]; then
        log "ERROR: nothing at $MOUNTPOINT and no device labelled '$USB_LABEL'. No reboot."
        exit 1
    fi
    log "Not mounted; found $DEV by label '$USB_LABEL'"
else
    log "Found $DEV mounted at $MOUNTPOINT"
fi

# --- unmount --------------------------------------------------------------
# fsck.fat on a mounted filesystem can corrupt it, so this only proceeds on a
# positive result: either the unmount worked, or it was never mounted.

sync

if [ "$WAS_MOUNTED" -eq 1 ]; then
    log "Unmounting $DEV ..."
    if ! sudo umount "$DEV" >> "$LOGFILE" 2>&1; then
        log "Normal unmount failed, showing what is holding the device:"
        # No sudo: shows pi's own processes, which is what Oradio runs as.
        fuser -mv "$MOUNTPOINT" >> "$LOGFILE" 2>&1
        log "Retrying with lazy unmount ..."
        if ! sudo umount -l "$DEV" >> "$LOGFILE" 2>&1; then
            log "ERROR: could not unmount $DEV. Rebooting WITHOUT fsck."
            sleep "$REBOOT_DELAY"
            sync
            sudo systemctl reboot
            exit 0
        fi
    fi
    log "Unmounted."
fi

# --- check and repair -----------------------------------------------------

log "Running fsck.fat -a -w -v on $DEV ..."
sudo fsck.fat -a -w -v "$DEV" >> "$LOGFILE" 2>&1
RC=$?
log "fsck.fat finished with exit code $RC"

case "$RC" in
    0) log "RESULT: filesystem clean, nothing to repair." ;;
    1) log "RESULT: errors were found and corrected." ;;
    *) log "RESULT: fsck reported a problem it could not handle (code $RC)." ;;
esac

# --- trim the log ---------------------------------------------------------

if [ "$(wc -l < "$LOGFILE")" -gt "$MAXLOGLINES" ]; then
    tail -n "$MAXLOGLINES" "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

# --- reboot ---------------------------------------------------------------

log "Rebooting in ${REBOOT_DELAY}s."
log "==================== repair run finished ==================="

sleep "$REBOOT_DELAY"
sync
sudo systemctl reboot
