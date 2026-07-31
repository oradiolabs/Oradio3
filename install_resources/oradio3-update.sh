#!/usr/bin/env bash
#
# oradio3-update.sh — install a software update package, once, safely.
#
# RUNS ON THE PI, as root, from oradio3-update.service. Not run by hand
# (though it is safe to: it declines anything it should not install).
#
# A drive carrying a .swu, or a trigger that names one, brings this service up.
# It then decides whether the package is worth installing:
#
#   1  the package version differs from the running version
#   2  the same package has not already failed too many times
#
# Only then does it hand over to install-swu.sh, which installs into the
# inactive slot, moves the boot pointer and reboots.
#
# Every trigger shares this decision logic and the boot-loop guard, whether the
# package arrives on a USB drive or is fetched over the network.
#
set -euo pipefail

# Two ways in, so that no trigger needs privileges it would not otherwise have.
#
# 1. /run/usb_present — touched by the udev mount handler after the drive is
#    mounted, removed after it is unmounted. Nothing has to be written by
#    anyone: this service looks for a package on the drive itself.
# 2. /run/swu_present — written by a trigger that already runs as root, such as
#    an internet downloader. Its contents are the path of the package. On
#    tmpfs, so a reboot cannot replay a stale marker.
USB_MARKER="/run/usb_present"
SWU_MARKER="/run/swu_present"

# Where the OS auto-mounts the ORADIO drive, and what a package looks like.
USB_MOUNT_POINT="/media/oradio"
SWU_GLOB="*.swu"
LOCK_FILE="/run/oradio3-update.lock"

# Attempt state must survive a slot switch, so it cannot live on the rootfs:
# after switching, /var belongs to the newly installed slot and anything written
# here would be gone. The boot partition is shared by both slots.
STATE_FILE="/boot/firmware/oradio3-update.state"
MAX_ATTEMPTS=2

# Where the running software records its own identity. Written by the project,
# carried through updates because it is in build-swu.sh's KEEP_LOGS.
VERSION_FILE="/var/log/oradio_sw_version.log"

# The public half of the build host's signing key. Belongs on the Pi, never on
# the medium the package arrives on.
CERT="/etc/oradio3/update-signing.cert.pem"

##### logging #############################################
# stdout goes to the journal. A refused or failed update does not switch slots,
# so the journal you need is the one on the slot you are still running.
log() { printf '%s oradio3-update: %s\n' "$(date -Is)" "$*"; }
die() {
	printf '%s oradio3-update: ERROR: %s\n' "$(date -Is)" "$*" >&2
	exit 1
}

##### helpers #############################################

# The package version, read from sw-description without reading the package.
# sw-description is the first member of the cpio archive, so a short prefix is
# enough — worth caring about when the package sits on a slow USB stick.
package_version() {
	local pkg="$1" desc
	desc="$(head -c 65536 -- "$pkg" 2>/dev/null |
		cpio -i --to-stdout sw-description 2>/dev/null || true)"
	[[ -n "$desc" ]] || return 1
	sed -n 's/.*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$desc" | head -1
}

# The identity of the software in a rootfs, read from its version file:
#   {
#       "dtstamp": "YYYY-MM-DD-hh-mm-ss",
#       "gitinfo":  "<string>"
#   }
# "dtstamp" is the identity, because it is unique per build and sorts.
# The commit alone is not: rebuilding a dirty tree gives the same hash.
#
# build-swu.sh reads the same field out of the rootfs it packages, so the two
# sides of the comparison come from the same place by construction. Change this
# function and its twin in build-swu.sh together, or the comparison stops
# meaning anything.
version_from_file() { # version_from_file <path>
	local f="$1" v
	[[ -r "$f" ]] || return 1
	v="$(sed -n 's/.*"dtstamp"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -1)"
	if [[ -z "$v" ]]; then
		# No dtstamp: fall back to the commit, which is better than nothing
		v="$(sed -n 's/.*"gitinfo".*@[[:space:]]*\([0-9a-f]\{7,\}\).*/\1/p' "$f" | head -1)"
	fi
	[[ -n "$v" ]] || return 1
	printf '%s' "$v"
}

gitinfo_from_file() { # gitinfo_from_file <path> — for the log only
	[[ -r "$1" ]] || return 1
	sed -n 's/.*"gitinfo"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -1
}

running_version() { version_from_file "$VERSION_FILE"; }

read_state() {
	STATE_VERSION=""
	STATE_COUNT=0
	[[ -r "$STATE_FILE" ]] || return 0
	STATE_VERSION="$(sed -n 's/^attempted=//p' "$STATE_FILE" | tail -1)"
	STATE_COUNT="$(sed -n 's/^count=//p' "$STATE_FILE" | tail -1)"
	[[ "$STATE_COUNT" =~ ^[0-9]+$ ]] || STATE_COUNT=0
	return 0
}

write_state() { # write_state <version> <count>
	# Attempt the write rather than testing permissions: running as root, -w
	# reports success on a directory whose mode forbids it, and the mount being
	# read-only is the case that actually matters.
	#
	# This fails closed. Without a recorded attempt the boot-loop guard cannot
	# count, and a package that installs but never comes up would be retried on
	# every boot for as long as the medium stays inserted. Declining to install
	# is recoverable; an unattended reboot loop on a deployed unit is not.
	if ! printf 'attempted=%s\ncount=%s\nupdated=%s\n' \
		"$1" "$2" "$(date -Is)" >"$STATE_FILE" 2>/dev/null; then
		die "cannot write $STATE_FILE, so the boot-loop guard would be inoperative.
       Refusing to install. Check that $(dirname "$STATE_FILE") is mounted read-write."
	fi
	# vfat, plus a reboot moments away: get it onto the card now
	sync
}

clear_state() {
	[[ -e "$STATE_FILE" ]] || return 0
	rm -f "$STATE_FILE"
	sync
}

find_installer() {
	local self_dir c
	self_dir="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
	for c in "$self_dir/install-swu.sh" /usr/local/sbin/install-swu.sh \
		/usr/local/bin/install-swu.sh ./install-swu.sh; do
		[[ -f "$c" ]] && printf '%s' "$c" && return 0
	done
	return 1
}

##### one at a time #######################################
# systemd will not run this unit concurrently, but a hand invocation could.
exec 9>"$LOCK_FILE"
flock -n 9 || die "another update is already running"

[[ $EUID -eq 0 ]] || die "must run as root"

##### find something to install ###########################
SWU=""

# An explicit marker wins: whoever wrote it knows exactly which package it
# means. Consume it immediately — the path unit fires on the file existing, so
# leaving it would re-trigger, and a crash below must not replay it.
if [[ -s "$SWU_MARKER" ]]; then
	SWU="$(head -1 "$SWU_MARKER" | tr -d '[:space:]')"
	rm -f "$SWU_MARKER"
	log "named by $SWU_MARKER: $SWU"
	[[ -n "$SWU" ]] || die "$SWU_MARKER named nothing"

# Otherwise this was the USB marker, so look on the drive. Doing the looking
# here rather than in the trigger is what keeps the trigger unprivileged: udev
# already creates the marker as root, and nothing else has to write anything.
elif [[ -e "$USB_MARKER" ]]; then
	# usb-drive.sh touches the marker only after mount(8) has returned success,
	# so the drive is mounted by the time this runs. If it is not, the drive was
	# pulled in between or the mount handler failed part-way — say which, rather
	# than reporting it as nothing to do.
	mountpoint -q "$USB_MOUNT_POINT" || {
		log "$USB_MARKER is set but $USB_MOUNT_POINT is not mounted — drive removed?"
		exit 0
	}

	mapfile -t found < <(find "$USB_MOUNT_POINT" -maxdepth 1 -name "$SWU_GLOB" -type f | sort)
	if ((${#found[@]} == 0)); then
		log "drive mounted, no $SWU_GLOB on it — nothing to do"
		exit 0
	fi
	# More than one is ambiguous rather than wrong: take the highest name, which
	# for date-stamped packages is the newest.
	if ((${#found[@]} > 1)); then
		log "WARNING: ${#found[@]} packages on the drive, using the last by name"
	fi
	SWU="${found[-1]}"
	log "found on $USB_MOUNT_POINT: $SWU"

else
	log "no marker set and no drive mounted"
	exit 0
fi

rm -f "$SWU_MARKER"

[[ -f "$SWU" ]] || die "package not found: $SWU (was the medium removed?)"
[[ -f "$CERT" ]] || die "signing certificate not found: $CERT"

INSTALLER="$(find_installer)" || die "install-swu.sh not found"

##### decide ##############################################
PKG_VER="$(package_version "$SWU" || true)"
[[ -n "$PKG_VER" ]] || die "could not read a version from $SWU — not a valid package?"

RUN_VER="$(running_version || true)"
log "package version: $PKG_VER"
log "running version: ${RUN_VER:-unknown}"
RUN_GIT="$(gitinfo_from_file "$VERSION_FILE" || true)"
[[ -n "$RUN_GIT" ]] && log "running build   : $RUN_GIT"

read_state

# A previous attempt that reached the version it was aiming for succeeded, so
# the counter has done its job.
if [[ -n "$STATE_VERSION" && -n "$RUN_VER" && "$STATE_VERSION" == "$RUN_VER" ]]; then
	log "previous attempt at $STATE_VERSION succeeded; clearing attempt state"
	clear_state
	read_state
fi

# Already running it. This is the check that makes re-inserting the same stick a
# no-op, without writing anything to the stick — which matters because the stick
# may be read-only, and because one stick should be able to update many units.
if [[ -n "$RUN_VER" && "$PKG_VER" == "$RUN_VER" ]]; then
	log "already running $PKG_VER — nothing to do"
	exit 0
fi

# Boot-loop guard. Without this, a package that installs but does not come up
# would be retried on every boot for as long as the medium stays inserted.
if [[ "$STATE_VERSION" == "$PKG_VER" ]] && ((STATE_COUNT >= MAX_ATTEMPTS)); then
	log "REFUSING: $PKG_VER has already been attempted $STATE_COUNT times"
	log "Remove the package, or clear $STATE_FILE to try again."
	exit 1
fi

##### install #############################################
NEXT_COUNT=$((STATE_COUNT + 1))
[[ "$STATE_VERSION" == "$PKG_VER" ]] || NEXT_COUNT=1
write_state "$PKG_VER" "$NEXT_COUNT"

log "installing $PKG_VER (attempt $NEXT_COUNT of $MAX_ATTEMPTS)"
log "handing over to $INSTALLER; it switches slots and reboots on success"

# install-swu.sh reboots on success, so nothing after this line runs then. On
# failure it returns non-zero, the running slot is untouched, and the attempt
# counter above is what stops this repeating forever.
bash "$INSTALLER" -i "$SWU" -k "$CERT"

die "install-swu.sh returned without rebooting — the update did not complete"
