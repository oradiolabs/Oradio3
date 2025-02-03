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
echo "Configure Oradio autostart on boot"

########## Configure and install service ##########
# Configure the autostart service
cp $MODULES/autostart/autostart.service.template $MODULES/autostart/autostart.service
replace=`echo $PYTHON | sed 's/\//\\\\\//g'`
sudo sed -i "s/SCRIPT_PATH/$replace/g" $MODULES/autostart/autostart.service
sudo sed -i "s/USER/$USER/g" $MODULES/autostart/autostart.service

# Install the autostart service
sudo cp $MODULES/autostart/autostart.service /etc/systemd/system/

# Set autostart system to start at boot
sudo systemctl enable autostart.service

# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
sudo systemctl daemon-reload

# Notify leaving module installation script
echo -e "${GREEN}Autostart Oradio on boot configured${NC}"
