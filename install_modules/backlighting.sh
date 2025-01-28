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
sudo raspi-config nonint do_i2c 0		# 0: enable

########## Setup modules ##########
# Load i2c modules at boot: Modules do not use parameters, so use /etc/modules over /etc/modprobe.d/
MODULES="/etc/modules"

echo "Checking i2c modules..."
I2C=(
	"i2c-dev"
	"i2c-bcm2835"
)

# Check required modules. If missing add to $MODULES and start now
for ((i = 0; i < ${#I2C[@]}; i++)); do
	module="${I2C[$i]}"
	if ! grep -qx "$module" $MODULES; then
		echo ">Adding module '"$module"'"
		# Add to $MODULES file
		echo $module | sudo tee -a $MODULES >/dev/null
		# Start now
		sudo modprobe $module
	fi
done
echo "i2c modules loaded"


########## Configure and install service ##########
# Configure the backlighting service
replace=`echo $ORADIO_PATH_PYTHON | sed 's/\//\\\\\//g'`
sed -i "s/SCRIPT_PATH/$replace/g" $ORADIO_PATH_INSTALL_MODULES/backlighting/backlighting.service
# Install the backlighting service
sudo cp $ORADIO_PATH_INSTALL_MODULES/backlighting/backlighting.service /etc/systemd/system/
# Set backlighting system to start at boot
sudo systemctl enable backlighting.service
# Start backlighting system now
sudo systemctl start backlighting.service
# To be safe, rerun all generators, reload all unit files, and recreate the entire dependency tree
sudo systemctl daemon-reload

# Install python modules
python -m pip install smbus2 rpi-lgpio

# Notify leaving module installation script
echo -e "${GREEN}Backlightng installed and configured${NC}"
