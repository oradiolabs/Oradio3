#!/usr/bin/bash

# This script is called from our systemd unit file to mount or unmount a USB partition.

# Basic usage check
if [[ $# -ne 2 ]]; then
	echo "Usage: $0 {add|remove} device_name (e.g. sda1)"
	exit 1
fi

ACTION=$1						# add | remove
DEVBASE=$2						# sd[a-z][1-9]
VALIDLABEL="ORADIO"				# Partition will not be mounted if label does not match VALIDLABEL
PARTITION="/dev/${DEVBASE}"		# Location of the USB partition to mount

MOUNT_POINT="/media/oradio"		# Location where USB is mounted
MONITOR="/media/usb_ready"		# File used to monitor if USB is mounted/unmounted

# Mount the USB partition
do_mount()
{
	# Skip if device not found
	if [ ! -b $PARTITION ]; then
		echo "$(date): Warning: ${PARTITION} not found"
		exit 1
	fi

	# Skip if already mounted. Should not happen, therefore the warning
	if /usr/bin/mountpoint $MOUNT_POINT > /dev/null 2>&1; then
		echo "$(date): Warning: ${PARTITION} is already mounted at ${MOUNT_POINT}"
		exit 1
	fi

	# Get LABEL for this partition. sed's to avoid space issues
	LABEL=$(/sbin/blkid -o udev $PARTITION | grep 'LABEL.*' | sed -n 's/.*=\(.*\).*/\1/p' | sort -u)

	# Skip if partition label is not 
	if [ "$LABEL" != $VALIDLABEL ]; then
		echo "$(date): Warning: Label '$LABEL' of '$PARTITION' does not match '$VALIDLABEL'"
		exit 1
	fi

	# Create mount point
	/usr/bin/mkdir -p $MOUNT_POINT

	# File system type specific mount options
	OPTS="rw,relatime,users,gid=100,umask=000,shortname=mixed,utf8=1,flush"

	# Try to mount the partition
	if ! /bin/mount -o $OPTS $PARTITION $MOUNT_POINT; then
		echo "$(date): Error: Mounting '$PARTITION' (status = $?)"
		# Cleanup mount point
		/usr/bin/rm -f $MOUNT_POINT
		exit 1
	fi

	# Mount succesful: Create the flag triggering the Python watchdog 
	/usr/bin/touch $MONITOR

	echo "$(date): Success: Mounted '$PARTITION' at '$MOUNT_POINT'"
}

# Unmount the USB partition
do_unmount()
{
	# Skip if not mounted. Should not happen, therefore the warning
	if [ ! -d $MOUNT_POINT ]; then
		echo "$(date): Warning: '$PARTITION' is not mounted"
		exit 1
	fi

	# Try to unmount the partition
	if ! /usr/bin/umount -l $PARTITION; then
		echo "$(date): Error: Unmounting '$PARTITION' (status = $?)"
		exit 1
	fi

	# Delete mount point
	/usr/bin/rmdir $MOUNT_POINT

	# Delete the flag triggering the Python watchdog 
	/usr/bin/rm -f $MONITOR

	echo "$(date): Success: Unmounted '$PARTITION'"
}

case "${ACTION}" in
	add)
		do_mount
		;;
	remove)
		do_unmount
		;;
	*)
		usage
		;;
esac
