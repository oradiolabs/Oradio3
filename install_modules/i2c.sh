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
echo "Install and configure i2c"

# Activate i2c interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#i2c-nonint
sudo raspi-config nonint do_i2c 0	# 0: enable

# Setup modules
sudo cp $SCRIPT_DIR/i2c/modules /etc/modules

# Start modules now
I2C_MODULES=(
	"i2c-dev"
	"i2c-bcm2835"
)
for ((i2c_i = 0; i2c_i < ${#I2C[@]}; i2c_i++)); do
	module="${I2C[$i2c_i]}"
	sudo modprobe $module
done

# Check for Python environment
if [ -v $VIRTUAL_ENV ]; then
	echo -e "${RED}Python not configured.${NC}"
	return 1
fi

# Install python modules or upgrade if need be
pip install --upgrade smbus2 rpi-lgpio

# Notify leaving module installation script
echo -e "${GREEN}i2c installed and configured${NC}"
