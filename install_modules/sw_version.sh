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
echo "Configure Oradio software version log"

# Get git tag
gittag=$(GIT_DIR=../$SCRIPTDIR/.git git describe --tags >/dev/null 2>&1)

# Set count to 0 if not found in parsed output
if [ -v $gittag ]; then
	gittag="main HEAD"
fi

# Generate new sw version info
echo "{" > $SCRIPT_DIR/oradio_sw_version.log
echo "   \"serial\": \""$(date +"%Y-%m-%d-%H-%M-%S")"\"," >> $SCRIPT_DIR/oradio_sw_version.log
echo "   \"git-tag\": \"$gittag\"" >> $SCRIPT_DIR/oradio_sw_version.log
echo "}" >> $SCRIPT_DIR/oradio_sw_version.log

# Install sw version
sudo cp $SCRIPT_DIR/oradio_sw_version.log /var/log/oradio_sw_version.log
rm -f $SCRIPT_DIR/oradio_sw_version.log

# Notify leaving module installation script
echo -e "${GREEN}Oradio software version log configured${NC}"
