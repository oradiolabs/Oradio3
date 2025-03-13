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
echo "Load and configure USB functionalty"

#OMJ: Hoort bij logging.sh acties
# Create log directory
mkdir -p "$SCRIPT_DIR/../$LOG_DIR"

# Get absolute path to USB logfile
LOGFILE_USB=$(realpath "$SCRIPT_DIR/../$LOG_DIR/oradio_usb.log")

# Configure the USB mount script
SRC=$SCRIPT_DIR/usb/usb-mount.sh
DST=/usr/local/bin/usb-mount.sh
cp $SRC.template $SRC
replace=`echo $USB_MOUNT_POINT | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_MOUNT_POINT/$replace/g" $SRC
replace=`echo $USB_MONITOR | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_MONITOR/$replace/g" $SRC

# Install script if new or changed
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# Install the USB mount script
	sudo cp $SRC $DST
	sudo chmod +x $DST
fi

# Mount USB if present but not mounted
if [ ! -f $USB_MONITOR ]; then
	# Mount USB partition if present
	for filename in /dev/sda[1-9]; do
		if [ -b "$filename" ]; then
			sudo bash $DST add $(basename $filename)
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

#OMJ: Er zijn meer logfiles die rotated moeten worden: consolideren
# Setup log file rotation to limit logfile size
SRC=$SCRIPT_DIR/usb/logrotate.conf
DST=/etc/logrotate.d/usb
cp $SRC.template $SRC
replace=`echo $LOGFILE_USB | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE_USB/$replace/g" $SRC
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# Install the oradio logrotate configuration file
	sudo cp $SRC $DST
fi

# Configure the USB service
SRC=$SCRIPT_DIR/usb/usb-mount@.service
DST=/etc/systemd/system/usb-mount@.service
cp $SRC.template $SRC
replace=`echo $LOGFILE_USB | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE_USB/$replace/g" $SRC

# Install service if new or changed
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# The script is called by a systemd unit file. The "@" filename syntax allows passing the device name as an argument
	sudo cp $SRC $DST
fi

# Install rules if new or changed
SRC=$SCRIPT_DIR/usb/99-local.rules
DST=/etc/udev/rules.d/99-local.rules
if ! sudo diff $SRC $DST >/dev/null 2>&1; then
	# (Re)Install udev rules to trigger the systemd unit service on USB hotplug/unplug
	sudo cp $SCRIPT_DIR/usb/99-local.rules /etc/udev/rules.d/

	# Reload to activate
	sudo udevadm control --reload-rules
fi

# Check for Python environment
if [ -v $VIRTUAL_ENV ]; then
	echo -e "${RED}Python not configured.${NC}"
	return 1
fi

# Install python modules or upgrade if need be
pip install --upgrade watchdog

# Notify leaving module installation script
echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'${NC}"
