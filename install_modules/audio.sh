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
echo "Install and configure audio"

# Install the audio configuration
#sudo cp $ORADIO_PATH_INSTALL_MODULES/audio/asound.conf /etc/asound.conf

# Install audio packages
#sudo apt-get install mpd mpc -y

# Configure music location
#replace=`echo '"'$ORADIO_PATH_USB_MUSIC'"' | sed 's/\//\\\\\//g'`
#sudo sed -i "s/^music_directory.*/music_directory\t\t$replace/g" /etc/mpd.conf

# Configure audio output
AUDIO_OUTPUT="audio_output {
    type            \"alsa\"
    name            \"MPD Output\"
    device          \"MPD_in\"      # The ALSA PCM device
    mixer_type      \"none\"        # Disable software volume control
}"
echo $AUDIO_OUTPUT
echo "TODO: test of audio output al bestaat. Zo ja dan vervangen, zo niet dan toevoegen"

return

			echo "Checking i2c modules..."
			I2C=(
				"i2c-dev"
				"i2c-bcm2835"
			)

			# Check required modules. If missing add to $MODULES and start now
			for ((i = 0; i < ${#I2C[@]}; i++)); do
				module="${I2C[$i]}"
				if ! grep -qx "$module" $MODULES; then
					echo ">Adding module '"$module"'"
					# Add to $MODULES file
					echo $module | sudo tee -a $MODULES >/dev/null
					# Start now
					sudo modprobe $module
				fi
			done
			echo "i2c modules loaded"

			########## Backlight ##########
			echo "Checking backlight options..."
			BACKLIGHT=(
				"#### Oradio backlight options ####"
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
			for ((i = 0; i < ${#BACKLIGHT[@]}; i++)); do
				option="${BACKLIGHT[$i]}"
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
			echo "Backlight options added"


# Install python modules
python -m pip install python-mpd2

# Notify leaving module installation script
echo -e "${GREEN}Audio installed and configured${NC}"
