#!/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on January 24, 2025
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

# In case the script is executed stand-alone
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source $SCRIPT_DIR/constants.sh

# Notify entering module installation script
echo "Configuring boot options"

########## Generic ##########
# Explaining options: https://www.raspberrypi.com/documentation/computers/config_txt.html
# In short: The Oradio is a RPI 3, headless, with own audio, and no over-clocking to keep it cool
CONFIG="/boot/firmware/config.txt"
# Options to deactivate
DEACTIVATE=(
	"dtparam=audio=on"
	"camera_auto_detect=1"
	"display_auto_detect=1"
	"dtoverlay=vc4-kms-v3d"
	"max_framebuffers=2"
	"disable_fw_kms_setup=1"
	"disable_overscan=1"
	"arm_boost=1"
	"otg_mode=1"
	"dtoverlay=dwc2,dr_mode=host"
)
# Options to activate
ACTIVATE=(
	"dtparam=i2c_arm=on"
	"dtparam=i2s=on"
	"auto_initramfs=1"
	"arm_64bit=1"
)
########## Deactivate unneccesary ##########
# Check not needed options, deactivate if enabled
echo "Checking unneccesary boot options..."
for ((i = 0; i < ${#DEACTIVATE[@]}; i++)); do
	option="${DEACTIVATE[$i]}"
	if grep -qx "^$option" $CONFIG; then
		echo ">Deactivating option '$option'"
		sudo sed -i "s/^$option$/#$option/g" $CONFIG
		REBOOT_REQUIRED=$TRUE
	else
		if ! grep -qx "^#.*$option" $CONFIG; then
			echo -e "${YELLOW}Missing option '$option' in $CONFIG${NC}"
		fi
	fi
done
echo "Unneccesary boot options deactivated"

########## Activate required ##########
# Check required options, activate if disabled
echo "Checking required boot options..."
for ((i = 0; i < ${#ACTIVATE[@]}; i++)); do
	option="${ACTIVATE[$i]}"
	if grep -qx "#.*$option" $CONFIG; then
		echo ">Activating option '$option'"
		sudo sed -i "s/^#.*$option$/$option/g" $CONFIG
		REBOOT_REQUIRED=$TRUE
	else
		if ! grep -qx "^$option" $CONFIG; then
			echo -e "${RED}Fatal error: Missing option '$option' in $CONFIG"
			# Let parent script know a reboot is required
			return $ERROR
		fi
	fi
done
echo "Required boot options activated"

########## Backlighting ##########
echo "Checking backlighting options..."
BACKLIGHTING=(
	"#### Oradio backlighting options ####"
	"# gpio pinning LEDs all leds off - only backlighting"
	"gpio=23=op,dl"
	"gpio=24=op,dh"
	"gpio=25=op,dh"
	"gpio=7=op,dh"
	"gpio=15=op,dh"
	"# Leds on board off"
	"dtparam=pwr_led_trigger=none"
	"dtparam=pwr_led_activelow=on"
	"dtparam=act_led_trigger=none"
	"dtparam=act_led_activelow=off # the off is ok, for act it is reversed!"
)

# Check required options, add if missing
for ((i = 0; i < ${#BACKLIGHTING[@]}; i++)); do
	option="${BACKLIGHTING[$i]}"
	if ! grep -qx "^$option$" $CONFIG; then
		echo ">Adding option '"$option"'"
		if [ "${option%"${option#?}"}" == "#" ]; then 
			echo $'\n'$option | sudo tee -a $CONFIG >/dev/null
		else
			echo $option | sudo tee -a $CONFIG >/dev/null
		fi
		REBOOT_REQUIRED=$TRUE
	fi
done
echo "Backlighting options added"

########## Audio ##########
echo "Checking audio options..."
AUDIO=(
	"#### Oradio audio options ####"
	"dtoverlay=i2s-mmap"
	"dtoverlay=rpi-digiampplus,unmute_amp"
)

# Check required options, add if missing
for ((i = 0; i < ${#AUDIO[@]}; i++)); do
	option="${AUDIO[$i]}"
	if ! grep -qx "^$option$" $CONFIG; then
		echo ">Adding option '"$option"'"
		if [ "${option%"${option#?}"}" == "#" ]; then 
			echo $'\n'$option | sudo tee -a $CONFIG >/dev/null
		else
			echo $option | sudo tee -a $CONFIG >/dev/null
		fi
		REBOOT_REQUIRED=$TRUE
	fi
done
echo "Audio options added"

# Notify leaving module installation script
echo -e "${GREEN}Boot options configured${NC}"
