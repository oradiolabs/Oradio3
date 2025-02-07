#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio LED control module

"""

import time
import threading
import RPi.GPIO as GPIO
import oradio_utils

# Global constant for LED GPIO pins
LEDS = {
    "LEDPlay": 15,
    "LEDPreset1": 24,
    "LEDPreset2": 25,
    "LEDPreset3": 7,
    "LEDStop": 23
}

class LEDControl:
    """
    Class to control LEDs using Raspberry Pi GPIO.
    """

    def __init__(self):
        """
        Initializes the GPIO and sets all LEDs to OFF (HIGH state).
        """
        GPIO.setmode(GPIO.BCM)

        # Configure LEDs as outputs and turn them off (HIGH)
        for name, pin in LEDS.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

        # Dictionary to track blinking flags for each LED
        self.blinking_leds = {}
        # Dictionary to track blinking thread objects for each LED
        self.blinking_threads = {}
        oradio_utils.logging("info", "LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name):
        """Turns off a specific LED and stops its blinking if active."""
        if led_name not in LEDS:
            oradio_utils.logging("error", f"Invalid LED name: {led_name}")
            return
        # Signal any blinking thread for this LED to stop
        self.blinking_leds[led_name] = False
        if led_name in self.blinking_threads:
            t = self.blinking_threads[led_name]
            t.join(timeout=0.5)
            del self.blinking_threads[led_name]
        GPIO.output(LEDS[led_name], GPIO.HIGH)
        oradio_utils.logging("info", f"{led_name} turned off")

    def turn_on_led(self, led_name):
        """Turns on a specific LED (stops blinking if active)."""
        if led_name in LEDS:
            self.turn_off_led(led_name)  # Stop blinking if active
            GPIO.output(LEDS[led_name], GPIO.LOW)
            oradio_utils.logging("info", f"{led_name} turned on")
        else:
            oradio_utils.logging("error", f"Invalid LED name: {led_name}")

    def turn_off_all_leds(self):
        """
        Turns off all LEDs and stops any ongoing blinking.
        """
        # Stop blinking for every LED
        for led_name in list(LEDS.keys()):
            self.blinking_leds[led_name] = False
            if led_name in self.blinking_threads:
                t = self.blinking_threads[led_name]
                t.join(timeout=0.5)
                del self.blinking_threads[led_name]
        # Turn off all LEDs
        for led in LEDS.values():
            GPIO.output(led, GPIO.HIGH)
        oradio_utils.logging("info", "All LEDs turned off and blinking stopped")
    
    def turn_on_led_with_delay(self, led_name, delay=3):
        """
        Turns on a specific LED and then turns it off after a delay.

        Args:
            led_name (str): The name of the LED to control.
            delay (float): Time in seconds before turning off the LED.
        """
        if led_name not in LEDS:
            oradio_utils.logging("error", f"Invalid LED name: {led_name}")
            return

        # Stop any blinking for this LED and turn it on
        self.turn_off_led(led_name)
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_utils.logging("info", f"{led_name} turned on, will turn off after {delay} seconds")

        def delayed_off():
            time.sleep(delay)
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_utils.logging("info", f"{led_name} turned off after {delay} seconds")

        threading.Thread(target=delayed_off, daemon=True).start()

    def control_blinking_led(self, led_name, cycle_time=None):
        """
        Controls blinking of a specific LED.
        If `cycle_time` is None or 0, blinking stops and the LED turns off.
        
        Args:
            led_name (str): The name of the LED to blink.
            cycle_time (float or None): Blink interval in seconds.
        """
        if led_name not in LEDS:
            oradio_utils.logging("error", f"Invalid LED name: {led_name}")
            return

        # If no cycle time is provided, stop blinking
        if cycle_time is None or cycle_time == 0:
            self.blinking_leds[led_name] = False
            if led_name in self.blinking_threads:
                t = self.blinking_threads[led_name]
                t.join(timeout=0.5)
                del self.blinking_threads[led_name]
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_utils.logging("info", f"{led_name} blinking stopped and turned off")
            return

        # If a blinking thread is already running for this LED, stop it first.
        if led_name in self.blinking_threads and self.blinking_threads[led_name].is_alive():
            self.blinking_leds[led_name] = False
            t = self.blinking_threads[led_name]
            t.join(timeout=0.5)
            del self.blinking_threads[led_name]

        # Start a new blinking thread
        self.blinking_leds[led_name] = True

        def blink():
            while self.blinking_leds.get(led_name, False):
                # Turn on LED
                GPIO.output(LEDS[led_name], GPIO.LOW)
                time.sleep(cycle_time / 2)
                if not self.blinking_leds.get(led_name, False):
                    break
                # Turn off LED
                GPIO.output(LEDS[led_name], GPIO.HIGH)
                time.sleep(cycle_time / 2)
            # Ensure LED is off when finished
            GPIO.output(LEDS[led_name], GPIO.HIGH)

        t = threading.Thread(target=blink, daemon=True)
        t.start()
        self.blinking_threads[led_name] = t
        oradio_utils.logging("info", f"{led_name} blinking started with cycle time: {cycle_time}s")

    def cleanup(self):
        """Cleans up GPIO on program exit."""
        self.turn_off_all_leds()
        self.blinking_leds = {}
        self.blinking_threads = {}
        GPIO.cleanup()
        oradio_utils.logging("info", "GPIO cleanup completed")

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("\nStarting LED Control Standalone Test...\n")
    
    # Instantiate LEDControl
    leds = LEDControl()

    def select_led():
        """Displays available LEDs and prompts the user to select one."""
        print("\nSelect an LED:")
        for idx, led_name in enumerate(LEDS.keys(), start=1):
            print(f" {idx} - {led_name}")
        print(" 6 - Turn OFF all LEDs")
        print(" 0 - Quit")  # Quit option now in LED selection menu

        try:
            choice = int(input("Select LED number: ")) - 1
            if choice == -1:  # User chose 0 = Quit
                print("\nExiting test program...\n")
                leds.cleanup()
                exit()
            elif 0 <= choice < len(LEDS):
                return list(LEDS.keys())[choice]
            elif choice == 5:  # If user selects option 6 (zero-based index 5)
                return "ALL"
            else:
                print("Invalid selection.\n")
                return None
        except ValueError:
            print("Invalid input. Please enter a number.\n")
            return None

    # Show menu with test options
    input_selection = ("\nSelect an action for the LED:\n"
                       " 0 - Back to LED selection\n"  # Instead of quitting
                       " 1 - Turn ON\n"
                       " 2 - Turn OFF\n"
                       " 3 - Blink\n"
                       " 4 - Turn ON and OFF after delay\n"
                       " 5 - Turn OFF all LEDs\n"
                       "Select: ")

    # User command loop
    while True:
        led_name = select_led()
        if led_name == "ALL":
            leds.turn_off_all_leds()
            print("\nExecuting: Turn OFF all LEDs\n")
            continue  # Go back to LED selection

        if not led_name:
            continue  # If invalid LED selection, retry

        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1  # Invalid input

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nReturning to LED selection...\n")
                    break  # Go back to LED selection menu
                case 1:
                    print(f"\nExecuting: Turn ON {led_name}\n")
                    leds.turn_on_led(led_name)
                case 2:
                    print(f"\nExecuting: Turn OFF {led_name}\n")
                    leds.turn_off_led(led_name)
                case 3:
                    cycle_time = float(input("Enter blink cycle time (seconds): "))
                    print(f"\nExecuting: Blinking {led_name} every {cycle_time}s\n")
                    leds.control_blinking_led(led_name, cycle_time)
                case 4:
                    delay = float(input("Enter delay before turning off (seconds): "))
                    print(f"\nExecuting: Turning ON {led_name} and OFF after {delay} seconds\n")
                    leds.turn_on_led_with_delay(led_name, delay)
                case 5:
                    print("\nExecuting: Turn OFF all LEDs\n")
                    leds.turn_off_all_leds()
                case _:
                    print("\nInvalid selection. Please enter a valid number.\n")
