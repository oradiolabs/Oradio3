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
echo "Install and configure backlighting"

# Configure the backlighting service
SRC=$SCRIPT_DIR/backlighting/backlighting.service
DST=/etc/systemd/system/backlighting.service
cp $SRC.template $SRC
sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $SRC
sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" $SRC
replace=`echo $(realpath "$SCRIPT_DIR/../Python") | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_PATH/$replace/g" $SRC

if ! sudo diff $SRC $DST >/dev/null 2>&1; then

	# Install the backlighting service
	sudo cp $SRC $DST

	# Set backlighting system to start at boot
	sudo systemctl enable backlighting.service

	# Start backlighting system now
	sudo systemctl start backlighting.service

	# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
	sudo systemctl daemon-reload

fi

# Notify leaving module installation script
echo -e "${GREEN}Backlighting installed and configured${NC}"
