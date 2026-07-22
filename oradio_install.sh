#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on January 19, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       2
# @email:         info@stichtingoradio.nl
# @status:        Development

########## INITIALIZE BEGIN ##########

# Fail fast on unset variables and on failures hidden inside a pipeline
# (e.g. `curl ... | python -c ...`). We deliberately do NOT use `set -e`:
# large parts of this script rely on checking a command's exit status
# with `if`/`||` and continuing (see INSTALL_ERROR below), which `set -e`
# would short-circuit in surprising ways.
set -uo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo -e "${RED}Aborting: This script requires bash${NC}"
	exit 1
fi

# Enable passwordless sudo (no password prompt running sudo)
# https://www.raspberrypi.com/documentation/computers/configuration.html#disable-sudo-password
if ! sudo -p "Enter Oradio3 password: " raspi-config nonint do_sudo_pass 1; then
	echo -e "${RED}Aborting: Incorrect password${NC}"
	exit 1
fi

# Get the script path and name.
SCRIPT_PATH=$(cd "$(dirname "$(readlink -f "$BASH_SOURCE")")" && pwd)
SCRIPT_NAME=$(basename "$BASH_SOURCE")

# Working directory
cd "$SCRIPT_PATH" || { echo -e "${RED}Aborting: Failed to cd to $SCRIPT_PATH${NC}"; exit 1; }

# Location of Oradio3 program
MAIN_PATH="$SCRIPT_PATH/Main"
# Location of log files
LOGGING_PATH="$SCRIPT_PATH/logging"
# Spotify directory
SPOTIFY_PATH="$SCRIPT_PATH/Spotify"
# Location of files to install
RESOURCES_PATH="$SCRIPT_PATH/install_resources"

# Ensure logging directory exists
mkdir -p "$LOGGING_PATH" || { echo -e "${RED}Aborting: Failed to create directory $LOGGING_PATH${NC}"; exit 1; }

# Define log files
LOGFILE_USB="$LOGGING_PATH/usb.log"
LOGFILE_MPD="$LOGGING_PATH/mpd.log"
LOGFILE_SPOTIFY="$LOGGING_PATH/spotify.log"
LOGFILE_INSTALL="$LOGGING_PATH/install.log"
LOGFILE_TRACEBACK="$LOGGING_PATH/traceback.log"

# Redirect script output to console and file
exec > >(tee -a "$LOGFILE_INSTALL") 2>&1

# When leaving this script stop redirection and wait until redirect process has finished.
# NOTE: this assumes a tty is attached (interactive run). If this script is ever invoked
# from a context without one (cron, a CI runner, `ssh host script.sh < /dev/null`), the
# `exec > /dev/tty` below will fail; that failure is harmless here since it only affects
# where *further* output after the trap goes, not the exit status of the script itself.
trap 'exec > /dev/tty 2>&1; wait' EXIT

# Script is for Raspberry Pi OS Lite (64bit)
TARGETOS="Debian GNU/Linux 13 (trixie)"
OSVERSION=$(lsb_release -a 2>/dev/null | grep "Description:" | cut -d$'\t' -f2)
if [ "$OSVERSION" != "$TARGETOS" ]; then
	echo -e "${RED}Aborting: Invalid OS version: $OSVERSION${NC}"
	# Stop with error flag
	exit 1
fi

# Network domain name
HOSTNAME="oradio"

# Clear flag indicating reboot required to complete the installation
unset REBOOT_NEEDED

# Clear flag indicating installation error
unset INSTALL_ERROR

# Install file replacing placeholders and execute follow-up commands.
#
#   install_resource SRC DST [CMD...]
#
#   - If "SRC.template" exists, it is rendered into SRC first, replacing
#     PLACEHOLDER_USER / PLACEHOLDER_GROUP / PLACEHOLDER_<PATH_VAR> tokens
#     with the current user/group and the path variables defined above
#     (MAIN_PATH, SPOTIFY_PATH, LOGGING_PATH, LOGFILE_*).
#   - SRC is copied to DST via sudo only if the two files differ, so
#     re-running this script is idempotent and quiet on unchanged files.
#   - Any trailing CMD arguments run via `sudo bash -c "CMD"` *after* a
#     successful copy (e.g. `chmod +x ...`, `systemctl enable ...`).
#   - Sets the global INSTALL_ERROR flag (rather than exiting immediately)
#     on any failure, so one bad resource doesn't abort the whole install;
#     the script checks INSTALL_ERROR once, near the end, and exits then.
#   - GOTCHA FOR FUTURE EDITS: a trailing CMD such as `FOO=1` does NOT set
#     FOO in the calling script — it runs inside a throwaway `sudo bash -c`
#     subshell and is discarded when that subshell exits. Don't use a
#     trailing CMD to set a flag like REBOOT_NEEDED; set that directly in
#     the caller based on install_resource's return value instead, e.g.:
#       install_resource "$SRC" "$DST" && REBOOT_NEEDED=true
function install_resource {
	if [ $# -lt 2 ]; then
		echo -e "${RED}Aborting: install_resource has too few arguments: '$*'${NC}"
		echo "Usage: $0 src dst"
		# Stop with error flag
		INSTALL_ERROR=1
		return 1
	fi

	local SRC=$1
	local DST=$2
	shift 2

	if [ -f "$SRC.template" ]; then

		# Create by replacing placeholders
		cp "$SRC.template" "$SRC" || { echo -e "${RED}Failed to copy $SRC.template to $SRC${NC}"; INSTALL_ERROR=1; return 1; }

		# Replace placeholders. Combined into one sed invocation (instead of one
		# `sed -i` per substitution) to avoid re-opening/rewriting the file N times.
		local SED_ARGS=(-e "s/PLACEHOLDER_USER/$(id -un)/g" -e "s/PLACEHOLDER_GROUP/$(id -gn)/g")
		for VAR_NAME in MAIN_PATH SPOTIFY_PATH LOGGING_PATH LOGFILE_USB LOGFILE_MPD LOGFILE_INSTALL LOGFILE_SPOTIFY LOGFILE_TRACEBACK; do
			local VALUE="${!VAR_NAME}"
			# Escape & because sed treats it specially in the replacement text
			local ESCAPED_VALUE
			ESCAPED_VALUE=$(echo "$VALUE" | sed 's/[&]/\\&/g')
			local PLACEHOLDER="PLACEHOLDER_${VAR_NAME}"
			# Use | as delimiter instead of / since paths contain /
			SED_ARGS+=(-e "s|$PLACEHOLDER|$ESCAPED_VALUE|g")
		done
		sed -i "${SED_ARGS[@]}" "$SRC" || { echo -e "${RED}Failed to render placeholders in $SRC${NC}"; INSTALL_ERROR=1; return 1; }
	fi

	# Install only if files differ
	if ! cmp -s "$SRC" "$DST"; then
		echo "Installing '$SRC' to '$DST'"
		if ! sudo cp "$SRC" "$DST"; then
			echo -e "${RED}Failed to install '$DST'${NC}"
			INSTALL_ERROR=1
			return 1
		fi

		# Execute any extra commands (chmod, systemctl enable, etc). Each one is
		# checked independently and failures are recorded but don't stop the loop,
		# so a single bad follow-up command doesn't hide problems with the others.
		for CMD in "$@"; do
			echo "Executing: '$CMD'"
			if ! sudo bash -c "$CMD"; then
				echo -e "${RED}Command failed: '$CMD'${NC}"
				INSTALL_ERROR=1
			fi
		done
	fi

	return 0
}

########## INITIALIZE END ##########

if [ "${1:-}" != "--continue" ]; then

########## INITIAL RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Starting '$SCRIPT_NAME'${NC}"

########## OS PACKAGES BEGIN ##########

	STAMP_FILE="/var/lib/apt/last_update_stamp"
	MAX_AGE=$((6 * 3600))	# 6 hours in seconds

	# Get last time the list was updated, 0 if never (or if the stamp file is
	# missing/corrupted — guard against a non-numeric value breaking the
	# arithmetic below, e.g. after a partial write or manual edit).
	if [[ -f "$STAMP_FILE" ]]; then
		last_update=$(cat "$STAMP_FILE")
		[[ "$last_update" =~ ^[0-9]+$ ]] || last_update=0
	else
		last_update=0
	fi

	# Get time since last update
	current_time=$(date +%s)
	age=$((current_time - last_update))

	# Update lists if too old. Only report success (and only refresh the
	# stamp) once `apt update` has actually confirmed success — otherwise a
	# transient failure (e.g. no network) would get cached as "up to date"
	# for up to MAX_AGE and mask itself on the next run.
	if (( age > MAX_AGE )); then
		echo -e "${YELLOW}Package lists out of date, updating...${NC}"
		# Ensure the package list is clean
		sudo rm -rf /var/lib/apt/lists/*
		sudo apt clean
		# Get the latest package lists
		if sudo apt update; then
			# Save time lists were updated
			date +%s | sudo tee "$STAMP_FILE" >/dev/null
			echo -e "${GREEN}Package lists are up to date${NC}"
		else
			echo -e "${RED}Aborting: apt update failed (check network/repositories)${NC}"
			exit 1
		fi
	else
		echo -e "${GREEN}Package lists are up to date${NC}"
	fi
	# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

########## OS PACKAGES END ##########

########## ORADIO3 LINUX PACKAGES BEGIN ##########

#***************************************************************#
#   Add any additionally required packages to 'LINUX_PACKAGES'  #
#***************************************************************#
	LINUX_PACKAGES=(
		jq
		git
		mpd
		mpc
		caps
		iptables
		raspotify
		python3-gi
		python3-dev
		python3-dbus
		python3-jinja2
		python3-requests
		python3-watchdog
	)

	# Fetch list of upgradable packages once, up front, rather than shelling
	# out to `apt` again for every package in the loop below.
	UPGRADABLE=$(apt list --upgradable 2>/dev/null | cut -d/ -f1)

	# Create associative array for fast lookup
	declare -A UPGRADABLE_MAP
	for package in $UPGRADABLE; do
		UPGRADABLE_MAP["$package"]=1
	done

	# Ensure Linux packages are installed and up-to-date
	unset REBUILD_PYTHON_ENV
	for package in "${LINUX_PACKAGES[@]}"; do
		if dpkg -s "$package" &>/dev/null; then
			# Check if installed package can be upgraded
			if [[ ${UPGRADABLE_MAP["$package"]+_} ]]; then
				echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
				if sudo apt-get install -y "$package"; then
					REBUILD_PYTHON_ENV=1
				else
					echo -e "${RED}Failed to upgrade $package${NC}"
					INSTALL_ERROR=1
				fi
			else
				echo "$package is up-to-date"
			fi
		else
			echo -e "${YELLOW}$package is missing: installing...${NC}"
			if [ "$package" == "raspotify" ]; then
				# raspotify needs to be configured separately
				# Install raspotify which includes librespot.
				# NOTE: this pipes a remote, unpinned install script straight into
				# `sh` as root. Convenient, but means the exact code that runs
				# depends on whatever dtcooper's server serves at run time. If
				# reproducibility/auditability ever matters more than convenience,
				# switch to: download to a file, check `curl`'s exit status, then
				# `sh` the local copy (optionally after inspecting/pinning it).
				if curl -sL https://dtcooper.github.io/raspotify/install.sh | sh; then
					# Only keep librespot
					sudo systemctl stop raspotify
					sudo systemctl disable raspotify
				else
					echo -e "${RED}Failed to install raspotify${NC}"
					INSTALL_ERROR=1
				fi
			else
				if sudo apt-get install -y "$package"; then
					REBUILD_PYTHON_ENV=1
				else
					echo -e "${RED}Failed to install $package${NC}"
					INSTALL_ERROR=1
				fi
			fi
		fi
	done

	# Progress report
	echo -e "${GREEN}Oradio3 packages installed and up to date${NC}"

########## ORADIO3 LINUX PACKAGES END ##########

########## PYTHON BEGIN ##########

	# If needed, prepare python virtual environment including system site packages
	if [ -n "${REBUILD_PYTHON_ENV:-}" ]; then
		echo "Configuring Python virtual environment"
		python3 -m venv --system-site-packages ~/.venv
	fi

	# Activate the python virtual environment in current environment
	source ~/.venv/bin/activate

	# Activate python virtual environment when logging in if not yet present
	ADDTOBASHRC="source ~/.venv/bin/activate"
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}" >> ~/.bashrc

	# Set paths to python scripts if not yet present.
	# Quoted so the path survives intact in ~/.bashrc even if SCRIPT_PATH
	# ever contains a space (it doesn't today, but nothing enforces that).
	ADDTOBASHRC="export PYTHONPATH=\"${SCRIPT_PATH}/Main:${SCRIPT_PATH}/module_test\""
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}" >> ~/.bashrc

	# Progress report
	echo -e "${GREEN}Python virtual environment configured${NC}"

	# https://www.raspberrypi.com/documentation/computers/os.html#use-python-on-a-raspberry-pi

#***************************************************************#
#   Add any additionally required Python modules to 'PYTHON'    #
#***************************************************************#
	PYTHON_PACKAGES=(
		nmcli
		fastapi
		uvicorn
		python-mpd2
		python-multipart
		concurrent-log-handler
	)

	# Ensure Python packages are installed and up-to-date.
	# `pip` tracks installed versions and can diff them against the index
	# itself, so we ask it once for "what's installed" and once for "what's
	# outdated", then look up each package in those two results.
	INSTALLED_JSON=$(python3 -m pip list --format=json) || { echo -e "${RED}Aborting: pip list failed${NC}"; exit 1; }
	OUTDATED_JSON=$(python3 -m pip list --outdated --format=json) || { echo -e "${RED}Aborting: pip list --outdated failed${NC}"; exit 1; }

	for package in "${PYTHON_PACKAGES[@]}"; do
		# jq -e exits 1 (not an error) when no entry matches; only a malformed
		# JSON payload should be treated as a real failure, so we only check
		# for that below rather than treating "not found" as fatal here.
		if ! echo "$INSTALLED_JSON" | jq -e --arg pkg "$package" \
				'.[] | select(.name | ascii_downcase == ($pkg | ascii_downcase))' >/dev/null; then
			echo -e "${YELLOW}$package is missing: installing...${NC}"
			# On --use-pep517 see https://peps.python.org/pep-0517/
			if ! python3 -m pip install --upgrade --use-pep517 "$package"; then
				echo -e "${RED}Failed to install $package${NC}"
				INSTALL_ERROR=1
			fi
		elif echo "$OUTDATED_JSON" | jq -e --arg pkg "$package" \
				'.[] | select(.name | ascii_downcase == ($pkg | ascii_downcase))' >/dev/null; then
			echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
			# On --use-pep517 see https://peps.python.org/pep-0517/
			if ! python3 -m pip install --upgrade --use-pep517 "$package"; then
				echo -e "${RED}Failed to upgrade $package${NC}"
				INSTALL_ERROR=1
			fi
		else
			echo "$package is up-to-date"
		fi
	done

	# Progress report
	echo -e "${GREEN}Python packages installed and up-to-date${NC}"

########## PYTHON END ##########

########## CONFIGURATION BEGIN ##########

	# Install boot options.
	# install_resource returns 0 if it installed something new (or if the
	# file was already up to date — see its "differ" check), non-zero on failure.
	if ! cmp -s "$RESOURCES_PATH/config.txt" /boot/firmware/config.txt 2>/dev/null; then
		if install_resource "$RESOURCES_PATH/config.txt" /boot/firmware/config.txt; then
			REBOOT_NEEDED=true
		fi
	fi

	# Configure for Oradio3 USB to force load USB-storage device
	if ! sudo grep -q "usb-storage.quirks=0781:5583:u" /boot/firmware/cmdline.txt; then
		sudo sed -i 's/$/ usb-storage.quirks=0781:5583:u/' /boot/firmware/cmdline.txt
		# Reboot required to activate
		REBOOT_NEEDED=true
	fi

	# Progress report
	echo -e "${GREEN}Boot options and USB driver configured${NC}"

########## CONFIGURATION END ##########

	# Reboot if required for activation
	if [ -v REBOOT_NEEDED ]; then
		# Configure to continue the installation after reboot: appends a line to
		# ~/.bashrc that re-invokes this script with --continue. Since this edits
		# the invoking user's own home directory, it deliberately does NOT use
		# sudo (the file must stay owned by that user, and no root access is
		# needed to write to it).
		grep -qxF "bash $SCRIPT_PATH/$SCRIPT_NAME --continue" ~/.bashrc || echo "bash $SCRIPT_PATH/$SCRIPT_NAME --continue" >> ~/.bashrc

		# Enable raspi-config to auto-login to console, so the --continue line
		# above actually gets a chance to run without manual intervention.
		sudo raspi-config nonint do_boot_behaviour B2

		# This script will automatically be started after reboot
		echo -e "${YELLOW}Reboot required: Installation will continue after reboot${NC}"
		sleep 3 # Flush output to logfile
		sudo reboot
	fi

########## INITIAL RUN END ##########

else # Execute if this script IS automatically started after reboot

########## REBOOT RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Continueing after reboot${NC}"

	# Restore normal behaviour after reboot: remove the --continue line we
	# added to ~/.bashrc before rebooting, and turn auto-login back off. No
	# sudo needed here either, for the same reason as above.
	sed -i "\#^bash $SCRIPT_PATH/$SCRIPT_NAME --continue\$#d" ~/.bashrc

	# Disable raspi-config to auto-login to console
	sudo raspi-config nonint do_boot_behaviour B1

########## REBOOT RUN END ##########

fi

########## CONFIGURATION BEGIN ##########

# Minimize Oradio boot time
bash "$RESOURCES_PATH/optimize_boot_time.sh"

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country NL		# Implicitly activates wifi

# Change hostname and hosts mapping to reflect the network domain name.
# Split into two plain sudo commands instead of one `sudo bash -c "... && ..."`
# chain — neither command needs a shell beyond what sudo already gives it, and
# this way a failure in one is attributable without guessing which half of the
# chain broke.
sudo hostnamectl set-hostname "$HOSTNAME"
sudo sed -i "s/^127.0.1.1.*/127.0.1.1\t${HOSTNAME}/g" /etc/hosts

# Set Top Level Domain (TLD) to 'local', enabling access via http://oradio.local
sudo sed -i "s/^.domain-name=.*/domain-name=local/g" /etc/avahi/avahi-daemon.conf

# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf

# Progress report
echo -e "${GREEN}Wifi is enabled and network domain is set to '${HOSTNAME}.local'${NC}"

# Comment any active AcceptEnv lines in main config
sudo sed -Ei '/^[[:space:]]*AcceptEnv/ s/^[[:space:]]*/#/' /etc/ssh/sshd_config
# reload sshd with changed config
sudo systemctl reload ssh
# Set safe system-wide defaults
sudo update-locale LANG=C.UTF-8 LC_CTYPE=C.UTF-8
# Progress report
echo -e "${GREEN}Fix installed for \"-bash: warning: setlocale ...\" when SSH-ing from macOS${NC}"

# Get date and time of git last update
gitdate=$(git log -1 --format=%cd --date=format:'%Y-%m-%d-%H-%M-%S')
# Get info about installed Oradio3 version. Prefer an exact tag; fall back to
# "branch @ short-hash" if this checkout isn't on a tag. `--show-current`
# (rather than parsing `git branch`'s `* ` marker) is used so this keeps
# working even if the repo ever has many local branches.
gitinfo="Release '$(git describe --tags 2>&1)'"
if [ $? -gt 0 ]; then
	gitinfo="Branch '$(git branch --show-current)' @ $(git log --pretty='format:%h' -1)"
fi
# Generate new sw version info
sudo bash -c 'cat << EOL > /var/log/oradio_sw_version.log
{
    "serial": "$1",
    "gitinfo": "$2"
}
EOL' -- "$gitdate" "$gitinfo"
# Progress report
echo -e "${GREEN}Oradio software version log configured${NC}"

# Show Raspberry Pi serial number and SW version on login
if ! grep -q "Serial number: " /etc/bash.bashrc; then
	sudo bash -c 'cat << EOL >> /etc/bash.bashrc 
echo "--------------------------------------------------"
# Get Oradio3 serial number and software version
echo "Serial number: \$(vcgencmd otp_dump | grep "28:" | cut -c 4-)"
if [ -f /var/log/oradio_sw_version.log ]; then
	echo "SW version: \$(cat /var/log/oradio_sw_version.log | jq -r ".gitinfo")"
else
	echo "SW version: Unknown (No '"'"'oradio_sw_version.log'"'"')"
fi
echo "--------------------------------------------------"
EOL'
fi

# Install udev rules triggering when inserting/removing ORADIO USB drive
install_resource "$RESOURCES_PATH/99-local.rules" /etc/udev/rules.d/99-local.rules
# Configure the USB service triggered by udev rules
install_resource "$RESOURCES_PATH/usb-drive@.service" /etc/systemd/system/usb-drive@.service
# Configure the USB mount/unmount script used by the system service
install_resource "$RESOURCES_PATH/usb-drive.sh" /usr/local/bin/usb-drive.sh 'chmod +x /usr/local/bin/usb-drive.sh'
# Progress report
echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'${NC}"

# Activate i2c interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#i2c-nonint
sudo raspi-config nonint do_i2c 0	# 0: enable
# Install i2c modules
install_resource "$RESOURCES_PATH/modules" /etc/modules
# Progress report
echo -e "${GREEN}i2c installed and configured${NC}"

# Install audio configuration, set volume to reasonable level, play silence to activate
install_resource "$RESOURCES_PATH/asound.conf" /etc/asound.conf \
	'amixer -c 0 cset name="Digital Playback Volume" 120'\
	'aplay -D SpotCon_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1' \
	'aplay -D MPD_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1' \
	'aplay -D SysSound_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1'
# Configure MPD
install_resource "$RESOURCES_PATH/mpd.conf" /etc/mpd.conf
# Install empty MPD database (prevents MPD updating when starting)
install_resource "$RESOURCES_PATH/mpd.database" /var/lib/mpd/tag_cache
# Configure the MPD service to start on boot
install_resource "$RESOURCES_PATH/mpd.service" /lib/systemd/system/mpd.service 'systemctl enable mpd.service'
# Progress report
echo -e "${GREEN}Audio installed and configured${NC}"

# Setup log file rotation to limit logfile size
install_resource "$RESOURCES_PATH/logrotate.conf" /etc/logrotate.d/oradio
# Progress report
echo -e "${GREEN}Log files rotation configured${NC}"

# Configure Spotify connect
# Ensure logfile exists with correct ownership and permissions before starting librespot
touch "$LOGFILE_SPOTIFY"
# Ensure Spotify directory and flag files exist with default '0' and correct ownership and permissions
mkdir -p "$SPOTIFY_PATH" || { echo -e "${RED}Aborting: Failed to create directory $SPOTIFY_PATH${NC}"; exit 1; }
for flag in spotactive.flag spotplaying.flag; do
	file="$SPOTIFY_PATH/$flag"
	if [ ! -f "$file" ]; then
		echo "0" >"$file" || { echo -e "${RED}Aborting: Failed to write $file${NC}"; exit 1; }
	fi
done
# install librespot event handler script
install_resource "$RESOURCES_PATH/spotify_event_handler.sh" /usr/local/bin/spotify_event_handler.sh 'chmod +x /usr/local/bin/spotify_event_handler.sh'
# Configure the Librespot service to start on boot
install_resource "$RESOURCES_PATH/librespot.service" /etc/systemd/system/librespot.service 'systemctl enable librespot.service'
# Progress report
echo -e "${GREEN}Spotify connect functionality is installed and configured${NC}"

# Install the send_log_files_to_rms script
install_resource "$RESOURCES_PATH/send_log_files_to_rms.sh" /usr/local/bin/send_log_files_to_rms.sh 'chmod +x /usr/local/bin/send_log_files_to_rms.sh'
# Install the about script
install_resource "$RESOURCES_PATH/about" /usr/local/bin/about 'chmod +x /usr/local/bin/about'
# Progress report
echo -e "${GREEN}Support tools installed${NC}"

# Configure the power-save (USB low idle power) service to start on boot
install_resource "$RESOURCES_PATH/usb_low_idle_power.service" /etc/systemd/system/usb_low_idle_power.service 'systemctl enable usb_low_idle_power.service'
# Progress report
echo -e "${GREEN}Power save features configured${NC}"

# Configure the oradio service to start on boot
install_resource "$RESOURCES_PATH/oradio.service" /etc/systemd/system/oradio.service 'systemctl enable oradio.service'
# Progress report
echo -e "${GREEN}Start Oradio3 on boot configured${NC}"

# Stop if any installation failed. Checked once, here, rather than exiting
# immediately at each failure point above, so a single bad resource/package
# doesn't prevent the rest of a mostly-good install from completing — the
# operator gets one clear summary of everything that went wrong instead of
# having to re-run the script repeatedly to discover problems one at a time.
if [ -v INSTALL_ERROR ]; then
	echo -e "${RED}Aborting: Installation completed with errors${NC}"
	# Stop with error flag
	exit 1
fi

########## CONFIGURATION END ##########

# Progress report
echo -e "${GREEN}Installation completed. Rebooting to start Oradio3${NC}"
sleep 3

# Reboot to start Oradio3
sudo reboot
