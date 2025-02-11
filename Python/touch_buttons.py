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
@summary:       Oradio touch buttons module with fast response and standalone test mode
"""
import time
import threading
import RPi.GPIO as GPIO


##### oradio modules ####################
from oradio_logging import oradio_log
from play_system_sound import PlaySystemSound
from led_control import LEDControl  # Import LED control module

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
# Define button GPIO mappings globally
BUTTONS = {
    "Play": 9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop": 6
}
# Press durations
LONG_PRESS_DURATION = 6         # Seconds for long press
EXTRA_LONG_PRESS_DURATION = 16  # Seconds for extra-long press

class TouchButtons:
    """
    Handles GPIO-based touch buttons efficiently using event-driven callbacks.
    Detects normal, long, and extra-long presses.
    """

    def __init__(self, state_machine=None, test_mode=False):
        """
        Initializes the button handler.
        :param state_machine: The state machine instance for handling transitions.
        :param test_mode: If True, runs in test mode, blinking LEDs instead of transitioning states.
        """
        self.state_machine = state_machine
        self.sound_player = PlaySystemSound()  # Play "Click" when a button is touched
        self.led_control = LEDControl()  # LED control instance
        self.test_mode = test_mode  # Enable test mode if running standalone
        self.button_press_times = {}  # Track button press times
        self.gpio_to_button = {pin: name for name, pin in BUTTONS.items()}  # Map GPIO to button names

        # Setup GPIO buttons
        self._setup_gpio()

    def _setup_gpio(self):
        """Configures GPIO pins and sets up event detection."""
        GPIO.setmode(GPIO.BCM)

        for pin in BUTTONS.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(pin, GPIO.FALLING, callback=self._button_callback, bouncetime=10)

    def _button_callback(self, channel):
        """
        Handles button press events instantly.
        """
        button_name = self.gpio_to_button[channel]

        # Play click sound immediately
        self.sound_player.play("Click")

        if self.test_mode:
            # Blink corresponding LED for 0.1 seconds
            led_name = f"LED{button_name}"  # LED names match button names prefixed with "LED"
            self.led_control.turn_on_led(led_name)
            threading.Thread(target=self._blink_led, daemon=True).start()
        else:
            # Transition to the new state
            new_state = f"State{button_name}"
            if self.state_machine:
                self.state_machine.transition(new_state)

            # Store press time for long press detection
            self.button_press_times[button_name] = time.monotonic()

            # Start background thread for long-press detection
            threading.Thread(target=self._detect_long_press, args=(channel, button_name), daemon=True).start()

    def _blink_led(self):
        """Blinks all LEDs for 0.1 seconds."""
        time.sleep(0.05)
        self.led_control.turn_off_all_leds()

    def _detect_long_press(self, channel, button_name):
        """
        Detects long and extra-long presses without unnecessary CPU usage.
        """
        start_time = self.button_press_times.get(button_name, time.monotonic())

        # Wait for LONG_PRESS_DURATION
        while time.monotonic() - start_time < LONG_PRESS_DURATION:
            if GPIO.input(channel) == GPIO.HIGH:  # Button released
                return  # Exit if released early
            time.sleep(0.05)  # Reduce CPU load

        # Trigger long press action
        threading.Thread(target=self._long_press_handler, args=(button_name,), daemon=True).start()

        # Continue checking for extra-long press
        while time.monotonic() - start_time < EXTRA_LONG_PRESS_DURATION:
            if GPIO.input(channel) == GPIO.HIGH:
                return  # Exit if released early
            time.sleep(0.05)

        # Trigger extra-long press action
        self._extra_long_press_handler(button_name)

    def _long_press_handler(self, button_name):
        """Handles long press actions."""
        if self.state_machine:
            if button_name == "Play":
                self.state_machine.transition("StateWebService")
            else:
                oradio_log.error(f"LONG press detected on button: {button_name} (no action)")

    def _extra_long_press_handler(self, button_name):
        """Handles extra-long press actions."""
        if self.state_machine:
            if button_name == "Play":
                self.state_machine.transition("StateWebServiceForceAP")
            else:
                oradio_log.error(f"EXTRA LONG press detected on button: {button_name} (no action)")

    def cleanup(self):
        """Cleans up GPIO on exit."""
        GPIO.cleanup()

# ------------------ Standalone Test Mode ------------------
if __name__ == "__main__":
    print("\nStarting Touch Buttons Test Mode...\n")
    print("Press a button to see its corresponding LED blink for 0.1 seconds.")
    print("Press Ctrl+C to exit.\n")

    try:
        # Run touch button test mode (no state machine, only LED blinking)
        touch_buttons = TouchButtons(test_mode=True)

        while True:
            time.sleep(1)  # Keep the script running to detect button presses
    except KeyboardInterrupt:
        print("\nExiting test mode...")
    finally:
        touch_buttons.cleanup()