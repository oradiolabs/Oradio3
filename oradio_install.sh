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

# Load shared constants
if [ ! -f install_modules/constants.sh ]; then
	echo "constants.sh not found"
	return 1
fi
source install_modules/constants.sh

# Modules to install. ORDER IS IMPORTANT!
ORADIO_MODULES=(
	"config"
	"packages"
	"python"		# depends on packages
	"network"
	"i2c"			# Requires on python
	"hw_version"	# depends on python
	"logging"		# Depends on python
	"backlighting"	# Depends on python
	"usb_service"	# Depends on network and python
	"audio"			# Depends on usb_service and python
	"volume"		# Depends on audio and python
	"spotify_connect"
	"web_service"	# Depends on network and python
	"sw_version"
	"autostart"		# Must be last
)

# Function to pretty-print the list of modules
show_modules() {
	# Build nice looking list of modules

	# Initialize first modules line
	mods="#      "

	# Iterate through modules
	for ((i = 0; i < ${#ORADIO_MODULES[@]}; i++)); do
		module="${ORADIO_MODULES[$i]}"

		# Get expeted modules line length
		n=$((${#mods} + ${#module}))

		# Break line if too long
		if [ $n -ge 77 ]; then

			# Pad module line with spaces
			while [ ${#mods} -lt 78 ]; do
				mods=$mods" "
			done

			# Add line-end marker
			mods=$mods"#"

			# Output modules line
			echo "$mods"

			# Initialize new modules line
			mods="#      "
		fi

		# Concatenate module
		mods=$mods"'"$module"',"
	done

	# Cleanup last modules line 
	mods=${mods::-1}
	while [ ${#mods} -lt 78 ]; do
		# Pad module line with spaces
		mods=$mods" "
	done

	# Add line-end marker
	mods=$mods"#"

	# Output modules line
	echo "$mods"
}

# Script is for Bookworm 64bit Lite
BOOKWORM64="Debian GNU/Linux 12 (bookworm)"
if [ "$(lsb_release -a | grep "Description:" | cut -d$'\t' -f2)" != "$BOOKWORM64" ]; then

	# Output "header"
	echo
	echo "###############################################################################"
	echo "#                                                                             #"
	echo -e "# ${RED}Unsupported OS version${NC}                                                      #"
	echo "#                                                                             #"
	echo "# This script prepares a Bookworm 64bit Lite image for Oradio3                #"
	echo "#                                                                             #"
	echo "# The following modules will be installed:                                    #"

	# Output nice looking list of modules
	show_modules

	# Output "footer"
	echo "#                                                                             #"
	echo "###############################################################################"
	echo

	return $ERROR
fi

# Initialize to 'no reboot required'
REBOOT_REQUIRED=$NO

########## Install modules ##########

# Iterate through MANDATORY modules to install
for ((main_i = 0; main_i < ${#ORADIO_MODULES[@]}; main_i++)); do
	module="${ORADIO_MODULES[$main_i]}"
	source install_modules/$module.sh
	if [ $REBOOT_REQUIRED == $YES ]; then
		echo -e "${YELLOW}Please reboot now to complete the installion.\nAfter reboot login and start this install script again to complete the installation.${NC}"
		return
	fi
done

# Output wrap-up "header"
echo
echo    "###############################################################################"
echo -e "# ${GREEN}Oradio installation and configuration done.${NC}                                 #"
# Remind user if reboot is required to complete installation
if [ $REBOOT_REQUIRED == $YES ]; then
	echo -e "# ${YELLOW}Please reboot now to complete the installion${NC}                                #"
# Everything is fine
else
	echo    "# 1) 'cd Python' and run 'python [test_]<module>.py' to test module           #"
	echo    "# 2) Reboot to start Oradio.                                                  #"
fi
echo    "###############################################################################"
echo

