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

# Get absolute path to USB logfile
USB_LOGFILE=$(realpath "$SCRIPT_DIR/../$LOG_DIR/")$USB_LOGFILE

# Configure the USB mount script
cp $SCRIPT_DIR/usb/usb-mount.sh.template $SCRIPT_DIR/usb/usb-mount.sh
replace=`echo $USB_MOUNT_POINT | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_MOUNT_POINT/$replace/g" $SCRIPT_DIR/usb/usb-mount.sh
replace=`echo $USB_MONITOR | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_MONITOR/$replace/g" $SCRIPT_DIR/usb/usb-mount.sh

# Get absolute path to USB logfile
replace=`echo $USB_LOGFILE | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_LOGFILE/$replace/g" $SCRIPT_DIR/usb/usb-mount.sh
sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $SCRIPT_DIR/usb/usb-mount.sh
sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" $SCRIPT_DIR/usb/usb-mount.sh

# Install the USB mount script
sudo cp $SCRIPT_DIR/usb/usb-mount.sh /usr/local/bin/usb-mount.sh
sudo chmod +x /usr/local/bin/usb-mount.sh

# Mount USB if present but not mounted
if [ ! -f $USB_MONITOR ]; then
	# Mount USB partition if present
	for filename in /dev/sda[1-9]; do
		if [ -b "$filename" ]; then
			sudo bash /usr/local/bin/usb-mount.sh add $(basename $filename)
		fi
	done
fi

# Check for USB mount errors and/or warnings
if [ -f $USB_LOGFILE ]; then
	cat $USB_LOGFILE | grep "Error\|Warning"
fi

# The script is called by a systemd unit file. The "@" filename syntax allows passing the device name as an argument
sudo cp $SCRIPT_DIR/usb/usb-mount@.service /etc/systemd/system/

# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
sudo systemctl daemon-reload

# udev rules start and stop the systemd unit service on hotplug/unplug
sudo cp $SCRIPT_DIR/usb/99-local.rules /etc/udev/rules.d/

# Reload to activate
sudo udevadm control --reload-rules

# Install python modules
python -m pip install watchdog

# Notify leaving module installation script
echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'${NC}"
