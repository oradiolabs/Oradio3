#!/bin/bash

# The script uses bash constructs, so make sure the script is running in the bash shell
if [ ! "$BASH_VERSION" ]; then
	echo "Please use bash to run this script ($0), or just execute it directly" 1>&2
	exit 1
fi

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

########## Setup usb automount ##########
# The shell script doing the heavy lifting
sudo cp $(dirname "$0")/usb/usb-mount.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/usb-mount.sh
# The script, in turn, is called by a systemd unit file. The "@" filename syntax allows passing the device name as an argument.
sudo cp $(dirname "$0")/usb/usb-mount@.service /etc/systemd/system/
# Restart the daemon to activate
sudo systemctl daemon-reload
# udev rules start and stop the systemd unit service on hotplug/unplug
sudo cp $(dirname "$0")/usb/99-local.rules /etc/udev/rules.d/
# Reload to activate
sudo udevadm control --reload-rules

########## Get packages and python modules for USB services ##########
# Install pip
sudo apt-get install pip -y
# Install python modules
sudo pip install usb-monitor --break-system-packages

echo -e "${GREEN}USB functionalty loaded and configured. System automounts USB drives on '/media'.${NC}"
