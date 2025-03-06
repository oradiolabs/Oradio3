#!/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on January 30, 2025
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
	return 1
fi

# In case the script is executed stand-alone
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Load shared constants
if [ ! -f $SCRIPT_DIR/constants.sh ]; then
	echo "constants.sh not found"
	return 1
fi
source $SCRIPT_DIR/constants.sh

# Notify entering module installation script
echo "Configure Oradio hardware version log on boot"

# Configure the hw_version service
if [ ! -f /var/log/oradio_hw_version.log ]; then

	cp $SCRIPT_DIR/hw_version/hw_version.service.template $SCRIPT_DIR/hw_version/hw_version.service
	sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $SCRIPT_DIR/hw_version/hw_version.service
	replace=`echo $(realpath "$SCRIPT_DIR/../Python") | sed 's/\//\\\\\//g'`
	sed -i "s/PLACEHOLDER_PATH/$replace/g" $SCRIPT_DIR/hw_version/hw_version.service

	# Install the hw_version service
	sudo cp $SCRIPT_DIR/hw_version/hw_version.service /etc/systemd/system/
	echo 'script installed'

	# Set hw_version system to start at boot
	sudo systemctl enable hw_version.service
	echo 'service enabled'

	# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
	sudo systemctl daemon-reload
	echo 'daemon reloaded'
fi

# Notify leaving module installation script
echo -e "${GREEN}Oradio hardware version log on boot configured${NC}"
