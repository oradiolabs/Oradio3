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
echo "Load and configure spotify connect functionalty"

########## Get packages and python modules for spotify connect ##########
echo "install git"
sudo apt install -y git

######### librespot() ####################################################
echo "install raspotify which also install the librespot"
sudo apt-get -y install curl && curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
echo "==> stop/disable raspotify service, we only need librespot"
sudo systemctl stop raspotify
sudo systemctl disable raspotify

echo "install the latest version librespot from github repo"
python -m pip install git+https://github.com/kokarare1212/librespot-python

echo "install avahi-browse tool"
sudo apt -y install avahi-utils

echo "install pydantic"
python -m pip install pydantic

echo "install mpv and its libraries"
python -m pip install mpv
sudo apt -y install mpv libmpv-dev python3-mpv mpv-mpris

echo "install dbus-python"
python -m pip install dbus-python

# also install pydantic in non-venv environment
echo "deactivate current virtual machine"

INSTALL_DIR='/home/pi/Oradio3/install_modules'

deactivate
sudo python -m pip install --break-system-packages pydantic
echo "activate virtual machine again"
source /home/pi/.venv/bin/activate
echo "copy the librespot service to /etc/systemd/system"
sudo cp $INSTALL_DIR/spotify_connect/librespot.service /etc/systemd/system
echo "copy the configuration file mpv.conf to /etc/mpv"
sudo cp $INSTALL_DIR/spotify_connect/mpv.conf /etc/mpv/mpv.conf
sudo cp $INSTALL_DIR/spotify_connect/mpv.service /etc/systemd/system
sudo systemctl enable mpv.service
sudo systemctl start mpv.service
echo "create a audio pipe between librespot and mpv player"
SPOTIFY_DIR="/home/pi/spotify"
SPOTIFY_PIPE="/home/pi/spotify/librespot-pipe"
if ! [ -d "$SPOTIFY_DIR" ];
then
	mkdir $SPOTIFY_DIR
fi
if ! [[] -e "$SPOTIFY_PIPE" ];
then
	mkfifo $SPOTIFY_PIPE
	chmod 666 $SPOTIFY_PIPE
fi
# take care that librespot_event_handler.py has execute rights
chmod +x /home/pi/Oradio3/Python/librespot_event_handler.py
if systemctl is-active -q avahi-daemon.service;
then
	echo "avahi-daemon.service is active"
    echo -e "${GREEN}avahi-daemon service is active${NC} "	
else
	echo "start the avahi-daemon"
	sudo systemctl start avahi-daemon
fi

if ! systemctl is-active -q avahi-daemon.service;
then
    echo -e "${RED}avahi-daemon still not active${NC}"
    return
fi

echo "enable and start the librespot service"
sudo systemctl enable librespot
sudo systemctl restart librespot

if systemctl is-active -q librespot.service;
then
	echo "librespot.service is active"
    echo -e "${GREEN}Librespot service is active${NC} "	
else
	echo "librespot.service is not active"
    echo -e "${RED}Librespot service is not active${NC}"
    return	
fi

## check if asound.conf contains the correct spotify audio device ####
echo "check asound.conf for Spotify Sound Device"
if ! grep -q "$SPOTIFY_SOUND_DEVICE" /etc/asound.conf;
then
     echo -e "${RED}The asound.conf has no audio device $SPOTIFY_SOUND_DEVICE for Spotify Connect${NC} "
     return
fi

############################ end ######################################################################

# Notify leaving module installation script
echo -e "${GREEN}Spotify connect functionality is loaded and configured${NC}"
