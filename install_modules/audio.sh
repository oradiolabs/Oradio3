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

# Install packages if not yet installed
dpkg --verify mpd >/dev/null 2>&1 || sudo apt-get install -y mpd
dpkg --verify mpc >/dev/null 2>&1 || sudo apt-get install -y mpc

###########Equal ##############

#equalizer package
sudo apt install libasound2-plugin-equal -y

# Install the audio configuration
SRC=$SCRIPT_DIR/audio/asound.conf
DST=/etc/asound.conf
if ! sudo diff $SRC $DST >/dev/null 2>&1; then

	# Install the asound configuration
	sudo cp $SRC $DST

	# Work-around to activate SoftVolSpotCon
	speaker-test -D SoftVolSpotCon1 -c2 >/dev/null 2>&1
        # added another 2 to activate
        speaker-test -D SoftVolSpotCon1 -c2 >/dev/null 2>&1
        speaker-test -D SoftVolSysSound -c2 >/dev/null 2>&1

fi

# install equalizer settings
SRC=$SCRIPT_DIR/audio/alsaequal.bin
DST=/etc/alsaequal.bin
if ! sudo diff $SRC $DST >/dev/null 2>&1; then

        # Install the alsaequal settings
        sudo cp $SRC $DST


fi
# give the mpd user permissions to rw alsaequal.bin
sudo chmod 666 $DST

###########Equal end ##############

# Configure mpd music library location
SRC=$SCRIPT_DIR/audio/mpd.conf
DST=/etc/mpd.conf
cp $SRC.template $SRC
replace=`echo $USB_MUSIC | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_USB_MUSIC/$replace/g" $SRC
replace=`echo $USB_PLAYLISTS | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_USB_PLAYLISTS/$replace/g" $SRC

if ! sudo diff $SRC $DST >/dev/null 2>&1; then

	# Install the mpd configuration
	sudo cp $SRC $DST

	# Set mpd system to start at boot
	sudo systemctl enable mpd.service

	# Start mpd system now
	sudo systemctl start mpd.service

	# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
	sudo systemctl daemon-reload
fi

# Check for Python environment
if [ -v $VIRTUAL_ENV ]; then
	echo -e "${RED}Python not configured.${NC}"
	return 1
fi

# Install python modules or upgrade if need be
pip install --upgrade python-mpd2

# Notify leaving module installation script
echo -e "${GREEN}Audio installed and configured${NC}"
