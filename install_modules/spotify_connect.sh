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

# Create Spotify directory
SPOTIFY_DIR=$(realpath "$SCRIPT_DIR/../Spotify")
mkdir -p $SPOTIFY_DIR

# Get absolute path to USB logfile
LOGFILE_SPOTIFY=$(realpath "$SCRIPT_DIR/../$LOG_DIR/oradio_spotify.log")

# Configure the Spotify script
SRC=$SCRIPT_DIR/spotify_connect/spotify_event_handler.sh
DST=/usr/local/bin/spotify_event_handler.sh
cp $SRC.template $SRC
replace=`echo $SPOTIFY_DIR | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_SPOTIFY_DIR/$replace/g" $SRC
replace=`echo $LOGFILE_SPOTIFY | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE_SPOTIFY/$replace/g" $SRC
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# Install the USB mount script
	sudo cp $SRC $DST
	sudo chmod +x $DST
fi

#OMJ: Is er een manier om te checken of de laatste versie van librespot al geinstalleerd is?
# Install raspotify which also install the librespot
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh

# stop and disable raspotify service, we only need librespot
sudo systemctl stop raspotify
sudo systemctl disable raspotify

#OMJ: Vanwege problemen bij de integratie van gebruik vna de Oradio knoppen draaien we in eerste instatie alleen de Librespot service
#OMJ: Start comment-out
if false; then

	echo "install the latest version librespot from github repo"
	#OMJ: door optie '--use-pep517' toe te voegen addresseer je een deprecated message
	#python -m pip install git+https://github.com/kokarare1212/librespot-python
	python -m pip install git+https://github.com/kokarare1212/librespot-python --use-pep517

	echo "install avahi-browse tool"
	sudo apt -y install avahi-utils

	echo "install pydantic"
	# pydantic wordt ook geinstalleerd in web_service. Dat lost zich vanzelf op als we de install scripts consolideren, issue #101
	python -m pip install pydantic

	echo "install mpv and its libraries"
	python -m pip install mpv
	sudo apt -y install mpv libmpv-dev python3-mpv mpv-mpris

	echo "install dbus-python"
	python -m pip install dbus-python

	# also install pydantic in non-venv environment
	echo "deactivate current virtual machine"

	deactivate
	sudo python -m pip install --break-system-packages pydantic
	echo "activate virtual machine again"
	#OMJ: pas op met absolute paden
	#source /home/pi/.venv/bin/activate
	source $HOME/.venv/bin/activate

	echo "copy the configuration file mpv.conf to /etc/mpv"
	sudo cp $SCRIPT_DIR/spotify_connect/mpv.conf /etc/mpv/mpv.conf
	sudo cp $SCRIPT_DIR/spotify_connect/mpv.service /etc/systemd/system
	sudo systemctl enable mpv.service
	sudo systemctl start mpv.service
	echo "create a audio pipe between librespot and mpv player"
	#OMJ: pas op met absolute paden
	#SPOTIFY_DIR="/home/pi/spotify"
	#SPOTIFY_PIPE="/home/pi/spotify/librespot-pipe"
	SPOTIFY_DIR=$HOME"/spotify"
	SPOTIFY_PIPE=$HOME"/spotify/librespot-pipe"
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
	#OMJ: pas op met absolute paden
	chmod +x $SCRIPT_DIR/../Python/librespot_event_handler.py
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

fi
#OMJ: Einde comment-out

# Configure the Librespot service
SRC=$SCRIPT_DIR/spotify_connect/librespot.service
DST=/etc/systemd/system/librespot.service
cp $SRC.template $SRC
replace=`echo $SPOTIFY_DIR | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_SPOTIFY_DIR/$replace/g" $SRC
replace=`echo $LOGFILE_SPOTIFY | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE_SPOTIFY/$replace/g" $SRC
sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $SRC
sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" $SRC

# Install service if new or changed
if ! sudo diff $SRC $DST >/dev/null 2>&1; then

	# Install the service
	sudo cp $SRC $DST

	# Set service to start at boot
	sudo systemctl enable $(basename $DST)

	# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
	sudo systemctl daemon-reload

	# Start the service
	sudo systemctl restart  $(basename $DST)
fi

if ! systemctl is-active -q librespot.service; then
    echo -e "${RED}Librespot service is not active${NC}"
    return
fi

#OMJ: Er zijn meer logfiles die rotated moeten worden: consolideren
# Setup log file rotation to limit logfile size
SRC=$SCRIPT_DIR/spotify_connect/logrotate.conf
DST=/etc/logrotate.d/spotify
cp $SRC.template $SRC
replace=`echo $LOGFILE_SPOTIFY | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE_SPOTIFY/$replace/g" $SRC
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# Install the oradio logrotate configuration file
	sudo cp $SRC $DST
fi

# Notify leaving module installation script
echo -e "${GREEN}Spotify connect functionality is loaded and configured${NC}"
