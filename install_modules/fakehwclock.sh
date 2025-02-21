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
echo "Configure fake hw clock"

# Install fake hw clock script
sudo cp $SCRIPT_DIR/fakehwclock/fake-hwclock.sh /usr/bin/fake-hwclock.sh
sudo chmod 0755 /usr/bin/fake-hwclock.sh

# Install the fake hw clock services
sudo cp $SCRIPT_DIR/fakehwclock/fake-hwclock.service /etc/systemd/system/
sudo cp $SCRIPT_DIR/fakehwclock/fake-hwclock-tick.service /etc/systemd/system/
sudo cp $SCRIPT_DIR/fakehwclock/fake-hwclock-tick.timer /etc/systemd/system/

# Set fake hw clock service to start at boot
sudo systemctl enable fake-hwclock.service

# Start fake hw clock service
sudo systemctl start fake-hwclock.service

# Notify leaving module installation script
echo -e "${GREEN}Fake hw clock configured${NC}"
