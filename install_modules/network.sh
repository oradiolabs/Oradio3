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

# Define network domain name
DOMAIN="oradio"

########## Set domain name ##########
# change hostname to reflect the device
sudo hostnamectl set-hostname ${DOMAIN}
# change hosts mapping to reflect the device
sudo sed -i "s/^127.0.1.1.*/127.0.1.1\t${DOMAIN}/g" /etc/hosts
# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf
# Activate changes
sudo systemctl restart NetworkManager.service

########## Activate wireless interface ##########
# Set wifi country, implicitly activating wifi
sudo raspi-config nonint do_wifi_country NL

########## Get packages and python modules for wifi services ##########
# Install iptables and pip
sudo apt-get install iptables pip -y
# Install python modules
sudo pip install pydantic fastapi nmcli JinJa2 uvicorn --break-system-packages

echo -e "${GREEN}Networking functionalty loaded and configured. Oradio has domain name '${DOMAIN}'${NC}"
