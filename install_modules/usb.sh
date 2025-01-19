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
# @copyright:     Oradio Stichting
# @license:       GNU General Public License (GPL)
# @organization:  Oradio Stichting
# @version:       1
# @email:         oradioinfo@stichtingoradio.nl
# @status:        Development

# The script uses bash constructs and changes the environment
if [ ! "$BASH_VERSION" ] || [ ! "$0" == "-bash" ]; then
	echo "Use 'source $0' to run this script" 1>&2
	exit 1
fi

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

########## Setup usb automount ##########
# The shell script doing the heavy lifting
sudo cp $PWD/install_modules/usb/usb-mount.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/usb-mount.sh
# The script, in turn, is called by a systemd unit file. The "@" filename syntax allows passing the device name as an argument.
sudo cp $PWD/install_modules/usb/usb-mount@.service /etc/systemd/system/
# Restart the daemon to activate
sudo systemctl daemon-reload
# udev rules start and stop the systemd unit service on hotplug/unplug
sudo cp $PWD/install_modules/usb/99-local.rules /etc/udev/rules.d/
# Reload to activate
sudo udevadm control --reload-rules

# Install python modules
python -m pip install usb-monitor

echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'.${NC}"
