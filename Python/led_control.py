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
from RPi import GPIO
from oradio_logging import oradio_log

# Global constant for LED GPIO pins
LEDS = {
    "LEDPlay": 15,
    "LEDPreset1": 24,
    "LEDPreset2": 25,
    "LEDPreset3": 7,
    "LEDStop": 23
}

class LEDControl:
    """Control LED states"""

    def __init__(self):
        """Class constructor: setup class variables"""
        GPIO.setmode(GPIO.BCM)
        for _, pin in LEDS.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

        # replace blinking_leds with real Events…
        self.blinking_leds = {}
        self.blink_stop_events = {}       # map led_name → threading.Event()
        self.blinking_threads = {}        # map led_name → Thread
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name, log: bool = True):
        """Turns off a specific LED and waits for its blink‐thread to exit."""
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # signal any blink thread to stop
        evt = self.blink_stop_events.pop(led_name, None)
        if evt:
            evt.set()

        # block until thread really finishes
        thread = self.blinking_threads.pop(led_name, None)
        if thread:
            thread.join()

        # now safe to drive off
        GPIO.output(LEDS[led_name], GPIO.HIGH)
        if log:
            oradio_log.debug("%s turned off", led_name)


    def turn_on_led(self, led_name):
        """Turns on a specific LED (stops blinking if active)."""
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # stop blinking silently (no 'turned off' log), then light it
        self.turn_off_led(led_name, log=False)
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_log.debug("%s turned on", led_name)

    def turn_off_all_leds(self):
        """Stops all blink‐threads and turns every LED off."""
        # stop all threads
        for evt in self.blink_stop_events.values():
            evt.set()
        for thread in self.blinking_threads.values():
            thread.join()
        self.blink_stop_events.clear()
        self.blinking_threads.clear()

        # drive every pin HIGH
        for pin in LEDS.values():
            GPIO.output(pin, GPIO.HIGH)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_led_with_delay(self, led_name, delay=3):
        """
        Turns on a specific LED and then turns it off after a delay.

        Args:
            led_name (str): The name of the LED to control.
            delay (float): Time in seconds before turning off the LED.
        """
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # Stop any blinking for this LED and turn it on
        self.turn_off_led(led_name)
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, delay)

        def delayed_off():
            time.sleep(delay)
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_log.debug("%s turned off after %s seconds", led_name, delay)

        threading.Thread(target=delayed_off, daemon=True).start()

    def control_blinking_led(self, led_name, cycle_time=None):
        """
        Blink using an Event for instant stop, not long sleeps.
        """
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # stop any existing blink
        old_evt = self.blink_stop_events.pop(led_name, None)
        if old_evt:
            old_evt.set()
        old_thread = self.blinking_threads.pop(led_name, None)
        if old_thread:
            old_thread.join()

        # if no cycle_time, just turn off
        if not cycle_time:
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_log.debug("%s blinking stopped and turned off", led_name)
            return

        # start new blink thread
        stop_evt = threading.Event()
        self.blink_stop_events[led_name] = stop_evt

        def _blink():
            pin = LEDS[led_name]
            half = cycle_time / 2
            while not stop_evt.is_set():
                GPIO.output(pin, GPIO.LOW)
                if stop_evt.wait(half):
                    break
                GPIO.output(pin, GPIO.HIGH)
                if stop_evt.wait(half):
                    break
            GPIO.output(pin, GPIO.HIGH)

        thread = threading.Thread(target=_blink, daemon=True)
        thread.start()
        self.blinking_threads[led_name] = thread
        oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)

    def cleanup(self):
        """Cleans up GPIO on program exit."""
        self.turn_off_all_leds()
        self.blinking_leds = {}
        self.blinking_threads = {}
        GPIO.cleanup()
        oradio_log.debug("GPIO cleanup completed")

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
    INPUT_SELECTION = ("\nSelect an action for the LED:\n"
                       " 0 - Back to LED selection\n"  # Instead of quitting
                       " 1 - Turn ON\n"
                       " 2 - Turn OFF\n"
                       " 3 - Blink\n"
                       " 4 - Turn ON and OFF after delay\n"
                       " 5 - Turn OFF all LEDs\n"
                       "Select: ")

    # User command loop
    while True:
        led = select_led()
        if led == "ALL":
            leds.turn_off_all_leds()
            print("\nExecuting: Turn OFF all LEDs\n")
            continue  # Go back to LED selection

        if not led:
            continue  # If invalid LED selection, retry

        while True:
            # Get user input
            try:
                function_nr = int(input(INPUT_SELECTION))  # pylint: disable=invalid-name
            except ValueError:
                function_nr = -1  # pylint: disable=invalid-name

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nReturning to LED selection...\n")
                    break  # Go back to LED selection menu
                case 1:
                    print(f"\nExecuting: Turn ON {led}\n")
                    leds.turn_on_led(led)
                case 2:
                    print(f"\nExecuting: Turn OFF {led}\n")
                    leds.turn_off_led(led)
                case 3:
                    cycle = float(input("Enter blink cycle time (seconds): "))
                    print(f"\nExecuting: Blinking {led} every {cycle}s\n")
                    leds.control_blinking_led(led, cycle)
                case 4:
                    wait = float(input("Enter delay before turning off (seconds): "))
                    print(f"\nExecuting: Turning ON {led} and OFF after {wait} seconds\n")
                    leds.turn_on_led_with_delay(led, wait)
                case 5:
                    print("\nExecuting: Turn OFF all LEDs\n")
                    leds.turn_off_all_leds()
                case _:
                    print("\nInvalid selection. Please enter a valid number.\n")
