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

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

# Get the script path and name
SCRIPT_PATH=$HOME/Oradio3
SCRIPT_NAME=$(basename $BASH_SOURCE)

# Working directory
cd $SCRIPT_PATH

# Location of Oradio3 program
MAIN_PATH=$SCRIPT_PATH/Main
# Location of log files
LOGGING_PATH=$SCRIPT_PATH/logging
# Spotify directory
SPOTIFY_PATH=$SCRIPT_PATH/Spotify
# Location of files to install
RESOURCES_PATH=$SCRIPT_PATH/install_resources

# Constant where Python looks for importing
PYTHONPATH=$MAIN_PATH:$SCRIPT_PATH/.github/workflows

# Ensure logging directory exists
mkdir -p "$LOGGING_PATH" || { echo -e "${RED}Failed to create directory $LOGGING_PATH${NC}"; exit 1; }

# Define log files
LOGFILE_USB=$LOGGING_PATH/usb.log
LOGFILE_MPD=$LOGGING_PATH/mpd.log
LOGFILE_SPOTIFY=$LOGGING_PATH/spotify.log
LOGFILE_INSTALL=$LOGGING_PATH/install.log
LOGFILE_TRACEBACK=$LOGGING_PATH/traceback.log

# Redirect script output to console and file
exec > >(tee -a $LOGFILE_INSTALL) 2>&1

# When leaving this script stop redirection and wait until redirect process has finished
trap 'exec > /dev/tty 2>&1; wait' EXIT

# Script is for Bookworm 64bit Lite
TARGETOS="Debian GNU/Linux 13 (trixie)"
OSVERSION=$(lsb_release -a | grep "Description:" | cut -d$'\t' -f2)
if [ "$OSVERSION" != "$TARGETOS" ]; then
	echo -e "${RED}Invalid OS version: $OSVERSION${NC}"
	# Stop with error flag
	exit 1
fi

# Network domain name
HOSTNAME="oradio"

# Clear flag indicating reboot required to complete the installation
unset REBOOT_NEEDED

# Clear flag indicating installation error
unset INSTALL_ERROR

# Install file replacing placeholders and execute follow-up commands
function install_resource {
    if [ $# -lt 2 ]; then
        echo -e "${RED}install_resource has too few arguments: '$@'${NC}"
		echo "Usage: $0 src dst"
		# Stop with error flag
        INSTALL_ERROR=1
        return
    fi

	SRC=$1
	DST=$2
	shift 2

    if [ -f "$SRC.template" ]; then

		# Create by replacing placeholders
        cp "$SRC.template" "$SRC"

		# Replace placeholders
        sed -i "s/PLACEHOLDER_USER/$(id -un)/g" "$SRC"
        sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" "$SRC"
		for VAR_NAME in MAIN_PATH SPOTIFY_PATH LOGGING_PATH LOGFILE_USB LOGFILE_MPD LOGFILE_INSTALL LOGFILE_SPOTIFY LOGFILE_TRACEBACK; do
			VALUE="${!VAR_NAME}"
			# Escape & because sed treats it specially
			ESCAPED_VALUE=$(echo "$VALUE" | sed 's/[&]/\\&/g')
			PLACEHOLDER="PLACEHOLDER_${VAR_NAME}"
			# Use | as delimiter instead of /
			sed -i "s|$PLACEHOLDER|$ESCAPED_VALUE|g" "$SRC"
		done
    fi

    # Install only if files differ
    if ! cmp -s "$SRC" "$DST"; then
		echo "Installing '$SRC' to '$DST'"
        sudo cp "$SRC" "$DST"

        # Execute any extra commands
        for CMD in "$@"; do
			echo "Executing: '$CMD'"
            sudo bash -c "$CMD"
        done
    fi
}

########## INITIALIZE END ##########

if [ "$1" != "--continue" ]; then

########## INITIAL RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Starting '$SCRIPT_NAME'${NC}"

########## OS PACKAGES BEGIN ##########

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
		echo -e "${YELLOW}Package lists out of date, updating...${NC}"
		sudo apt-get update
		# Save time lists were updated
		date +%s | sudo tee "$STAMP_FILE" >/dev/null
	fi
	echo -e "${GREEN}Package lists are up to date${NC}"
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
		python3-netifaces
	)

	# Fetch list of upgradable packages
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
				sudo apt-get install -y "$package"
				REBUILD_PYTHON_ENV=1
			else
				echo "$package is up-to-date"
			fi
		else
			echo -e "${YELLOW}$package is missing: installing...${NC}"
			if [ "$package" == "raspotify" ]; then
				# raspotify needs to be configured separately
				# Install raspotify which includes librespot
				curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
				# Only keep librespot
				sudo systemctl stop raspotify
				sudo systemctl disable raspotify
			else
				sudo apt-get install -y "$package"
				REBUILD_PYTHON_ENV=1
			fi
		fi
	done

	# Progress report
	echo -e "${GREEN}Oradio3 packages installed and up to date${NC}"

########## ORADIO3 LINUX PACKAGES END ##########

########## PYTHON BEGIN ##########

	# If needed, prepare python virtual environment including system site packages
	if [ -n "${REBUILD_PYTHON_ENV:-}" ]; then
		python3 -m venv --system-site-packages ~/.venv
	fi

	# Activate the python virtual environment in current environment
	source ~/.venv/bin/activate

	# Activate python virtual environment when logging in if not yet present
	ADDTOBASHRC="source ~/.venv/bin/activate"
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}" >> ~/.bashrc

	# Set paths to python scripts if not yet present
	ADDTOBASHRC="export PYTHONPATH="${SCRIPT_PATH}:${SCRIPT_PATH}/Main:${SCRIPT_PATH}/module_test:${SCRIPT_PATH}/.github/workflows""
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}"  >> ~/.bashrc
	
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

	# Ensure Python packages are installed and up-to-date
	for package in "${PYTHON_PACKAGES[@]}"; do
		installed_version=$(python -c "
import sys
try:
    from importlib.metadata import version
except ImportError:
    from importlib_metadata import version  # For Python < 3.8 with backport
try:
    print(version('$package'))
except:
    sys.exit(1)
")
		if [ $? -ne 0 ]; then
			echo -e "${YELLOW}$package is missing: installing...${NC}"
			# On --use-pep517 see https://peps.python.org/pep-0517/
			python3 -m pip install --upgrade --use-pep517 $package
			continue
		fi

		# Get latest version from PyPI
		latest_version=$(curl -s "https://pypi.org/pypi/${package}/json" | \
			python -c "import sys, json; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null)

		if [ -z "$latest_version" ]; then
			echo -e "${ERROR} Failed to fetch version for $package from PyPI${NC}"
			exit 1
		fi

		# Compare versions
		if [ "$installed_version" != "$latest_version" ]; then
			echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
			# On --use-pep517 see https://peps.python.org/pep-0517/
			python3 -m pip install --upgrade --use-pep517 $package
		else
			echo "$package is up-to-date"
		fi
	done

	# Progress report
	echo -e "${GREEN}Python packages installed and up-to-date${NC}"

########## PYTHON END ##########

########## CONFIGURATION BEGIN ##########

	# Install boot options
	install_resource $RESOURCES_PATH/config.txt /boot/firmware/config.txt 'REBOOT_NEEDED=true'

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
		# Configure to continue the installation after reboot
		sudo grep -qxF "bash $SCRIPT_PATH/$SCRIPT_NAME --continue" ~/.bashrc || echo "bash $SCRIPT_PATH/$SCRIPT_NAME --continue" >> ~/.bashrc

		# Enable raspi-config to auto-login to console
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

	# Restore normal behaviour after reboot
	sudo sed -i "\#^bash $SCRIPT_PATH/$SCRIPT_NAME --continue\$#d" ~/.bashrc

	# Disable raspi-config to auto-login to console
	sudo raspi-config nonint do_boot_behaviour B1

########## REBOOT RUN END ##########

fi

########## CONFIGURATION BEGIN ##########

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country NL		# Implicitly activates wifi

# change hostname and hosts mapping to reflect the network domain name
sudo bash -c "hostnamectl set-hostname ${HOSTNAME} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${HOSTNAME}/g\" /etc/hosts"

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
# Get info about installed Oradio3 version
gitinfo="Release '$(git describe --tags 2>&1)'"
if [ $? -gt 0 ]; then
	gitinfo="Branch '$(git branch | cut -d' ' -f2)' @ $(git log --pretty='format:%h')"
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
        echo "SW version: Unknown (No 'oradio_sw_version.log')"
fi
echo "--------------------------------------------------"
EOL'
fi

# Ensure defined state when booting: service removes /media/usb_ready
install_resource $RESOURCES_PATH/usb-prepare.service /etc/systemd/system/usb-prepare.service 'systemctl enable usb-prepare.service'
# Configure the USB mount script
install_resource $RESOURCES_PATH/usb-mount.sh /usr/local/bin/usb-mount.sh 'chmod +x /usr/local/bin/usb-mount.sh'
# Configure the USB service
install_resource $RESOURCES_PATH/usb-mount@.service /etc/systemd/system/usb-mount@.service
# Install rules if new or changed and reload to activate
install_resource $RESOURCES_PATH/99-local.rules /etc/udev/rules.d/99-local.rules
# Progress report
echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'${NC}"

# Activate i2c interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#i2c-nonint
sudo raspi-config nonint do_i2c 0	# 0: enable
# Install i2c modules
install_resource $RESOURCES_PATH/modules /etc/modules
# Progress report
echo -e "${GREEN}i2c installed and configured${NC}"

# Install audio configuration, set volume to reasonable level, play silence to activate
install_resource $RESOURCES_PATH/asound.conf /etc/asound.conf \
	'amixer -c 0 cset name="Digital Playback Volume" 120'\
	'aplay -D SpotCon_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1' \
	'aplay -D MPD_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1' \
	'aplay -D SysSound_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1'
# Configure MPD
install_resource $RESOURCES_PATH/mpd.conf /etc/mpd.conf
# Install empty MPD database (prevents MPD updating when starting
install_resource $RESOURCES_PATH/mpd.database /var/lib/mpd/tag_cache
# Configure the MPD service to start on boot
install_resource $RESOURCES_PATH/mpd.service /lib/systemd/system/mpd.service 'systemctl enable mpd.service'
# Progress report
echo -e "${GREEN}Audio installed and configured${NC}"

# Setup log file rotation to limit logfile size
install_resource $RESOURCES_PATH/logrotate.conf /etc/logrotate.d/oradio
# Progress report
echo -e "${GREEN}Log files rotation configured${NC}"

# Configure Spotify connect
# Ensure logfile exists with correct ownership and permissions before starting librespot
touch $LOGFILE_SPOTIFY
# Ensure Spotify directory and flag files exist with default '0' and correct ownership and permissions
mkdir -p "$SPOTIFY_PATH" || { echo -e "${RED}Failed to create directory $SPOTIFY_PATH${NC}"; exit 1; }
for flag in spotactive.flag spotplaying.flag; do
	file="$SPOTIFY_PATH/$flag"
	if [ ! -f "$file" ]; then
		echo "0" >"$file" || { echo -e "${RED}Failed to write $file${NC}"; exit 1; }
#		chown "$(id -un):$(id -gn)" "$file" 2>/dev/null || { echo -e "${RED}chown failed for $file${NC}"; exit 1; }
#		chmod 644 "$file" 2>/dev/null || { echo -e "${RED}chmod failed for $file${NC}"; exit 1; }
	fi
done
# install librespot event handler script
install_resource $RESOURCES_PATH/spotify_event_handler.sh /usr/local/bin/spotify_event_handler.sh 'chmod +x /usr/local/bin/spotify_event_handler.sh'
# Configure the Librespot service to start on boot
install_resource $RESOURCES_PATH/librespot.service /etc/systemd/system/librespot.service 'systemctl enable librespot.service'
# Progress report
echo -e "${GREEN}Spotify connect functionality is installed and configured${NC}"

# Install the send_log_files_to_rms script
install_resource $RESOURCES_PATH/send_log_files_to_rms.sh /usr/local/bin/send_log_files_to_rms.sh 'chmod +x /usr/local/bin/send_log_files_to_rms.sh'
# Install the about script
install_resource $RESOURCES_PATH/about /usr/local/bin/about 'chmod +x /usr/local/bin/about'
# Progress report
echo -e "${GREEN}Support tools installed${NC}"

# Configure the oradio service to start on boot
install_resource $RESOURCES_PATH/usb_low_idle_power.service /etc/systemd/system/usb_low_idle_power.service 'systemctl enable usb_low_idle_power.service'
# Progress report
echo -e "${GREEN}Power save features configured${NC}"

# Configure the oradio service to start on boot
install_resource $RESOURCES_PATH/oradio.service /etc/systemd/system/oradio.service 'systemctl enable oradio.service'
# Progress report
echo -e "${GREEN}Start Oradio3 on boot configured${NC}"

# Stop if any installation failed
if [ -v INSTALL_ERROR ]; then
	echo -e "${RED}Installation completed with errors${NC}"
	# Stop with error flag
	exit 1
fi

########## CONFIGURATION END ##########

# Progress report
echo -e "${GREEN}Installation completed. Rebooting to start Oradio3${NC}"
sleep 3

# Reboot to start Oradio3
sudo reboot
