#!/bin/bash

# This script is called from our systemd unit file to mount or unmount a USB drive.

usage()
{
	echo "Usage: $0 {add|remove} device_name (e.g. sda1)"
	exit 1
}

if [[ $# -ne 2 ]]; then
	usage
fi

ACTION=$1
DEVBASE=$2
DEVICE="/dev/${DEVBASE}"

# See if this drive is already mounted, and if so where
MOUNT_POINT=$(/bin/mount | /bin/grep ${DEVICE} | /usr/bin/awk '{ print $3 }')

# Mount the USB drive
do_mount()
{
	# Skip if already mounted. Should not happen, therefore the warning
	if [[ -n ${MOUNT_POINT} ]]; then
		echo "Warning: ${DEVICE} is already mounted at ${MOUNT_POINT}"
		exit 1
	fi

	# Get info for this drive: $ID_FS_TYPE. sed's to avoid space issues
	eval $(/sbin/blkid -o udev ${DEVICE} | sed 's/=/="/' | sed 's/$/"/')

	# Figure out mount point to use
	MOUNT_POINT="/media/${DEVBASE}"

	echo "Mount point for ${DEVICE}: ${MOUNT_POINT}"

	/bin/mkdir -p ${MOUNT_POINT}

	# Global mount options
	OPTS="rw,relatime"

	# File system type specific mount options
	if [[ ${ID_FS_TYPE} == "vfat" ]]; then
		OPTS+=",users,gid=100,umask=000,shortname=mixed,utf8=1,flush"
	fi

	if ! /bin/mount -o ${OPTS} ${DEVICE} ${MOUNT_POINT}; then
		echo "Error mounting ${DEVICE} (status = $?)"
		/bin/rmdir ${MOUNT_POINT}
		exit 1
	fi

	echo "**** Mounted ${DEVICE} at ${MOUNT_POINT} ****"
}

# Unmount the USB drive
do_unmount()
{
	# Skip if not mounted. Should not happen, therefore the warning
	if [[ -z ${MOUNT_POINT} ]]; then
		echo "Warning: ${DEVICE} is not mounted"
	else
		/bin/umount -l ${DEVICE}
		echo "**** Unmounted ${DEVICE} ****"
	fi

	# Delete all empty dirs in /media that aren't being used as mount points.
	# This is kind of overkill, but if the drive was unmounted prior to removal
	# we no longer know its mount point, and we don't want to leave it orphaned...
	for f in /media/* ; do
		if [[ -n $(/usr/bin/find "$f" -maxdepth 0 -type d -empty) ]]; then
			if ! /bin/grep -q " $f " /etc/mtab; then
				echo "**** Removing mount point $f"
				/bin/rmdir "$f"
			fi
		fi
	done
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
