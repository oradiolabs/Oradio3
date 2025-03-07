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
echo "Installing pip and configure virtual environment"

# Install packages if not yet installed
dpkg --verify python3-pip >/dev/null 2>&1 || sudo apt-get install -y python3-pip

# Configure Python virtual environment
if [ -v $VIRTUAL_ENV ]; then
	# Prepare python virtual environment
	python3 -m venv ~/.venv

	# Activate the python virtual environment in current environemnt
	source ~/.venv/bin/activate

	# Activate python virtual environment when logging in: add if not yet present
	sudo grep -qxF 'source ~/.venv/bin/activate' ~/.bashrc || echo 'source ~/.venv/bin/activate' >> ~/.bashrc
fi

# Install generic python modules or upgrade if need be
pip install --upgrade vcgencmd

# Notify leaving module installation script
echo -e "${GREEN}Python pip installed and virtual environment configured${NC}"
