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
# @purpose:		 Synchronizes SharePoint content to USB using rclone.
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
		echo "No temporrary files removed"
	fi

	# Remount USB with original options if it was unmounted by this script
	if  [[ -n "${OPTIONS:-}" && -n "${DEVICE:-}" && -b "${DEVICE:-}" && -n "${MOUNTPOINT:-}" ]]; then
		sudo umount "$DEVICE" 2>/dev/null || true
		if sudo mount -t vfat -o "$OPTIONS" "$DEVICE" "$MOUNTPOINT"; then
			echo " - USB device successfully remounted"
		else
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
}

trap 'EXITCODE=$?; cleanup EXIT $EXITCODE' EXIT		# Normal exit
trap 'cleanup INT; exit 130' INT					# Ctrl+C
trap 'cleanup TERM; exit 143' TERM					# Kill command
trap '' HUP  										# Keep running if SSH session disconnects

##### Dependencies #############################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

# Check USB present
if ! mountpoint -q "$MOUNTPOINT"; then
	echo -e "${RED}USB is missing${NC}"
	exit 1
fi

# Save mount device and options so cleanup can remount with identical settings
read -r DEVICE OPTIONS < <(findmnt -n -o SOURCE,OPTIONS "$MOUNTPOINT" || true)

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

# Unmount silently, ignoring errors if not mounted
sudo umount "$DEVICE" 2>/dev/null || true

# Filesystem check
echo "USB Health Check for $DEVICE"

# 1. Quick scan (read-only)
if sudo fsck.fat -n "$DEVICE"; then
	echo -e "${GREEN}Quick scan: no errors found${NC}"
else
	echo -e "${YELLOW}Quick scan: errors found, trying to repair${NC}"

	# 2. Repair
	sudo fsck.fat -a -f "$DEVICE" || rc=$?
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
read -r -p "Do you want to do a sector scan for bad blocks? (~20 min) [y/N]: " answer
if [[ "$answer" =~ ^[yY]$ ]]; then
	if sudo badblocks -sv "$DEVICE"; then
		echo -e "${GREEN}Sector scan completed, no bad blocks found${NC}"
	else
		echo -e "${RED}Errors found, please repair with (low level) format${NC}"
		exit 1
	fi
else
	echo "Skipping sector scan"
fi

# Remount with explicit options (ensures consistent permissions)
OPTS="rw,users,uid=0,gid=100,fmask=111,dmask=000,utf8=1"
if ! sudo mount -t vfat -o "$OPTS" "$DEVICE" "$MOUNTPOINT"; then
	echo -e "${RED}Failed to mount $DEVICE to $MOUNTPOINT${NC}"
	exit 1
fi

# Force ownership and group of USB content to root:users
sudo chown -R root:users "$MOUNTPOINT"

##### rclone config ############################

RCLONE_ENC="/tmp/rclone.conf.enc"
RCLONE_CFG="/tmp/rclone.conf"

# Fetch the encrypted config from GitHub Releases (never stored in the repo)
curl -fsSL "https://github.com/oradiolabs/Oradio3/releases/download/config/rclone.conf.enc" -o "$RCLONE_ENC"

# Prompt for password, show * for entered charracters, supporting backspace
PW=""
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

# Use password securely with OpenSSL
if ! openssl enc -d -aes-256-cbc -pbkdf2 -base64 -in "$RCLONE_ENC" -out "$RCLONE_CFG" -pass pass:"$PW" 2>/dev/null; then
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

##### Sync #####################################

# Prompt for overwrite or check only
read -r -p "Do you only want to check for differences? [y/N]: " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
	DRYRUN_FLAG="--dry-run"
	echo -e "${YELLOW}Dry-run mode enabled: USB will not be updated${NC}"
else
	DRYRUN_FLAG=""
	echo -e "${YELLOW}Dry-run mode disabled: USB content will be overwritten${NC}"
fi

# Create empty log file capturing rclone output
LOGFILE="rclone.log"
: > "$LOGFILE"	# truncate/create log

# Define source and destination
SHAREPOINT="stichtingsharepoint:Docs_StichtingOradio/Music_Read_Only/Oradio3USB"

echo "$(date +'%Y-%m-%d %H:%M:%S'): Start synchronizing SharePoint content to USB" | tee -a "$LOGFILE"

# Run the sync with options:
#   --stats=1s			Updates stats every second
#   --stats-one-line	Condenses stats to a single line, without timestamp
#   --stats-log-level	Forces final summary even when non-interactive
#   --progress			Shows live progress
#   --checksum			Compares by content, not just size/mtime
#   --delete-during		Deletes obsolete files during transfer (faster than after)
#   --exclude			Skips Windows metadata folder
if rclone sync "$SHAREPOINT" "$MOUNTPOINT" \
	--config "$RCLONE_CFG" \
	--stats=1s \
	--stats-one-line \
	--stats-log-level NOTICE \
	--progress \
	--checksum \
	--delete-during \
	--exclude "System Volume Information/**" \
	$DRYRUN_FLAG; then
	# Send colored output to terminal, plain to logfile
	if [[ -n "$DRYRUN_FLAG" ]]; then
		MSG="Finished check — dry-run, no changes made"
		echo "$(date +'%Y-%m-%d %H:%M:%S'): $MSG" >> "$LOGFILE"
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Finished check${NC} — ${YELLOW}dry-run, no changes made${NC}"
	else
		MSG="Finished sync"
		echo "$(date +'%Y-%m-%d %H:%M:%S'): $MSG" >> "$LOGFILE"
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): $MSG${NC}"
	fi
else
	RC=$?
	echo -e "$(date +'%Y-%m-%d %H:%M:%S'): rclone sync failed with exit code $RC" >> "$LOGFILE"
	echo -e "${RED}$(date +'%Y-%m-%d %H:%M:%S'): rclone sync failed with exit code $RC${NC}"
fi
