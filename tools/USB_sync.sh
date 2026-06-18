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
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1
# @email:         info@stichtingoradio.nl
# @status:        Development
################################################################################
# HOW TO CREATE OR REPLACE THE ENCRYPTED RCLOUD CONFIG BLOCK
# ------------------------------------------------------------------------------
# Purpose:
#   The installer embeds an AES-256-CBC encrypted copy of the full rclone.conf.
#   During installation, it decrypts this block (using a password) to recreate:
#       /home/pi/.config/rclone/rclone.conf
#   providing secure SharePoint/OneDrive access without storing plaintext tokens.
#
# Process summary:
#   rclone.conf  (working file with valid SharePoint credentials)
#        ↓ encrypt (OpenSSL AES-256-CBC + PBKDF2 + base64)
#   sharepoint.conf.enc  (encrypted text block)
#        ↓ paste block into installer between BEGIN/END markers
#   installer decrypts → rclone.conf → used by USB update
#
# Step-by-step (on a secure development system):
#
#   1. Confirm rclone works:
#        rclone lsd stichtingsharepoint:
#
#   2. Copy the full config:
#        cp /home/pi/.config/rclone/rclone.conf sharepoint.conf
#
#   3. Encrypt it:
#        openssl enc -aes-256-cbc -pbkdf2 -salt -in sharepoint.conf \
#            -out sharepoint.conf.enc -base64
#
#      → You will be prompted for a password. Keep this secret and consistent
#        with the one used by the installer.
#
#   4. Test decryption:
#        openssl enc -d -aes-256-cbc -pbkdf2 -base64 -in sharepoint.conf.enc
#
#   5. Paste the entire base64 block (including trailing ==) into the installer:
#        -----BEGIN ENCRYPTED RCLOUD CONFIG-----
#        <paste here>
#        -----END ENCRYPTED RCLOUD CONFIG-----
#
#   6. Delete plaintext after encryption:
#        shred -u sharepoint.conf  ||  rm -f sharepoint.conf
#
################################################################################

##### Initialize #####################

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

#---------- Ensure using bash ----------

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Ensure required packages are installed and up to date ----------

# Get script path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install/update required packages
sudo bash $SCRIPT_DIR/pkg-helper.sh rclone

#---------- Configure cleanup and restore on exit ----------

# Global flag to indicate cleanup already done
CLEANUP_DONE=false

function cleanup {

	local signal="${1:-EXIT}"	# trap signal: EXIT, INT, TERM
	local exitcode="${2:-0}"	# optional exit code for EXIT

    # Reset terminal if attached to a TTY
    if [ -t 0 ]; then
        stty sane
    fi

	# Skip if cleanup already ran
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

    # Clear sensitive variable if set
    unset PW

	# Safely remove all RCLONE_* files
	rclone_vars=$(compgen -v | grep '^RCLONE_' || true)  # safe even if no matches
	if [ -n "$rclone_vars" ]; then
		while IFS= read -r var; do
			val="${!var:-}"          # safe default if unset
			if [ -n "$val" ] && [ -f "$val" ]; then
				rm -f "$val" && echo " - Removed $val"
			fi
		done <<< "$rclone_vars"
	else
		echo "No temporrary files removed"
	fi

	# Safely remount USB
	if  [[ -n "${OPTIONS:-}" && -n "${DEVICE:-}" && -b "${DEVICE:-}" && -n "${MOUNTPOINT:-}" ]]; then
		# Unmount silently, ignoring errors if not mounted
		sudo umount "$DEVICE" 2>/dev/null || true

		# Attempt to mount the device with the provided options
		if sudo mount -t vfat -o "$OPTIONS" "$DEVICE" "$MOUNTPOINT"; then
			echo " - USB device successfully remounted"
		else
			echo -e "${RED}Failed to mount $DEVICE to $MOUNTPOINT${NC}"
		fi
	else
		echo "USB not remounted"
	fi

	# Restore services safely
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

# EXIT trap for normal exit
trap 'EXITCODE=$?; cleanup EXIT $EXITCODE' EXIT

# INT trap for Ctrl+C
trap 'cleanup INT; exit 130' INT

# TERM trap (optional)
trap 'cleanup TERM; exit 143' TERM

# HUP trap: ignore hangup so script keeps running after SSH disconnects
trap '' HUP

#---------- Stop services using the USB ----------

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

#---------- Ensure USB is present and ready ----------

# Define USB location
MOUNTPOINT="/media/oradio"

# Check USB present
if ! mountpoint -q "$MOUNTPOINT"; then
	echo -e "${RED}USB is missing${NC}"
	exit 1
fi

# Get device and options for the mount point
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

#---------- USB health check ----------

echo "USB Health Check for $DEVICE"

set -euo pipefail

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

# Ask for sector scan
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

# Mount with desired options
OPTS="rw,users,uid=0,gid=100,fmask=111,dmask=000,utf8=1"
if ! sudo mount -t vfat -o "$OPTS" "$DEVICE" "$MOUNTPOINT"; then
	echo -e "${RED}Failed to mount $DEVICE to $MOUNTPOINT${NC}"
	exit 1
fi

# Force ownership and group of USB content to root:users
sudo chown -R root:users "$MOUNTPOINT"

#---------- Prepare rclone environment ----------

RCLONE_TMP="/tmp/rclone.tmp"
RCLONE_CFG="/tmp/rclone.cfg"

# Paste your encrypted block (from sharepoint.conf.enc) between the markers below
cat <<'ENCRYPTED' > "$RCLONE_TMP"
-----BEGIN ENCRYPTED RCLOUD CONFIG-----
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
yS64qemrncbhzWT5M6ZsRw==
-----END ENCRYPTED RCLOUD CONFIG-----
ENCRYPTED

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
if ! openssl enc -d -aes-256-cbc -pbkdf2 -base64 -in "$RCLONE_TMP" -out "$RCLONE_CFG" -pass pass:"$PW" 2>/dev/null; then
	echo -e "${RED}Decryption failed — wrong password or corrupted input${NC}"
	exit 1
fi

# Test rclone connection
if rclone --config "$RCLONE_CFG" lsd stichtingsharepoint: >/dev/null; then
	echo "SharePoint connection verified successfully"
else
	echo -e "${RED}Could not verify SharePoint connection. Check credentials or network${NC}"
	exit 1
fi

# Prompt for overwrite or check only
read -r -p "Do you only want to check for differences? [y/N]: " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
	DRYRUN_FLAG="--dry-run"
	echo -e "${YELLOW}Dry-run mode enabled: USB will not be updated${NC}"
else
    DRYRUN_FLAG=""
	echo -e "${YELLOW}Dry-run mode disabled: USB content will be overwritten${NC}"
fi

#---------- rclone SharePoint content with USB ----------

# Create empty log file capturing rclone output
LOGFILE="rclone.log"
: > "$LOGFILE"

# Define source and destination
SHAREPOINT="stichtingsharepoint:Docs_StichtingOradio/Music_Read_Only/Oradio3USB"

echo "$(date +'%Y-%m-%d %H:%M:%S'): Start synchronizing SharePoint content to USB" | tee -a "$LOGFILE"

# Run the sync with options:
# --progress				Shows live progress (interactive)
# --stats=1s				Updates stats every second
# --stats-one-line-date		Each stats line includes a timestamp and overwrites less
# --stats-log-level NOTICE	Forces rclone to print the final total summary when done — even if not interactive
# --checksum				Compares files by checksum
# --delete-during			Deletes files during transfer (faster)
# --exclude ...				Skips unwanted files
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
		echo -e "$(date +'%Y-%m-%d %H:%M:%S'): Finished checking SharePoint content versus USB - dry-run, no changes made" >> "$LOGFILE"
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Finished checking SharePoint content versus USB${NC} - ${YELLOW}dry-run, no changes made${NC}"
	else
		echo -e "$(date +'%Y-%m-%d %H:%M:%S'): Finished synchronizing SharePoint content to USB" >> "$LOGFILE"
		echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Finished synchronizing SharePoint content to USB${NC}"
	fi
else
	RC=$?
	echo -e "$(date +'%Y-%m-%d %H:%M:%S'): rclone sync failed with exit code $RC" >> "$LOGFILE"
	echo -e "${RED}$(date +'%Y-%m-%d %H:%M:%S'): rclone sync failed with exit code $RC${NC}"
fi
