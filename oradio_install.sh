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

# Available modules to install
MODULES_MANDATORY="network support"
MODULES_OPTIONAL="webinterface usb"

# Function to pretty-print the list of modules
show_modules() {
	# Build nice looking list of modules

	# Initialize first modules line
	mods="#    Modules: "

	# Iterate through modules
	for i in $@; do

		# Get expeted modules line length
		n=$((${#mods} + ${#i}))

		# Break line if too long
		if [ $n -ge 75 ]; then

			# Pad module line with spaces
			while [ ${#mods} -lt 78 ]; do
				mods=$mods" "
			done

			# Add line-end marker
			mods=$mods"#"

			# Output modules line
			echo "$mods"

			# Initialize new modules line
			mods="#                       "
		fi

		# Concatenate module
		mods=$mods"'"$i"',"
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

# Process the input arguments
for i in $@; do
	if `echo $MODULES_OPTIONAL | grep -q -c -w "$i"`; then
		install="$install $i"
	else
		# Output help "header"
		echo
		echo "###############################################################################"
		echo "#                                                                             #"
		echo "# This script does a (modular) Oradio installation and configuration          #"
		echo "#                                                                             #"
		echo "# Syntax: oradio_install module1 module2 module3                              #"

		# Output nice looking list of modules
		show_modules $MODULES_OPTIONAL

		# Output help "footer"
		echo "# No arguments is the same as listing all available modules                   #"

		# Output help "footer"
		echo "#                                                                             #"
		echo "# NOTE: Modules always installed:                                             #"

		# Output nice looking list of modules
		show_modules $MODULES_MANDATORY

		echo "###############################################################################"
		echo

		# Stop script execution
		exit
	fi
done

# TODO: Dit wordt het script waarmee je een schone Bookworm 64bit Lite image configureert voor Oradio3
# De huidige versie ondersteunt networking en usb

########## Install latest OS packages ##########
# Update the OS to the latest greatest
# NOTE: You may be tempted to skip, but better not...
sudo apt-get update && sudo apt-get -y full-upgrade

# Het kan zijn dat een upgrade de kernel heeft bijgewerkt. Dan is een reboot noodzakelijk.
# TODO:
# 1. Detecteren of een reboot nodig is
# 2. Script uitbreiden dat het automatisch herstart bij reboot
# 3. Script uitbreiden dat code voor 2. opgeruimd wordt
echo -e "${YELLOW}Script does not yet reboot and restart if needed${NC}"

echo -e "${GREEN}OS is up to date${NC}"

# If no modules given as argument then process all modules
if [ ${#install} -eq 0 ]; then
	install=$MODULES_OPTIONAL
fi

########## Install modules ##########

# Iterate through MANDATORY modules to install
for i in $MODULES_MANDATORY; do
	echo "Installing module '$i'"
	source install_modules/$i.sh
done

# Iterate through OPTIONAL modules to install
for i in $MODULES_OPTIONAL; do
	if `echo $install | grep -q -c -w "$i"`; then
		echo "Installing module '$i'"
		source install_modules/$i.sh
	else
		echo -e "${YELLOW}Skipping installing '$i'${NC}"
	fi
done

# Display usage

# Output wrap-up "header"
echo
echo    "###############################################################################"
echo -e "# ${GREEN}Oradio installation and configuration done.${NC}                                 #"
echo    "# 1) 'cd Oradio3' and run 'python test-<module>.py' to test specific modules  #"

# Output nice looking list of modules
show_modules $MODULES_OPTIONAL

# Output wrap-up "footer"
echo    "# 2) Reboot to start Oradio.                                                  #"
echo    "###############################################################################"
echo

