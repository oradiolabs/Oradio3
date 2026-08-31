#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on November 2, 2025
# @author:		 Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:	 Stichting Oradio
# @license:		 GNU General Public License (GPL)
# @organization: Stichting Oradio
# @version:	   	 2
# @email:		 info@stichtingoradio.nl
# @status:		 Development
# @purpose:		 Synchronizes selected SharePoint directories to USB using rclone.
#
#		Usage: USB_sync.sh [-y|--yes] [directory ...]
#		  e.g. USB_sync.sh                    (complete SharePoint tree)
#		       USB_sync.sh Muziek
#		       USB_sync.sh Muziek Speellijsten
#		       USB_sync.sh --yes Muziek/Evergreens
#
#		For unattended runs, combine --yes with ORADIO_SYNC_PW holding the
#		config decryption password. Keep that password in a root-owned 0600
#		file (systemd EnvironmentFile), never in the crontab itself.
#
#		Each argument is a directory path relative to the SharePoint sync root.
#		Names are case-sensitive and must exist on SharePoint; the script aborts
#		if any of them does not. Directories on the USB that are not listed are
#		left untouched — they are neither updated nor deleted. Without arguments
#		the whole tree is mirrored and USB content that is not on SharePoint is
#		deleted, except for the Windows System Volume Information folder.
#
#		The rclone config contains OAuth tokens and is stored AES-256-CBC encrypted
#		in GitHub Releases. It is fetched, decrypted at runtime, and never written to
#		disk in plaintext beyond the /tmp lifetime of this script.
#
#		To update the encrypted config after rclone refreshes its tokens:
#		1. cp /home/pi/.config/rclone/rclone.conf sharepoint.conf
#		2. openssl enc -aes-256-cbc -pbkdf2 -salt -in sharepoint.conf -out rclone.conf.enc -base64
#		3. Upload rclone.conf.enc to GitHub Releases (tag: config)
#		4. shred -u sharepoint.conf

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# Require bash — this script uses bash-specific constructs
if [ -z "${BASH:-}" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

##### Arguments ################################

function usage {
	echo "Usage: $(basename "$0") [-y|--yes] [directory ...]"
	echo
	echo "  Without arguments the complete SharePoint tree is synchronized."
	echo "  With arguments only those directories are synchronized; the rest of"
	echo "  the USB is left untouched. Paths are relative to the SharePoint sync"
	echo "  root and are case-sensitive."
	echo
	echo "  Options:"
	echo "    -y, --yes   Answer all prompts with their default and do not ask"
	echo "                for confirmation. Runs a real sync, not a dry-run,"
	echo "                and skips the optional sector scan"
	echo "    -h, --help  Show this help"
	echo
	echo "  Environment:"
	echo "    ORADIO_SYNC_PW  Decryption password for the rclone config. When"
	echo "                    set, the password prompt is skipped"
	echo
	echo "  Examples:"
	echo "    $(basename "$0")"
	echo "    $(basename "$0") Muziek"
	echo "    $(basename "$0") Muziek Speellijsten"
	echo "    $(basename "$0") --yes Muziek/Evergreens"
}

# Normalize arguments: strip surrounding slashes and reject unusable paths.
# No directories leaves SYNC_DIRS empty, which means "sync everything"
ASSUME_YES=false
SYNC_DIRS=()
for arg in "$@"; do

	case "$arg" in
		-h|--help)
			usage
			exit 0
			;;
		-y|--yes)
			ASSUME_YES=true
			continue
			;;
		-*)
			echo -e "${RED}Unknown option '$arg'${NC}"
			usage
			exit 1
			;;
	esac
	dir="${arg#/}"		# strip leading slash
	dir="${dir%/}"		# strip trailing slash

	if [ -z "$dir" ]; then
		echo -e "${RED}Empty directory argument${NC}"
		usage
		exit 1
	fi

	# Reject traversal and wildcards: patterns are built from these verbatim
	case "$dir" in
		*..* | *"*"* | *"?"* | *"["*)
			echo -e "${RED}Invalid directory '$arg'${NC}"
			exit 1
			;;
	esac

	SYNC_DIRS+=("$dir")
done

# Without a terminal the prompts further down cannot be answered. Fail here,
# before services are stopped, rather than part-way through
if ! $ASSUME_YES && [ ! -t 0 ]; then
	echo -e "${RED}No terminal available to answer prompts${NC}"
	echo "Use --yes when running non-interactively"
	exit 1
fi

##### Logging ##################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Persistent log, on the SD card rather than the journal: journald is configured
# with Storage=volatile and RuntimeMaxUse=8M, so an unattended run leaves no
# trace after a reboot. Appended, not truncated, so a stick that needs repairing
# repeatedly is visible as a pattern. Rotation is left to logrotate
LOGDIR="/home/pi/Oradio3/logging"
[ -d "$LOGDIR" ] || LOGDIR="$SCRIPT_DIR"
LOGFILE="${ORADIO_SYNC_LOG:-$LOGDIR/rclone.log}"

# Whether stdout was a terminal, recorded before it is replaced by the pipe below
INTERACTIVE=false
[ -t 1 ] && INTERACTIVE=true

# Send every line to both the terminal and the log. The sed strips colour
# escapes on the way to the file only, so the log stays greppable.
#
# The two writers are set up as separate process substitutions rather than
# nesting sed inside tee. Nested, sed is a grandchild of this shell and `wait`
# cannot reach it, so the script can exit before the last lines have been
# written and the log ends up short — or, if the reader is quick, empty.
# As siblings both are direct children, and cleanup closes stdout and fd 5 in
# that order before waiting: tee sees EOF first, then sed
if touch "$LOGFILE" 2>/dev/null; then
	exec 3>&1 4>&2
	exec 5> >(sed -u 's/\x1b\[[0-9;]*m//g' >> "$LOGFILE")
	exec > >(tee /dev/fd/5) 2>&1
	echo "$(date +'%Y-%m-%d %H:%M:%S') $(basename "$0") ${*:-(no arguments)}"
else
	echo -e "${YELLOW}Cannot write $LOGFILE; continuing without a log file${NC}"
	LOGFILE=""
fi

# Set when fsck repaired the filesystem, so the run can report it at the end
REPAIRED=false

##### Cleanup / restore on exit ################

# Global flag to indicate cleanup already done
CLEANUP_DONE=false

function cleanup {

	local signal="${1:-EXIT}"	# trap signal: EXIT, INT, TERM
	local exitcode="${2:-0}"	# optional exit code for EXIT

	# Reset terminal state if running interactively
	if [ -t 0 ]; then
		stty sane
	fi

	# Run only once (guards against overlapping trap signals)
	if $CLEANUP_DONE; then
		return
	fi
	CLEANUP_DONE=true

	# Handle signal messages
	case "$signal" in
		INT)
			echo -e "\n${RED}CTRL-C: Cleanup on exit:${NC}" 
			;;
		TERM)
			echo -e "\n${RED}SIGNAL: Cleanup on exit:${NC}" 
			;;
		EXIT)
			echo "Cleanup on exit (code $exitcode)"
			;;
	esac

	# Wipe decryption password from memory
	unset PW

	# Remove any /tmp files created by this script (tracked via RCLONE_* vars)
	rclone_vars=$(compgen -v | grep '^RCLONE_' || true)  # safe even if no matches
	if [ -n "$rclone_vars" ]; then
		while IFS= read -r var; do
			val="${!var:-}"		  # safe default if unset
			if [ -n "$val" ] && [ -f "$val" ]; then
				rm -f "$val" && echo " - Removed $val"
			fi
		done <<< "$rclone_vars"
	else
		echo "No temporary files removed"
	fi

	# Remount USB with original options if it was unmounted by this script
	if  [[ -n "${OPTIONS:-}" && -n "${DEVICE:-}" && -b "${DEVICE:-}" && -n "${MOUNTPOINT:-}" ]]; then

		# Hold the usb-drive.sh lock across the remount too. Re-locking an fd
		# this shell already holds returns immediately, so this is safe whether
		# or not the lock was released before the sync
		flock -x -w 10 9 2>/dev/null || true

		sudo umount "$DEVICE" 2>/dev/null || true
		if sudo mount -t vfat -o "$OPTIONS" "$DEVICE" "$MOUNTPOINT"; then
			sudo touch "${USB_FLAG:-/run/usb_present}"
			echo " - USB device successfully remounted"
		else
			# Leave no flag claiming a mount that is not there
			sudo rm -f "${USB_FLAG:-/run/usb_present}"
			echo -e "${RED}Failed to mount $DEVICE to $MOUNTPOINT${NC}"
		fi
	else
		echo "USB not remounted"
	fi

	# Restart services in reverse stop order
	if [ "${STOPPED_SERVICES+set}" = set ] && [ "${#STOPPED_SERVICES[@]}" -gt 0 ]; then
		for (( idx=${#STOPPED_SERVICES[@]}-1; idx>=0; idx-- )); do
			service="${STOPPED_SERVICES[idx]}"
			if sudo systemctl start "$service" >/dev/null 2>&1; then
				echo " - $service service started successfully"
			else
				echo -e "${RED}Failed to start $service${NC}"
			fi
		done
	else
		echo "No services restarted"
	fi

	# Last: stop logging and wait for the writers to drain. Nothing may echo
	# after this point, or it misses the log file. Closing stdout lets tee see
	# EOF, closing fd 5 then lets sed see it; `wait` returns once both are gone.
	# Guarded on fd 5 being open, since the log setup is skipped when $LOGFILE
	# is not writable
	if [ -e /proc/self/fd/5 ]; then
		exec 1>&3 2>&4
		exec 5>&-
		wait || true
	fi
}

trap 'EXITCODE=$?; cleanup EXIT $EXITCODE' EXIT		# Normal exit
trap 'cleanup INT; exit 130' INT					# Ctrl+C
trap 'cleanup TERM; exit 143' TERM					# Kill command
trap '' HUP  										# Keep running if SSH session disconnects

##### Dependencies #############################

sudo bash $SCRIPT_DIR/pkg-helper.sh rclone

##### Stop services using the USB ##############

# List of services to manage
SERVICES=("oradio" "mpd")

# Initialize array to track which services were stopped
STOPPED_SERVICES=()

# Stop running services
for service in "${SERVICES[@]}"; do
	if systemctl is-active --quiet "$service"; then
		if sudo systemctl stop "$service" >/dev/null 2>&1; then
			echo -e "${YELLOW}$service service stopped. Will be restarted later.${NC}"
			STOPPED_SERVICES+=("$service")
		else
			echo -e "${RED}Failed to stop $service service${NC}"
			exit 1
		fi
	fi
done

##### USB checks ###############################

# Define USB location
MOUNTPOINT="/media/oradio"

# Flag file that usb-drive.sh maintains: present = mounted, absent = unmounted.
# This script unmounts the USB for the health check, so it owns the flag for
# the duration and must keep it truthful
USB_FLAG="/run/usb_present"

# Lock used by usb-drive.sh (started from the udev rules for label ORADIO).
# Without it, an 'add' event during the health check would mount the
# filesystem underneath a running fsck. usb-drive.sh's own guard does not
# help: it tests mountpoint, which is false exactly because we unmounted
USB_LOCK="/run/usb_mount.lock"

# usb-drive.sh runs as root and creates the lock with 'exec 9>', leaving it
# root-owned 0644. Opening it for writing as pi fails, so open it read-only:
# flock() locks the open file description regardless of access mode, and an
# exclusive lock taken this way still blocks the root-owned handler
if [ ! -e "$USB_LOCK" ]; then
	sudo install -m 0644 /dev/null "$USB_LOCK"
fi
exec 9<"$USB_LOCK"

if ! flock -x -w 30 9; then
	echo -e "${RED}Timed out waiting for $USB_LOCK${NC}"
	echo "Another USB add/remove handler is still running"
	exit 1
fi

# Check USB present
if ! mountpoint -q "$MOUNTPOINT"; then
	echo -e "${RED}USB is missing${NC}"
	exit 1
fi

# Save mount device and options so cleanup can remount with identical settings.
# The read itself is the condition: with '|| true' inside the process
# substitution, empty output leaves read returning 1 and set -e ends the
# script before the checks below can explain why
if ! read -r DEVICE OPTIONS < <(findmnt -n -o SOURCE,OPTIONS "$MOUNTPOINT"); then
	echo -e "${RED}Could not determine device and mount options for $MOUNTPOINT${NC}"
	exit 1
fi

# Ensure DEVICE is set and exists
if [[ -z "${DEVICE:-}" || ! -b "${DEVICE:-}" ]]; then
	echo -e "${RED}USB device not found or invalid${NC}"
	exit 1
fi

# Ensure OPTIONS is set
if [[ -z "${OPTIONS:-}" ]]; then
	echo -e "${RED}Mount options could not be determined${NC}"
	exit 1
fi

# Unmount before checking. fsck.fat has no mounted-filesystem guard of its own
# (unlike e2fsck), so repairing a still-mounted volume is how small
# inconsistencies become large ones
sudo umount "$MOUNTPOINT" 2>/dev/null || true

# Verify it is really gone. usb-drive.sh falls back to 'umount -l' on its
# remove path, so busy mounts do happen on this device
if findmnt -n -S "$DEVICE" >/dev/null; then
	echo -e "${RED}$DEVICE is still mounted, cannot check safely${NC}"
	findmnt -n -S "$DEVICE" | sed 's/^/  /'
	echo "Find what is holding it with: sudo fuser -vm $MOUNTPOINT"
	exit 1
fi

# The USB is now unmounted: keep the flag file honest for as long as that lasts
sudo rm -f "$USB_FLAG"

# Filesystem check
echo "USB Health Check for $DEVICE"

# 1. Quick scan (read-only)
if sudo fsck.fat -n "$DEVICE"; then
	echo -e "${GREEN}Quick scan: no errors found${NC}"
else
	echo -e "${YELLOW}Quick scan: errors found, trying to repair${NC}"
	REPAIRED=true

	# 2. Repair.
	# No -f: in fsck.fat that means "salvage unused chains to files", not
	# "force". Auto mode writes orphaned clusters to FSCK0000.REC at the volume
	# root either way, so the flag only adds confusion. Exit code 1 means
	# errors were found and corrected; 2 or more means fsck could not proceed
	sudo fsck.fat -a "$DEVICE" || rc=$?
	rc=${rc:-0}
	if [ "$rc" -ge 2 ]; then
		echo -e "${RED}Repair failed (code $rc)${NC}"
		exit 1
	fi

	# 3. Re-check (must be clean)
	if ! sudo fsck.fat -n "$DEVICE"; then
		echo -e "${RED}Errors found, please repair with (low level) format${NC}"
		exit 1
	fi

	echo -e "${GREEN}Filesystem OK after re-check${NC}"
fi

# Optional sector scan (~20 min)
if $ASSUME_YES; then
	# Skipped rather than assumed: a 20 minute scan is not what an unattended
	# run wants, and its own prompt defaults to no anyway
	answer="N"
else
	read -r -p "Do you want to do a sector scan for bad blocks? (~20 min) [y/N]: " answer
fi
if [[ "$answer" =~ ^[yY]$ ]]; then

	# badblocks exits 0 even when it finds bad blocks: the count appears only in
	# its output. Write the block list to a file and judge on that instead
	BADBLOCKS_LIST="/tmp/oradio-badblocks.lst"
	sudo rm -f "$BADBLOCKS_LIST"

	if ! sudo badblocks -sv -o "$BADBLOCKS_LIST" "$DEVICE"; then
		echo -e "${RED}Sector scan could not run on $DEVICE${NC}"
		sudo rm -f "$BADBLOCKS_LIST"
		exit 1
	fi

	if [ -s "$BADBLOCKS_LIST" ]; then
		echo -e "${RED}$(wc -l < "$BADBLOCKS_LIST") bad blocks found, please repair with (low level) format${NC}"
		sudo rm -f "$BADBLOCKS_LIST"
		exit 1
	fi

	sudo rm -f "$BADBLOCKS_LIST"
	echo -e "${GREEN}Sector scan completed, no bad blocks found${NC}"
elif $ASSUME_YES; then
	echo "Skipping sector scan (--yes)"
else
	echo "Skipping sector scan"
fi

# Remount with explicit options (ensures consistent permissions).
# Note this drops the sync,flush,noatime,nodiratime that usb-drive.sh uses:
# deliberate, because 'sync' would make a multi-gigabyte rclone write painfully
# slow here. Cleanup restores the original options captured above
OPTS="rw,users,uid=0,gid=100,fmask=111,dmask=000,utf8=1"
if ! sudo mount -t vfat -o "$OPTS" "$DEVICE" "$MOUNTPOINT"; then
	echo -e "${RED}Failed to mount $DEVICE to $MOUNTPOINT${NC}"
	exit 1
fi

# Mounted again: restore the flag before anything else can read it
sudo touch "$USB_FLAG"

# No chown here. vfat has no on-disk ownership: it comes from the uid= and gid=
# mount options above. The kernel only permits a chown that matches those
# values, so 'chown -R root:users' walked every file on the stick to achieve
# nothing, and would fail hard under set -e if the options ever changed.

# The health check is done, so let usb-drive.sh handle add/remove events again
flock -u 9

##### rclone config ############################

RCLONE_ENC="/tmp/rclone.conf.enc"
RCLONE_CFG="/tmp/rclone.conf"

# Fetch the encrypted config from GitHub Releases (never stored in the repo)
curl -fsSL "https://github.com/oradiolabs/Oradio3/releases/download/config/rclone.conf.enc" -o "$RCLONE_ENC"

# Obtain the decryption password.
# ORADIO_SYNC_PW allows unattended runs; otherwise prompt interactively
PW=""
if [ -n "${ORADIO_SYNC_PW:-}" ]; then

	PW="$ORADIO_SYNC_PW"
	echo "Using decryption password from ORADIO_SYNC_PW"

	# Drop it from the environment so it is not inherited by rclone, curl or
	# any other child process, where it would be visible in /proc/<pid>/environ
	unset ORADIO_SYNC_PW

elif [ ! -t 0 ]; then

	# No terminal to prompt on: fail with a clear message instead of hanging
	echo -e "${RED}No terminal available to ask for the decryption password${NC}"
	echo "Set ORADIO_SYNC_PW when running non-interactively"
	exit 1

else

	# Prompt for password, show * for entered characters, supporting backspace.
	# Written straight to the terminal: the masking must not be buffered through
	# the log pipe, and a row of asterisks in the log file helps nobody
	{
		echo -n "Enter decryption password for sharepoint.conf.enc: "
		while IFS= read -r -s -n1 char; do

			# Break on Enter (newline or carriage return)
			[[ -z "$char" || $char == $'\n' || $char == $'\r' ]] && break

			if [[ $char == $'\177' ]]; then
				# Handle backspace
				if [ -n "$PW" ]; then
					PW=${PW%?}
					echo -ne "\b \b"
				fi
			else
				PW+="$char"
				echo -n "*"
			fi
		done
		echo
	} >/dev/tty
fi

# An empty password decrypts nothing; catch it before OpenSSL does
if [ -z "$PW" ]; then
	echo -e "${RED}No password entered${NC}"
	exit 1
fi

# Use password securely with OpenSSL.
# Passed via the environment, not -pass pass:, which would expose it in the
# process list to any user running ps. The assignment prefix limits the export
# to this one command
if ! PW="$PW" openssl enc -d -aes-256-cbc -pbkdf2 -base64 -in "$RCLONE_ENC" -out "$RCLONE_CFG" -pass env:PW 2>/dev/null; then
	echo -e "${RED}Decryption failed — wrong password or corrupted input${NC}"
	exit 1
fi

# Verify the decrypted config actually connects to SharePoint before proceeding
if rclone --config "$RCLONE_CFG" lsd stichtingsharepoint: >/dev/null; then
	echo "SharePoint connection verified successfully"
else
	echo -e "${RED}Could not verify SharePoint connection. Check credentials or network${NC}"
	exit 1
fi

##### Determine what to sync ###################

# Define source
SHAREPOINT="stichtingsharepoint:Docs_StichtingOradio/Music_Read_Only/Oradio3USB"

FILTER=()

if [ "${#SYNC_DIRS[@]}" -eq 0 ]; then

	# No arguments: sync the complete tree. List the top-level directories so
	# the summary below can show what that actually covers
	mapfile -t ROOT_DIRS < <(rclone --config "$RCLONE_CFG" lsf --dirs-only "$SHAREPOINT")
	ROOT_DIRS=("${ROOT_DIRS[@]%/}")

	if [ "${#ROOT_DIRS[@]}" -eq 0 ]; then
		echo -e "${RED}No directories found at the SharePoint source${NC}"
		echo "Syncing now would delete all USB content. Aborting"
		exit 1
	fi

	# Windows creates this folder on the USB; it is not on SharePoint, so
	# without this exclude a full sync would delete its contents
	FILTER+=(--exclude "/System Volume Information/**")

else

	# Check every requested directory exists, then turn it into an rclone filter.
	# A filter that matches nothing is not an error to rclone: it would sync zero
	# files and report success, so the existence check has to happen up front.
	for dir in "${SYNC_DIRS[@]}"; do

		parent="$(dirname "$dir")"
		name="$(basename "$dir")"

		# dirname returns "." for a top-level name; list the sync root in that case
		if [ "$parent" = "." ]; then
			remote="$SHAREPOINT"
			location="sync root"
		else
			remote="$SHAREPOINT/$parent"
			location="$parent"
		fi

		# List subdirectories of the parent; lsf appends a trailing slash.
		# An unreadable or non-existent parent yields an empty list, not a failure
		mapfile -t entries < <(rclone --config "$RCLONE_CFG" lsf --dirs-only "$remote" 2>/dev/null || true)
		entries=("${entries[@]%/}")

		# Case-sensitive match: SharePoint is case-insensitive, rclone filters are not
		found=false
		for entry in "${entries[@]}"; do
			if [ "$entry" = "$name" ]; then
				found=true
				break
			fi
		done

		if ! $found; then
			echo -e "${RED}'$dir' not found on SharePoint${NC}"
			if [ "${#entries[@]}" -gt 0 ]; then
				echo "Available in $location: ${entries[*]}"
			else
				echo "Nothing found in $location"
			fi
			exit 1
		fi

		# Leading slash anchors the pattern to the sync root, so a directory of the
		# same name nested elsewhere in the tree is not matched as well
		FILTER+=(--include "/$dir/**")
	done
fi

##### Sync #####################################

# Prompt for overwrite or check only
if $ASSUME_YES; then
	answer="N"
else
	read -r -p "Do you only want to check for differences? [y/N]: " answer
fi
if [[ "$answer" =~ ^[Yy]$ ]]; then
	DRYRUN_FLAG="--dry-run"
	echo -e "${YELLOW}Dry-run mode enabled: USB will not be updated${NC}"
else
	DRYRUN_FLAG=""
	echo -e "${YELLOW}Dry-run mode disabled: USB content will be overwritten${NC}"
fi

# Summarize what is about to happen, so the scope is visible before any change
echo
echo "About to synchronize:"
echo "  From: $SHAREPOINT"
echo "  To:   $MOUNTPOINT"
echo

if [ "${#SYNC_DIRS[@]}" -eq 0 ]; then
	echo "  Complete SharePoint tree:"
	for dir in "${ROOT_DIRS[@]}"; do
		echo "    - $dir"
	done
	echo
	echo -e "  ${YELLOW}USB content not present on SharePoint will be DELETED${NC}"
	echo "  Kept: System Volume Information"
else
	echo "  Selected directories:"
	for dir in "${SYNC_DIRS[@]}"; do
		echo "    - $dir"
	done
	echo
	echo "  Within these, USB content not present on SharePoint will be deleted"
	echo "  Everything else on the USB is left untouched"
fi

if [ -n "$DRYRUN_FLAG" ]; then
	echo -e "  ${YELLOW}Dry-run: nothing will actually be changed${NC}"
fi
echo

if $ASSUME_YES; then
	echo "Continuing without confirmation (--yes)"
else
	read -r -p "Continue? [y/N]: " answer
	if [[ ! "$answer" =~ ^[Yy]$ ]]; then
		echo "Aborted by user"
		exit 0
	fi
fi

echo "$(date +'%Y-%m-%d %H:%M:%S'): Start synchronizing SharePoint content to USB"
if [ "${#SYNC_DIRS[@]}" -eq 0 ]; then
	echo "Directories: complete tree (${ROOT_DIRS[*]})"
else
	echo "Directories: ${SYNC_DIRS[*]}"
fi

# Common rclone options:
#   --checksum			Compares by content, not just size/mtime
#   --delete-during		Deletes obsolete files during transfer (faster than after)
#   filters				Either --include patterns limiting the sync to the
#						requested directories, or --exclude for the Windows
#						metadata folder on a full sync. Deletions respect
#						filters, so excluded paths are never removed
RCLONE_ARGS=(
	sync "$SHAREPOINT" "$MOUNTPOINT"
	--config "$RCLONE_CFG"
	--checksum
	--delete-during
	"${FILTER[@]}"
)
[[ -n "$DRYRUN_FLAG" ]] && RCLONE_ARGS+=("$DRYRUN_FLAG")

SYNC_RC=0

if $INTERACTIVE; then

	# On a terminal, keep the live single-line display. It needs rclone's stdout
	# to BE a terminal, which it is not any more: the logging pipe replaced it.
	# Writing straight to /dev/tty gives rclone its terminal back, at the cost of
	# rclone's own chatter not reaching the log file - which is how this script
	# always behaved, since only the start and finish lines were ever logged
	#   --progress		In-place redraw of transfer progress
	#   --stats=1s		Refresh the display every second
	rclone "${RCLONE_ARGS[@]}" --progress --stats=1s >/dev/tty 2>&1 || SYNC_RC=$?

else

	# Unattended: no terminal to redraw on, so emit periodic one-line stats that
	# read sensibly in the log file. 30s rather than 1s to keep a long sync from
	# writing thousands of lines
	#   --stats-one-line	Condenses stats to a single line, without timestamp
	#   --stats-log-level	Forces a final summary even when non-interactive
	rclone "${RCLONE_ARGS[@]}" \
		--stats=30s \
		--stats-one-line \
		--stats-log-level NOTICE || SYNC_RC=$?
fi

if [ "$SYNC_RC" -eq 0 ]; then
	if [[ -n "$DRYRUN_FLAG" ]]; then
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Finished check${NC} — ${YELLOW}dry-run, no changes made${NC}"
	else
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Finished sync${NC}"
	fi
else
	echo -e "${RED}$(date +'%Y-%m-%d %H:%M:%S'): rclone sync failed with exit code $SYNC_RC${NC}"
fi

##### Result ###################################

# A repair means the stick had corruption. The sync heals file content within
# the synced directories, but the cause is not addressed, so say so plainly and
# exit non-zero: under --yes this is the only thing that will draw attention
if $REPAIRED; then
	echo
	echo -e "${YELLOW}NOTE: the filesystem was repaired during this run${NC}"
	echo "Corruption is usually a failing stick or an unclean removal."
	if [ -n "$LOGFILE" ]; then
		echo "Check $LOGFILE for how often this happens."
	fi
	echo "Any recovered fragments are in FSCK*.REC at the root of the USB."
	exit 1
fi

exit "$SYNC_RC"
