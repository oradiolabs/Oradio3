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
	exit 1
fi

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

########## Get packages and python modules for web-interface ##########
# Install iptables
sudo apt-get install iptables -y
# Install python modules
python -m pip install pydantic fastapi nmcli JinJa2 uvicorn

echo -e "${GREEN}web-interface andcaptive portal functionalty loaded and configured${NC}"
