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
echo "Install and configure audio"

# Install audio packages
sudo apt-get install mpd mpc -y

# Install the audio configuration
sudo cp $SCRIPT_DIR/audio/asound.conf /etc/asound.conf

# Configure mpd music library location
replace=`echo $USB_MUSIC | sed 's/\//\\\\\//g'`
sudo cat $SCRIPT_DIR/audio/mpd.conf.template | sed "s/USB_MUSIC/$replace/g" > $SCRIPT_DIR/audio/mpd.conf
sudo cp $SCRIPT_DIR/audio/mpd.conf /etc/mpd.conf

# Set mpd system to start at boot
sudo systemctl enable mpd.service

# Start mpd system now
sudo systemctl start mpd.service

# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
sudo systemctl daemon-reload

# Install python modules
python -m pip install python-mpd2

# Notify leaving module installation script
echo -e "${GREEN}Audio installed and configured${NC}"
