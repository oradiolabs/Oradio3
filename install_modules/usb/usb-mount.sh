#!/bin/bash

# This script is called from our systemd unit file to mount or unmount a USB partition.

usage()
{
	echo "Usage: $0 {add|remove} device_name (e.g. sda1)"
	exit 1
}

if [[ $# -ne 2 ]]; then
	usage
fi

ACTION=$1						# add | remove
DEVBASE=$2						# sd[a-z][1-9]
VALIDLABEL="ORADIO"				# Partition will not be mounted if label does not match VALIDLABEL
PARTITION="/dev/${DEVBASE}"		# Location of the USB partition to mount
MOUNT_POINT="/media/oradio"
MONITOR="/media/usb_ready"

# Mount the USB partition
do_mount()
{
	# Skip if already mounted. Should not happen, therefore the warning
	if test -f $MOUNT_POINT; then
		echo "Warning: ${PARTITION} is already mounted at ${MOUNT_POINT}"
		exit 1
	fi

	# Get LABEL for this partition. sed's to avoid space issues
	LABEL=$(/sbin/blkid -o udev ${PARTITION} | grep 'LABEL.*' | sed -n 's/.*=\(.*\).*/\1/p' | sort -u)

	# Skip if partition label is not 
	if [ "$LABEL" != $VALIDLABEL ]; then
		echo "Warning: Label of '${PARTITION}' does not match '${LABEL}'"
		exit 1
	fi

	# Create mount point
	/bin/mkdir -p ${MOUNT_POINT}

	# File system type specific mount options
	OPTS="rw,relatime,users,gid=100,umask=000,shortname=mixed,utf8=1,flush"

	# Try to mount the partition
	if ! /bin/mount -o ${OPTS} ${PARTITION} ${MOUNT_POINT}; then
		echo "Error: Mounting ${PARTITION} (status = $?)"

		# Cleanup mount point
		/bin/rmdir ${MOUNT_POINT}

		exit 1
	fi

	# Mount succesful: Create the flag triggering the Python watchdog 
	/bin/touch ${MONITOR}

	echo "**** Mounted '${PARTITION}' at '${MOUNT_POINT}' ****"
}

# Unmount the USB partition
do_unmount()
{
	# Skip if not mounted. Should not happen, therefore the warning
	if [ -z ${MOUNT_POINT} ]; then
		echo "Warning: ${PARTITION} is not mounted"
		exit 1
	fi

	# Try to unmount the partition
	if ! /bin/umount -l ${PARTITION}; then
		echo "Error: Unmounting ${PARTITION} (status = $?)"
		exit 1
	fi

	# Delete mount point
	/bin/rmdir ${MOUNT_POINT}

	# Delete the flag triggering the Python watchdog 
	/bin/rm ${MONITOR}

	echo "**** Unmounted ${PARTITION} ****"
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
