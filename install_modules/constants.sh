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

# Network domain name
NETWORK_DOMAIN="oradio"

# WiFi country setting
WIFI_COUNTRY="NL"

# Locations
ROOT="$HOME/Oradio3"
MODULES="$ROOT/install_modules"
PYTHON="$ROOT/Python"
USB_MOUNT_POINT="/media/oradio"
USB_MONITOR="/media/usb_ready"
USB_MUSIC="/media/oradio/Muziek"
USB_SYSTEM="/media/oradio/Systeem"
