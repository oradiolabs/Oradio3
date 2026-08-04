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
#   2  this exact package has not already failed a trial boot on this unit
#
# Only then does it hand over to install-swu.sh, which installs into the
# inactive slot and starts a trial boot of it.
#
# Every trigger shares this decision, whether the package arrives on a USB drive
# or is fetched over the network.
#
set -euo pipefail

# Two ways in, so that no trigger needs privileges it would not otherwise have.
#
# 1. Started directly by the udev mount handler after it mounts the drive. No
#    request is written: this service looks for a package on the drive itself,
#    using /run/usb_present only to confirm a drive is there.
# 2. /run/swu_present — written by a trigger that already runs as root, such as
#    an internet downloader. Its contents are the path of the package. On
#    tmpfs, so a reboot cannot replay a stale marker.
USB_MARKER="/run/usb_present"
SWU_MARKER="/run/swu_present"

# Where the OS auto-mounts the ORADIO drive, and what a package looks like.
USB_MOUNT_POINT="/media/oradio"
SWU_GLOB="*.swu"
LOCK_FILE="/run/oradio3-update.lock"

# Read only — this script keeps no state of its own. The file belongs to
# ab-boot-trial.sh, which is the only thing that can observe a slot failing to
# boot; it records failed_version when a trial does not survive, and that is
# what stops a still-inserted drive re-installing a package already proved
# unbootable.
#
# It lives on the boot partition because it must survive a slot switch: after
# switching, /var belongs to the newly installed slot.
TRIAL_STATE="/boot/firmware/oradio3-boot.state"

# Where the running software records its own identity. Written by the project,
# carried through updates because it is in build-swu.sh's KEEP_LOGS.
VERSION_FILE="/var/log/oradio_sw_version.log"

# The public half of the build host's signing key. Belongs on the Pi, never on
# the medium the package arrives on.
CERT="/etc/oradio3/update-signing.cert.pem"

# The rest of the toolkit. Installed together, so their location is known rather
# than searched for.
INSTALL_SCRIPT="/usr/local/sbin/install-swu.sh"

##### logging #############################################
# stdout and stderr are redirected to the Oradio log file by
# oradio3-update.service, so this output does not appear in `journalctl -u`.
#
# A refused or failed update never switches slots, so the log that matters is
# the one on the slot still running — which is the slot writing this.
log() { printf '%s oradio3-update: %s\n' "$(date -Is)" "$*"; }
die() {
	printf '%s oradio3-update: ERROR: %s\n' "$(date -Is)" "$*" >&2
	exit 1
}

##### helpers #############################################

# The package version, read from sw-description without reading the package.
# sw-description is the first member of the cpio archive, so a short prefix is
# enough — worth caring about when the package sits on a slow USB stick.
#
# Reports which step failed rather than a single "no version": the package being
# unreadable and the package having no version are different problems, and so is
# cpio simply not being installed.
package_version() {
	local pkg="$1" desc ver
	desc="$(head -c 65536 -- "$pkg" 2>/dev/null |
		cpio -i --to-stdout sw-description 2>/dev/null || true)"

	# Diagnostics go to stderr: this function is called in $( ), so anything on
	# stdout would be captured AS the version.
	if [[ -z "$desc" ]]; then
		log "could not read sw-description from $(basename "$pkg")" >&2
		log "  it should be the first member of the .swu's cpio archive" >&2
		log "  check the file is a complete package:  cpio -it < '$pkg' | head" >&2
		return 1
	fi

	ver="$(sed -n 's/.*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$desc" | head -1)"
	if [[ -z "$ver" ]]; then
		log "sw-description has no version field. It reads:" >&2
		while IFS= read -r l; do log "    $l" >&2; done <<<"$desc"
		return 1
	fi
	printf '%s' "$ver"
}

# The identity of the running software: the SHA-256 of its version file, taken
# as-is. build-swu.sh hashes the same file inside the rootfs it packages, and
# puts the result in the signed sw-description.
#
# Neither side parses the file, so the project owns its format entirely — keys
# can be added, renamed or restructured without breaking the comparison. A
# rebuild changes the file, changes the hash, and the package reads as new.
version_id() { # version_id <path>
	[[ -r "$1" ]] || return 1
	sha256sum "$1" | cut -c1-16
}

running_version() { version_id "$VERSION_FILE"; }

# Has this exact version already failed a trial boot? One demonstration is
# enough: a package that installed, rebooted and did not come up will do the
# same again, and each attempt costs an install plus a trial timeout.
version_failed_trial() { # version_failed_trial <version>
	[[ -r "$TRIAL_STATE" ]] || return 1
	local failed
	failed="$(sed -n 's/^failed_version=//p' "$TRIAL_STATE" | tail -1)"
	[[ -n "$failed" && "$failed" == "$1" ]]
}

##### one at a time #######################################
# systemd will not run this unit concurrently, but a hand invocation could.
exec 9>"$LOCK_FILE"
flock -n 9 || die "another update is already running"

[[ $EUID -eq 0 ]] || die "must run as root"

# cpio reads the version out of the package, sha256sum identifies the running
# build. Without them every package looks unreadable, which is easy to mistake
# for a bad package.
for t in cpio sha256sum; do
	command -v "$t" >/dev/null || die "missing tool: $t (apt install $t)"
done

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

# Clear the marker even when the USB branch fired: a leftover from an aborted
# run would otherwise be picked up as a request on the next insertion.
rm -f "$SWU_MARKER"

[[ -f "$SWU" ]] || die "package not found: $SWU (was the medium removed?)"
[[ -f "$CERT" ]] || die "signing certificate not found: $CERT"

[[ -f "$INSTALL_SCRIPT" ]] || die "$INSTALL_SCRIPT not found — is the toolkit installed?"

##### decide ##############################################
PKG_VER="$(package_version "$SWU" || true)"
[[ -n "$PKG_VER" ]] || die "could not read a version from $SWU — not a valid package?"

RUN_VER="$(running_version || true)"
log "package version: $PKG_VER"
log "running version: ${RUN_VER:-unknown}"
# Show the file itself, since the hash means nothing to a human reading a log.
if [[ -r "$VERSION_FILE" ]]; then
	while IFS= read -r line; do
		[[ -n "${line//[[:space:]]/}" ]] && log "  running build : $line"
	done <"$VERSION_FILE"
fi

# Without a running version there is nothing to compare against, so every
# trigger looks like a new package and the drive would reinstall on every
# insertion. The trial guard still catches a package that cannot boot, but a
# working package would be installed again and again.
if [[ -z "$RUN_VER" ]]; then
	log "WARNING: cannot read a version from $VERSION_FILE"
	log "WARNING: every trigger will look like a new package, so this will"
	log "WARNING: reinstall on every insertion until the file is readable."
fi

# Already running it. This is what makes re-inserting the same drive a no-op,
# without writing anything to the drive — which matters because the drive may be
# read-only, and because one drive should be able to update many units.
if [[ -n "$RUN_VER" && "$PKG_VER" == "$RUN_VER" ]]; then
	log "already running $PKG_VER — nothing to do"
	exit 0
fi

# Has this exact package already failed a trial boot? This is evidence, not a
# guess: ab-boot-trial.sh recorded it after watching the slot fail to come up.
if version_failed_trial "$PKG_VER"; then
	log "REFUSING: $PKG_VER has already failed a trial boot on this unit"
	log "It installed and then did not come up, so the Pi rolled back."
	log "Fix the package. To try this one again anyway:"
	log "  sudo sed -i '/^failed_version=/d' $TRIAL_STATE"
	exit 1
fi

##### install #############################################
log "installing $PKG_VER"
log "handing over to $INSTALL_SCRIPT; it starts a trial boot of the other slot"

# install-swu.sh reboots on success, so nothing after this line runs then. On
# failure it returns non-zero and the running slot is untouched — retrying then
# costs nothing, so re-inserting the drive is all it takes and no attempt is
# recorded. A package that installs but cannot boot is caught separately, by the
# trial-failure check above.
bash "$INSTALL_SCRIPT" -i "$SWU" -k "$CERT"

die "install-swu.sh returned without rebooting — the update did not complete"
