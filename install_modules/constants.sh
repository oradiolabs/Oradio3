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

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# Readability
OK=0
ERROR=1
TRUE="Yes"
FALSE="No"

# Network domain name
NETWORK_DOMAIN="oradio"

# WiFi country setting
WIFI_COUNTRY="NL"

# Locations
ORADIO_PATH_USB_MOUNT_POINT="/media/oradio"
ORADIO_PATH_USB_MONITOR="/media/usb_ready"
ORADIO_PATH_ROOT="$HOME/Oradio3"
ORADIO_PATH_INSTALL_MODULES="$ORADIO_PATH_ROOT/install_modules"
ORADIO_PATH_PYTHON="$ORADIO_PATH_ROOT/Oradio3"

# Start assuming no reboot is needed
REBOOT_REQUIRED=$FALSE
