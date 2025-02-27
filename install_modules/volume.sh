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
echo "Install and configure volume"

# Install packages if not yet installed
dpkg --verify libasound2-dev >/dev/null 2>&1 || sudo apt-get install -y libasound2-dev

# Set volume to normal level
amixer -c 0 cset name='Digital Playback Volume' 90

# Check for Python environment
if [ -v $VIRTUAL_ENV ]; then
	echo -e "${RED}Python not configured.${NC}"
	return 1
fi

# Install python modules. On --use-pep517 see https://github.com/pypa/pip/issues/8559
pip install pyalsaaudio --use-pep517 --upgrade

# Notify leaving module installation script
echo -e "${GREEN}Volume installed and configured${NC}"
