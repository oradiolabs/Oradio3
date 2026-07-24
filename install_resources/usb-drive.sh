#!/bin/bash
#
# Handles USB drive mount/unmount for devices labelled ORADIO.
# Invoked by systemd via usb-drive@.service, with the action ("add" or "remove")
# passed as the first argument by the %i template specifier.

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

ACTION="${1:-}"
PARTITION="/dev/disk/by-label/ORADIO"	# Stable symlink; survives device renumbering
MOUNTPOINT="/media/oradio"				# Location where USB is mounted
MONITOR="/run/usb_present"				# RAM-based flag file; present = mounted, absent = unmounted
LOCK="/run/usb_mount.lock"				# Prevents concurrent runs from duplicate udev events

# Logging helper: prefixes every message with a timestamp
log() {
	echo "$(date '+%F %T') $*"
}

# Acquire an exclusive lock for the duration of this script.
# Prevents a rapid remove/add or duplicate udev event from running two instances at once.
exec 9>"$LOCK"
flock -x 9

case "$ACTION" in

	# Runs when a USB device labelled ORADIO is inserted and detected by udev
	add)
		# Guard: partition symlink must exist before attempting to mount
		if [ ! -b "$PARTITION" ]; then
			log "Warning: ${PARTITION} not found"
			exit 1
		fi

		# Guard: skip if already mounted (should not happen; log as warning if it does)
		if mountpoint -q "$MOUNTPOINT"; then
			log "Warning: '${PARTITION}' already mounted at ${MOUNTPOINT}"
			exit 1
		fi

		# Ensure mount point directory exists
		mkdir -p "$MOUNTPOINT"

		# Mount options chosen to reduce risk of data loss on FAT volumes:
		#   rw			read/write mode
		#   users		allows non-root users to unmount
		#   uid=0		files owned by root
		#   gid=100		files belong to the "users" group
		#   fmask=111	file permissions: read+write (no execute)
		#   dmask=000	directory permissions: read+write+execute
		#   utf8=1		UTF-8 filename encoding
		#   noatime		suppress access-time updates on files
		#   nodiratime	suppress access-time updates on directories
		#   flush		write FAT metadata promptly (reduces corruption window)
		#   sync		write file data immediately (no deferred writeback)
		OPTS="rw,users,uid=0,gid=100,fmask=111,dmask=000,utf8=1,noatime,nodiratime,flush,sync"

		# Attempt mount; capture exit status before the if-branch resets $?
		if ! mount -t vfat -o "$OPTS" "$PARTITION" "$MOUNTPOINT" 2>/tmp/mount-error.txt; then
			log "Error: Mounting '$PARTITION' failed: $(cat /tmp/mount-error.txt)"
			exit 1
		fi

		# Create flag triggering the Python watchdog
		touch "$MONITOR"

		log "Success: Mounted '$PARTITION' at '$MOUNTPOINT'"
		;;

	# Runs when a USB device labelled ORADIO is physically removed
	remove)
		# Guard: nothing to do if already unmounted
		# (can happen if a prior remove event already cleaned up)
		if ! mountpoint -q "$MOUNTPOINT"; then
			log "Info: '$MOUNTPOINT' already unmounted"
			exit 0
		fi

		# Try clean unmount first, lazy unmount if clean unmount fails, force success if lazy unmount fails
		umount "$MOUNTPOINT" || umount -l "$MOUNTPOINT" || true

		# Verify unmount actually happened
		if mountpoint -q "$MOUNTPOINT"; then
			log "Error: Failed to unmount '$MOUNTPOINT'"
			exit 1
		fi

		# Now it is safe to clean up
		rm -f "$MONITOR"
		rmdir "$MOUNTPOINT"

		log "Success: Unmounted '$MOUNTPOINT'"
		;;

	# Catch unexpected or missing action argument
	*)
		log "Error: Unknown action: '$ACTION'"
		exit 1
		;;
esac
