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
	exit 1
fi

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# Define network domain name
DOMAIN="oradio"
COUNTRY="NL"

########## Activate wireless interface ##########
# Set wifi country, implicitly activating wifi
sudo raspi-config nonint do_wifi_country ${COUNTRY}

########## Set domain name ##########
# change hostname and hosts mapping to reflect the domain
sudo bash -c "hostnamectl set-hostname ${DOMAIN} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${DOMAIN}/g\" /etc/hosts"
# Set user prompt to reflect new hostname
export PS1="\e[01;32m\u@$DOMAIN\e[00m:\e[01;34m\w \$\e[00m "
# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf
# Activate changes
sudo systemctl restart NetworkManager.service

echo -e "${GREEN}Oradio wifi is enabled and has domain name '${DOMAIN}'${NC}"
