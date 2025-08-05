#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for USB detect, insert, and remove services
    :Note
    :Install
    :Documentation
"""
import time
import threading
from RPi import GPIO

##### oradio modules ####################
from backlighting import Backlighting


##### GLOBAL constants ####################
from oradio_const import LEDS

# Initialize GPIO
GPIO.setmode(GPIO.BCM)

# Configure LEDs as outputs
for name, pin in LEDS.items():
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

def test_backlighting():
    """Collection of backlighting test functions"""
    lighting = Backlighting()
    auto_adjust_thread = None

    def start_auto_adjust():
        """ Start the auto_adjust function in a separate thread """
        nonlocal auto_adjust_thread
        if auto_adjust_thread is None or not auto_adjust_thread.is_alive():
            print("Starting Auto Adjust...")
            auto_adjust_thread = threading.Thread(target=lighting.auto_adjust)
            auto_adjust_thread.daemon = True  # Allows the thread to exit when the main program exits
            auto_adjust_thread.start()
        else:
            print("Auto Adjust is already running.")

    def stop_auto_adjust():
        """ Stop the auto_adjust function """
        if lighting.running:
            print("Stopping Auto Adjust...")
            lighting.running = False
            if auto_adjust_thread:
                auto_adjust_thread.join()
        else:
            print("Auto Adjust is not running.")

    def test_sensor_mode():
        """ Print raw_visible_light, calculated lux, and DAC value every 2 seconds """
        print("Entering Test Mode... Press Ctrl+C to return to the main menu.")
        lighting.initialize_sensor()
        try:
            while True:
                raw_visible_light = lighting.read_visible_light()
                lux = lighting.calculate_lux(raw_visible_light)
                target_dac_value = lighting.interpolate_backlight(lux)
                print(f"Raw Visible Light: {raw_visible_light}, Lux: {lux:.2f}, DAC Value: {target_dac_value}")
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nExiting Test Mode...")

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Activate Auto Adjust\n"
                       " 2-Stop Auto Adjust\n"
                       " 3-Turn Off backlight\n"
                       " 4-Set backlight to maximum\n"
                       " 5-Test sensor mode\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except ValueError:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                break
            case 1:
                start_auto_adjust()
            case 2:
                stop_auto_adjust()
            case 3:
                print("Turning Off backlight...")
                lighting.off()
            case 4:
                print("Setting backlight to maximum...")
                lighting.maximum()
            case 5:
                test_sensor_mode()
            case _:
                print("\nPlease input a valid number\n")

if __name__ == "__main__":
# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    test_backlighting()

# Restore checking or duplicate code
# pylint: enable=duplicate-code
