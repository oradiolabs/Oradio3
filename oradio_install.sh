#!/bin/bash
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

# Initialize flag for calling scripts to test on
RETURN=0

# The script uses bash constructs and changes the environment
if [ ! "$BASH_VERSION" ] || [ ! "$0" == "-bash" ]; then
	echo -e "${RED}Use 'source $0' to run this script${NC}" 1>&2
	# Stop with error flag
	RETURN=1
	return
fi

# Get the path where the script is running
SCRIPT_PATH=$( cd -- "$( dirname -- "${BASH_SOURCE}" )" &> /dev/null && pwd )

# Location of Python files
PYTHON_PATH=$SCRIPT_PATH/Python
# Location of log files
LOGGING_PATH=$SCRIPT_PATH/logging
# Spotify directory
SPOTIFY_PATH=$SCRIPT_PATH/Spotify
# Location of files to install
RESOURCES_PATH=$SCRIPT_PATH/install_resources

# Ensure required directories exist
mkdir -p $LOGGING_PATH
mkdir -p $SPOTIFY_PATH

# Define log files
LOGFILE_USB=$LOGGING_PATH/usb.log
LOGFILE_SPOTIFY=$LOGGING_PATH/spotify.log
LOGFILE_INSTALL=$LOGGING_PATH/install.log
LOGFILE_TRACEBACK=$LOGGING_PATH/traceback.log

# Redirect script output to console and file
exec > >(tee -a $LOGFILE_INSTALL) 2>&1

# When leaving this script stop redirection and wait until redirect process has finished
trap 'exec > /dev/tty 2>&1; wait' RETURN

# Script is for Bookworm 64bit Lite
BOOKWORM64="Debian GNU/Linux 12 (bookworm)"
OSVERSION=$(lsb_release -a | grep "Description:" | cut -d$'\t' -f2)
if [ "$OSVERSION" != "$BOOKWORM64" ]; then
	echo -e "${RED}Unsupported OS version: $OSVERSION${NC}"
	# Stop with error flag
	RETURN=1
	return
fi

# Network domain name
HOSTNAME="oradio"

# Clear flag indicating reboot required to complete the installation
unset REBOOT_AND_CONTINUE

# Clear flag indicating installation error
unset INSTALL_ERROR

# Install file and run follow-up commnand
function install_resource {
	if [ $# -lt 2 ]; then
		echo -e "${RED}install_resource has too few arguments: '$@'${NC}"
		# Stop with error flag
		INSTALL_ERROR=1
	else

		if [ -f $1.template ]; then
			# Create by replacing placeholders
			cp $1.template $1

			sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $1
			sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" $1

			replace=`echo $PYTHON_PATH | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_PYTHON_PATH/$replace/g" $1
			replace=`echo $SPOTIFY_PATH | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_SPOTIFY_PATH/$replace/g" $1

			replace=`echo $LOGFILE_USB | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_LOGFILE_USB/$replace/g" $1
			replace=`echo $LOGFILE_SPOTIFY | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_LOGFILE_SPOTIFY/$replace/g" $1
			replace=`echo $LOGFILE_INSTALL | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_LOGFILE_INSTALL/$replace/g" $1
			replace=`echo $LOGFILE_TRACEBACK | sed 's/\//\\\\\//g'`
			sed -i "s/PLACEHOLDER_LOGFILE_TRACEBACK/$replace/g" $1
		fi

		if ! sudo diff $1 $2 >/dev/null 2>&1; then
			# Install the configuration
			sudo cp $1 $2

			# Execute any extra arguments
			while [ $# -gt 2 ]; do
				# Execute argument
				eval $3
				# Remove first argument from list
				shift
			done
		fi
	fi
}

########## INITIALIZE END ##########

if ! [ -f $HOME/.bashrc.backup ]; then # Execute if this script is NOT automatically started after reboot

########## INITIAL RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Starting '$BASH_SOURCE'${NC}"

########## OS PACKAGES BEGIN ##########

	# Ensure linux packages are up to date
	# https://www.raspberrypi.com/documentation/computers/os.html#update-software

	if ! $(sudo apt update 2>/dev/null | grep -q "All packages are up to date"); then
		# Upgrade packages to the latest greatest
		sudo apt -y full-upgrade

		# Remove obsolete packages and their configuration files
		sudo apt -y autoremove

		# Delete any lingering package files
		sudo apt -y clean

		# Check if reboot is needed to activate changes
		if [ -f /var/run/reboot-required ]; then
			echo -e "${YELLOW}Reboot requested for activating package updates${NC}"
			REBOOT_AND_CONTINUE=1
		fi
	fi

	# Progress report
	echo -e "${GREEN}OS packages up to date${NC}"

########## OS PACKAGES END ##########

########## ORADIO3 PACKAGES BEGIN ##########

	# Install packages if not yet installed
#***************************************************************#
#   Add any additionally required packages to 'PACKAGES'        #
#***************************************************************#
	PACKAGES="jq python3-dev libasound2-dev libasound2-plugin-equal mpd mpc iptables"
	dpkg --verify $PACKAGES >/dev/null 2>&1 || sudo apt install -y $PACKAGES

	# Progress report
	echo -e "${GREEN}Oradio3 packages installed and up to date${NC}"

########## ORADIO3 PACKAGES END ##########

########## PYTHON BEGIN ##########

	# Configure Python virtual environment
	if [ -v $VIRTUAL_ENV ]; then
		# Prepare python virtual environment
		python3 -m venv ~/.venv

		# Activate the python virtual environment in current environemnt
		source ~/.venv/bin/activate

		# Activate python virtual environment when logging in: add if not yet present
		sudo grep -qxF 'source ~/.venv/bin/activate' ~/.bashrc || echo 'source ~/.venv/bin/activate' >> ~/.bashrc
	fi

	# Progress report
	echo -e "${GREEN}Python virtual environment configured${NC}"

#OMJ: Uitzoeken welke van deze packages als python3-<xxx> package te installeren zijn en dan verplaatsen naar Oradio3 PACKAGES hierboven
	# Install python modules. On --use-pep517 see https://peps.python.org/pep-0517/
#***************************************************************#
#   Add any additionally required Python modules to 'PYTHON'    #
#***************************************************************#
	PYTHON="python-mpd2 smbus2 rpi-lgpio concurrent_log_handler requests nmcli pyalsaaudio\
			vcgencmd watchdog pydantic fastapi JinJa2 uvicorn python-multipart"
	python3 -m pip install --upgrade --use-pep517 $PYTHON

	# Progress report
	echo -e "${GREEN}Python modules installed and up to date${NC}"

########## PYTHON END ##########

########## CONFIGURATION BEGIN ##########

	# Install boot options
	install_resource $RESOURCES_PATH/config.txt /boot/firmware/config.txt 'REBOOT_AND_CONTINUE=1'

	# Progress report
	echo -e "${GREEN}Boot options configured${NC}"

########## CONFIGURATION END ##########

	# Reboot if required for activation
	if [ -v REBOOT_AND_CONTINUE ]; then
		# Configure to continue the installation after reboot
		if ! $(cat $HOME/.bashrc | grep -q $BASH_SOURCE); then
			# Backup $HOME/.bashrc
			cp $HOME/.bashrc $HOME/.bashrc.backup

# Write commands to continue installation
cat << EOL >> $HOME/.bashrc 
# Enter repository directory
cd $SCRIPT_PATH
# Continue installing Oradio3
source $BASH_SOURCE
EOL
		fi

		# Enable raspi-config to auto-login to console
		sudo raspi-config nonint do_boot_behaviour B2

		# This script will automatically be started after reboot
		echo -e "${YELLOW}Rebooting: Installation will automatically continue after reboot${NC}"
		sleep 3 # Flush output to logfile
		sudo reboot
	fi

########## INITIAL RUN END ##########

else # Execute if this script IS automatically started after reboot

########## REBOOT RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Continueing '$BASH_SOURCE'${NC}"

	# Restore normal behaviour after reboot
	mv $HOME/.bashrc.backup $HOME/.bashrc

	# Enable raspi-config to auto-login to console
	sudo raspi-config nonint do_boot_behaviour B1

########## REBOOT RUN END ##########

fi

########## CONFIGURATION BEGIN ##########

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country NL		# Implicitly activates wifi

# change hostname and hosts mapping to reflect the network domain name
sudo bash -c "hostnamectl set-hostname ${HOSTNAME} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${HOSTNAME}/g\" /etc/hosts"

# Set user prompt to reflect new hostname
export PS1=$VIRTUAL_ENV_PROMPT"\e[01;32m\u@$HOSTNAME\e[00m:\e[01;34m\w \$\e[00m "

# Set Top Level Domain (TLD) to 'local', enabling access via http://oradio.local
sudo sed -i "s/^.domain-name=.*/domain-name=local/g" /etc/avahi/avahi-daemon.conf

# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf

# Progress report
echo -e "${GREEN}Wifi is enabled and network domain is set to '${HOSTNAME}.local'${NC}"

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

# Show Raspberry Pi serial number on login
if ! grep -q "Serial number: " /etc/bash.bashrc; then
	# Get Oradio3 serial number
	serial=$(vcgencmd otp_dump | grep "28:" | cut -c 4-)
	sudo bash -c 'cat << EOL >> /etc/bash.bashrc 
# Show Oradio3 serial number on login
echo "--------------------------------------------------"
echo "Serial number: $1"
echo "SW version: \$(cat /var/log/oradio_sw_version.log | jq -r ".gitinfo")"
echo "--------------------------------------------------"
EOL' -- "$serial"
fi

# Configure the USB mount script
install_resource $RESOURCES_PATH/usb-mount.sh /usr/local/bin/usb-mount.sh 'sudo chmod +x /usr/local/bin/usb-mount.sh'

# Mount USB if present but not mounted
if [ ! -f /media/usb_ready ]; then
	# Mount USB partition if present
	for filename in /dev/sda[1-9]; do
		if [ -b "$filename" ]; then
			sudo bash /usr/local/bin/usb-mount.sh add $(basename $filename)
		fi
	done
fi

# Check for USB mount errors and/or warnings
if [ -f $LOGFILE_USB ]; then
	MESSAGE_USB=$(cat $LOGFILE_USB | grep "Error")
	if [ $? -eq 0 ]; then
		echo -e "${RED}Problem mounting USB: $MESSAGE_USB${NC}"
	fi
	MESSAGE_USB=$(cat $LOGFILE_USB | grep "Warning")
	if [ $? -eq 0 ]; then
		echo -e "${YELLOW}Problem mounting USB: $MESSAGE_USB${NC}"
	fi
fi

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

# Configure the hw_version service
if [ ! -f /var/log/oradio_hw_version.log ]; then
	install_resource $RESOURCES_PATH/hw_version.service /etc/systemd/system/hw_version.service 'sudo systemctl enable hw_version.service'
fi
# Progress report
echo -e "${GREEN}Oradio3 hardware version log configured${NC}"

# Configure the backlighting service
install_resource $RESOURCES_PATH/backlighting.service /etc/systemd/system/backlighting.service 'sudo systemctl enable backlighting.service'
# Progress report
echo -e "${GREEN}Backlighting installed and configured${NC}"

# Install equalizer settings with rw rights
install_resource $RESOURCES_PATH/alsaequal.bin /etc/alsaequal.bin 'sudo chmod 666 /etc/alsaequal.bin'
# Install audio configuration, activate SoftVolSpotCon, set volume to normal level
# NOTE: Requires the Oradio3 boot config to be installed and activate
install_resource $RESOURCES_PATH/asound.conf /etc/asound.conf \
		'speaker-test -D SoftVolSpotCon1 -c2 >/dev/null 2>&1' \
		'speaker-test -D SoftVolSysSound -c2 >/dev/null 2>&1' \
		'speaker-test -D SoftVolMPD -c2 >/dev/null 2>&1' \
		'amixer -c 0 cset name="Digital Playback Volume" 120'
# Configure mpd music library location and start service at boot
install_resource $RESOURCES_PATH/mpd.conf /etc/mpd.conf 'sudo systemctl enable mpd.service'
# Progress report
echo -e "${GREEN}Audio installed and configured${NC}"

# Setup log file rotation to limit logfile size
install_resource $RESOURCES_PATH/logrotate.conf /etc/logrotate.d/oradio
# Progress report
echo -e "${GREEN}Log files rotation configured${NC}"

# Configure Spotify connect
install_resource $RESOURCES_PATH/spotify_event_handler.sh /usr/local/bin/spotify_event_handler.sh 'sudo chmod +x /usr/local/bin/spotify_event_handler.sh'

# Install raspotify which also install the librespot
#OMJ: Is er een manier om te checken of de laatste versie van librespot al geinstalleerd is?
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh

# stop and disable raspotify service, we only need librespot
sudo systemctl stop raspotify
sudo systemctl disable raspotify

# Configure the Librespot service
install_resource $RESOURCES_PATH/librespot.service /etc/systemd/system/librespot.service 'sudo systemctl enable librespot.service'

# Progress report
echo -e "${GREEN}Spotify connect functionality is installed and configured${NC}"

# Configure the autostart service
install_resource $RESOURCES_PATH/autostart.service /etc/systemd/system/autostart.service 'sudo systemctl enable autostart.service'
# Progress report
echo -e "${GREEN}Autostart Oradio3 on boot configured${NC}"

# Stop if any installation failed
if [ -v INSTALL_ERROR ]; then
	echo -e "${RED}Installation completed with errors${NC}"
	# Stop with error flag
	RETURN=1
	return
fi

########## CONFIGURATION END ##########

# Progress report
echo -e "${GREEN}Installation completed. Rebooting to start Oradio3${NC}"
# Reboot to start Oradio3
sleep 3 && sudo reboot
