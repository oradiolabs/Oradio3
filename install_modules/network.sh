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
echo "Enable wifi and set network domain name to '${NETWORK_DOMAIN}'"

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country $WIFI_COUNTRY		# Implicitly activates wifi

# change hostname and hosts mapping to reflect the network domain name
sudo bash -c "hostnamectl set-hostname ${NETWORK_DOMAIN} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${NETWORK_DOMAIN}/g\" /etc/hosts"
# Set user prompt to reflect new hostname
export PS1=$VIRTUAL_ENV_PROMPT"\e[01;32m\u@$NETWORK_DOMAIN\e[00m:\e[01;34m\w \$\e[00m "
# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf
# Activate changes
sudo systemctl restart NetworkManager.service

# Install python modules
python -m pip install nmcli

# Notify leaving module installation script
echo -e "${GREEN}Wifi is enabled and network domain name is set to '${NETWORK_DOMAIN}'${NC}"
