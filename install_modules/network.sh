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

# In case the script is executed stand-alone
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source $SCRIPT_DIR/constants.sh

# Define network domain name
DOMAIN="oradio"
COUNTRY="NL"

# Notify entering module installation script
echo "Enable wifi and set domain name to '${DOMAIN}'"

########## Activate wireless interface ##########
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country $COUNTRY		# Implicitly activates wifi

########## Set domain name ##########
# change hostname and hosts mapping to reflect the domain
sudo bash -c "hostnamectl set-hostname ${DOMAIN} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${DOMAIN}/g\" /etc/hosts"
# Set user prompt to reflect new hostname
export PS1=$VIRTUAL_ENV_PROMPT"\e[01;32m\u@$DOMAIN\e[00m:\e[01;34m\w \$\e[00m "
# If activeAdd virtual environment to prompt
#if [ ! -z "${VIRTUAL_ENV}" ]; then
#    export PS1=$VIRTUAL_ENV_PROMPT"${PS1:-}"
#fi
# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf
# Activate changes
sudo systemctl restart NetworkManager.service

# Notify leaving module installation script
echo -e "${GREEN}Wifi is enabled and domain name is set to '${DOMAIN}'${NC}"
