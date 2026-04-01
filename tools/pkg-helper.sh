#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on November 2, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1
# @email:         info@stichtingoradio.nl
# @status:        Development
# @purpose:       Install/update packages passed as arguments

##### Initialize #####################

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

#---------- Ensure using bash ----------

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Parse arguments into list of packages to install/upgrade ----------

# Exit if no packages provided
if [ "$#" -eq 0 ]; then
    echo -e "${RED}No packages provided${NC}"
    echo "Usage: ${0##*/} <package> <package> ..."
    exit 1
fi

# Required packages
REQUIRED_PACKAGES=("$@")

#---------- Ensure connected to internet ----------

if ! curl -I https://google.com >/dev/null 2>&1; then
	echo -e "${RED}No internet connection${NC}"
	exit 1
else
	echo "Connected to Internet"
fi

#---------- Ensure required packages are installed and up to date ----------

STAMP_FILE="/var/lib/apt/last_update_stamp"
MAX_AGE=$((6 * 3600))	# 6 hours in seconds

# Get last time the list was updated, 0 if never
if [[ -f "$STAMP_FILE" ]]; then
	last_update=$(cat "$STAMP_FILE")
else
	last_update=0
fi

# Get time since last update
current_time=$(date +%s)
age=$((current_time - last_update))

# Update lists if to old
if (( age > MAX_AGE )); then
	sudo apt-get update
	# Save time lists were updated
	date +%s | sudo tee "$STAMP_FILE" >/dev/null
	echo "Package lists updated"
else
	echo "Package lists are up to date"
fi
# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

# Fetch list of upgradable packages
UPGRADABLE=$(apt list --upgradable 2>/dev/null | cut -d/ -f1)

# Create associative array for fast lookup
declare -A UPGRADABLE_MAP
for package in $UPGRADABLE; do
	UPGRADABLE_MAP["$package"]=1
done

# Ensure packages are installed and up-to-date
for package in "${REQUIRED_PACKAGES[@]}"; do
	if dpkg -s "$package" &>/dev/null; then
		# Check if installed package can be upgraded
		if [[ ${UPGRADABLE_MAP["$package"]+_} ]]; then
			echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
			sudo apt-get install -y "$package"
		else
			echo "$package is up-to-date"
		fi
	else
		echo -e "${YELLOW}$package is missing: installing...${NC}"
		sudo apt-get install -y "$package"
	fi
done
echo -e "${GREEN}Packages installed and up to date:  $REQUIRED_PACKAGES${NC}"

