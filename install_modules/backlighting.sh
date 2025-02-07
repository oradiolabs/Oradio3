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
echo "Install and configure backlighting"

########## Activate i2c interface ##########
# https://www.raspberrypi.com/documentation/computers/configuration.html#i2c-nonint
sudo raspi-config nonint do_i2c 0	# 0: enable

########## Setup modules ##########
sudo cp $SCRIPT_DIR/backlighting/modules /etc/modules

# Start modules now
I2C_MODULES=(
	"i2c-dev"
	"i2c-bcm2835"
)
echo "Start i2c modules..."
for ((backlighting_i = 0; backlighting_i < ${#I2C[@]}; backlighting_i++)); do
	module="${I2C[$backlighting_i]}"
	sudo modprobe $module
done

echo "i2c modules loaded and started"

########## Configure and install service ##########
# Configure the backlighting service
cp $SCRIPT_DIR/backlighting/backlighting.service.template $SCRIPT_DIR/backlighting/backlighting.service
sed -i "s/PLACEHOLDER_USER/$(id -un)/g" $SCRIPT_DIR/backlighting/backlighting.service
sed -i "s/PLACEHOLDER_GROUP/$(id -gn)/g" $SCRIPT_DIR/backlighting/backlighting.service
replace=`echo $(realpath "$SCRIPT_DIR/../Python") | sed 's/\//\\\\\//g'`
sed -i "s/PLACEHOLDER_PATH/$replace/g" $SCRIPT_DIR/backlighting/backlighting.service

# Install the backlighting service
sudo cp $SCRIPT_DIR/backlighting/backlighting.service /etc/systemd/system/

# Set backlighting system to start at boot
sudo systemctl enable backlighting.service

# Start backlighting system now
sudo systemctl start backlighting.service

# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
sudo systemctl daemon-reload

# Install python modules
python -m pip install smbus2 rpi-lgpio

# Notify leaving module installation script
echo -e "${GREEN}Backlighting installed and configured${NC}"
