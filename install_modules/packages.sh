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

# Update the OS packages to the latest greatest
sudo apt-get update && sudo apt-get -fy full-upgrade

# Cleanup obsolete packages
sudo apt-get autoremove -y

# Het kan zijn dat een upgrade de kernel heeft bijgewerkt. Dan is een reboot noodzakelijk.
# TODO:
# 1. Detecteren of een reboot nodig is
# 2. Script uitbreiden dat het automatisch herstart bij reboot
# 3. Script uitbreiden dat code voor 2. opgeruimd wordt
# Inform user if reboot is required
if [ -f /var/run/reboot-required ]
then
	echo -e "${YELLOW}*** Hello $USER, you must reboot your machine ***${NC}"
	return $ERROR
else
	echo -e "${YELLOW}'packages' does not reliably detect if reboot is needed to activate the changes${NC}"
fi

# Notify leaving module installation script
echo -e "${GREEN}Packages are up to date${NC}"
