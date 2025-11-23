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

#---------- 1. Ensure using bash ----------

# The script uses bash constructs
if [ -z "$BASH" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- 2. Ensure connected to internet ----------

if ! ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
	echo -e "${RED}No internet connection${NC}"
	exit 1
else
	echo "Connected to Internet"
fi

#---------- Ensure rclone is installed and up to date ----------

# Install rsync if missing or upgrade if out of date
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

# Ensure rclone package is installed and up-to-date
if dpkg -s "rclone" &>/dev/null; then
	# Check if installed package can be upgraded
	if [[ ${UPGRADABLE_MAP["rclone"]+_} ]]; then
		echo -e "${YELLOW}rclone is outdated: upgrading...${NC}"
		sudo apt-get install -y "rclone"
	else
		echo "rclone is up-to-date"
	fi
else
	echo -e "${YELLOW}rclone is missing: installing...${NC}"
	sudo apt-get install -y "rclone"
fi

#---------- 3. Configure cleanup and restore on exit ----------

# Global flag to indicate cleanup already done
CLEANUP_DONE=false

function cleanup {

	local signal="${1:-EXIT}"	# trap signal: EXIT, INT, TERM
	local exitcode="${2:-0}"	# optional exit code for EXIT

#    # Reset terminal
#    stty sane
    # Reset terminal (only if attached to a TTY)
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

#---------- 4. Stop services using the USB ----------

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

#---------- 5. Ensure USB is present and ready ----------

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

#---------- 6. Prepare rclone environment ----------

RCLONE_TMP="/tmp/rclone.tmp"
RCLONE_CFG="/tmp/rclone.cfg"

# Paste your encrypted block (from sharepoint.conf.enc) between the markers below
cat <<'ENCRYPTED' > "$RCLONE_TMP"
-----BEGIN ENCRYPTED RCLOUD CONFIG-----
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
sWrzosQDZCiUQ24Tag0iUQ==
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
read -r -p "Run in dry-run mode? [y/N]: " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
	DRYRUN_FLAG="--dry-run"
	echo -e "${YELLOW}Dry-run mode enabled: USB will not be updated${NC}"
else
    DRYRUN_FLAG=""
	echo -e "${YELLOW}Dry-run mode disabled: USB content will be overwritten${NC}"
fi

#---------- 7. rclone SharePoint content with USB ----------

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
