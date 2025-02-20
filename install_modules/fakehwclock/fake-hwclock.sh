#!/bin/sh

# Get the saved time (or empty) and clock time, in RFC399 format
# This format sorts lexicographically

CLOCKFILE="/storage/.fake-hwclock.data"
CTIME=$(date +%s)

# Ensure directory for storing hwclock info does exists
FILEDIR=$(/usr/bin/dirname "${CLOCKFILE}")
if test -f "${FILEDIR}" ; then
	/usr/bin/mkdir -p "${FILEDIR}"
fi

# Load last saved time, 0 if never saved
if test -f "${CLOCKFILE}" ; then
    FTIME=$(cat "${CLOCKFILE}")
else
    FTIME=0
fi

# if the file time is in the future, use that
setclock() {
	if test "${FTIME}" -ge "${CTIME}" ; then
		echo "loading saved time ${FTIME} over ${CTIME}"
		date @${FTIME}
	else
		echo "ignoring saved time ${FTIME} over ${CTIME}"
	fi
}

# if current time is greater than what is in the file, save it
saveclock() {
	echo "saving time ${CTIME} over ${FTIME}"
	echo "${CTIME}" > "${CLOCKFILE}"
}

case "$1" in
	load)
		setclock
		;;
	save)
		saveclock
		;;
	tick)
		saveclock
		;;
	*)
		echo "Usage: $0 {load|save|tick}"
		exit 1
		;;
esac
