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
echo "Upgrade packages"

# Output to console AND get number of upgradable packages
exec 5>&1
tmp_string=$(sudo apt update | tee >(cat - >&5))
upgrade_count=$(echo ${tmp_string%'packages can be upgraded'*} | rev | cut -d' ' -f1)

# Set count to 0 if not found in parsed output
if ! [[ $upgrade_count =~ ^-?[0-9]+$ ]]; then
	upgrade_count=0
fi

# Upgrade if need be
if [ $upgrade_count -gt 0 ]; then

	# Upgrade packages to the latest greatest
	sudo apt-get -fy dist-upgrade

	# Register if reboot is required
	if [ -f /var/run/reboot-required ]; then
#		echo -e "${YELLOW}A reboot is required to complete the installion${NC}"
		REBOOT_REQUIRED=$YES
	fi

	# Remove obsolete packages and their configuration files
	sudo apt-get autopurge -y
fi

# Notify leaving module installation script
echo -e "${GREEN}Packages are up to date${NC}"
