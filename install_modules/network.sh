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
echo "Enable wifi and set network domain to '${HOSTNAME}.local'"

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country $WIFI_COUNTRY		# Implicitly activates wifi

if [ $(hostname) != $HOSTNAME ]; then
	# change hostname and hosts mapping to reflect the network domain name
	sudo bash -c "hostnamectl set-hostname ${HOSTNAME} && sed -i \"s/^127.0.1.1.*/127.0.1.1\t${HOSTNAME}/g\" /etc/hosts"
	echo 'hostname and hosts set'

	# Set user prompt to reflect new hostname
	export PS1=$VIRTUAL_ENV_PROMPT"\e[01;32m\u@$HOSTNAME\e[00m:\e[01;34m\w \$\e[00m "
	echo 'prompt set'

	# Set Top Level Domain (TLD) to 'local', enabling access via http://oradio.local
	sudo sed -i "s/^.domain-name=.*/domain-name=local/g" /etc/avahi/avahi-daemon.conf

	# Allow mDNS on wired and wireless interfaces
	sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf
	echo 'avahi config set'

	# Activate changes
	sudo systemctl restart NetworkManager.service

	#OMJ: Als je NetworkManager restart kan je verder met het script, maar op de achtergrond is nog van allles gaande, kan het zijn dat de verbinding met het Internet nog niet hersteld is. Dit is met name het geval als je via een wifi verbindsing werkt, omdat reset van de NetworkManager een reconnect doet.
	#OMJ: Zoek alternatief voor resetten van NetworkManager die de wifi verbinding niet verbreekt
	# Allow NetworkManager to settle
	sleep 30
fi

# Check for Python environment
if [ -v $VIRTUAL_ENV ]; then
	echo -e "${RED}Python not configured.${NC}"
	return 1
fi

# Install generic python modules or upgrade if need be
pip install --upgrade nmcli

# Notify leaving module installation script
echo -e "${GREEN}Wifi is enabled and network domain is set to '${HOSTNAME}.local'${NC}"
