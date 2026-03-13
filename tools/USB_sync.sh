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

#---------- Ensure connected to internet ----------

if ! ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
	echo -e "${RED}No internet connection${NC}"
	exit 1
else
	echo "Connected to Internet"
fi

#---------- Ensure required packages are installed and up to date ----------

STAMP_FILE="/var/lib/apt/last_update_stamp"
MAX_AGE=$((6 * 3600))	# 6 hours in seconds

# Get last time the list was updated, 0 if never
if [[ -f "$STAMP_FILE" ]]; then
	last_update=$(cat "$STAMP_FILE")
else
	last_update=0
fi

# Get time since last update
current_time=$(date +%s)
age=$((current_time - last_update))

# Update lists if to old
if (( age > MAX_AGE )); then
	sudo apt-get update
	# Save time lists were updated
	date +%s | sudo tee "$STAMP_FILE" >/dev/null
	echo "Package lists updated"
else
	echo "Package lists are up to date"
fi
# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

# Fetch list of upgradable packages
UPGRADABLE=$(apt list --upgradable 2>/dev/null | cut -d/ -f1)

# Create associative array for fast lookup
declare -A UPGRADABLE_MAP
for package in $UPGRADABLE; do
	UPGRADABLE_MAP["$package"]=1
done

# Required packages
REQUIRED_PACKAGES=(
	rclone
)

# Ensure packages are installed and up-to-date
for package in "${REQUIRED_PACKAGES[@]}"; do
	if dpkg -s "$package" &>/dev/null; then
		# Check if installed package can be upgraded
		if [[ ${UPGRADABLE_MAP["$package"]+_} ]]; then
			echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
			sudo apt-get install -y "$package"
		else
			echo "$package is up-to-date"
		fi
	else
		echo -e "${YELLOW}$package is missing: installing...${NC}"
		sudo apt-get install -y "$package"
	fi
done

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
joZLQY22JnkNDotdxyfOMQ==
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
