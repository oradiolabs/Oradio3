#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on November 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio GPIO low-level access module
    For I/O pins related to buttons and leds
    Following services provided:
        * Set LED pin On/Off based on LED_NAMES
        * Get state of a LED pin based on LED_NAMES
        * Get state of a BUTTON pin based on BUTTON_NAMES
        * Set the callback for buttons related edge events

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from time import perf_counter
from typing import Tuple, Optional
from threading import Lock
from RPi import GPIO

##### oradio modules ####################
from oradio_logging import oradio_log
from singleton import singleton

##### GLOBAL constants ####################
from oradio_const import (
    LED_PLAY, LED_STOP,
    LED_PRESET1, LED_PRESET2, LED_PRESET3,
    LED_NAMES,
    BUTTON_PLAY, BUTTON_STOP,
    BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3,
    BUTTON_NAMES, BUTTON_PRESSED, BUTTON_RELEASED,
    TEST_ENABLED, TEST_DISABLED
)

##### Local constants ####################
# LED GPIO PINS
LEDS: dict[str, int] = {
    LED_PLAY: 15,
    LED_PRESET1: 24,
    LED_PRESET2: 25,
    LED_PRESET3: 7,
    LED_STOP: 23
}
# BUTTONS GPIO PINS
BUTTONS: dict[str, int] = {
    BUTTON_PLAY: 9,
    BUTTON_PRESET1: 11,
    BUTTON_PRESET2: 5,
    BUTTON_PRESET3: 10,
    BUTTON_STOP: 6,
}
BOUNCE_MS = 10  # hardware debounce in GPIO.add_event_detect
LED_ON = True
LED_OFF = False


@singleton
class GPIOService:
    """
    Thread-safe class for GPIO control and status.
    - Set the output pins for the configured LED pins 
    - Reading the inputs for the configured BUTTON pins
    - Callback for button change event
    - Log info/warnings/errors for debugging.
    Raises:
    Attributes:
        GPIO_MODULE_TEST:
            TEST_DISABLED = The module test is disabled (default)
            TEST_ENABLED  = The module test is enabled, additional code is provided
    """
    GPIO_MODULE_TEST = TEST_DISABLED

    def __init__(self) -> None:
        """
        Initialize and setup the GPIO
        """
        self._lock = Lock()
        self.edge_event_callback = None
        # Fast channel -> name lookup
        self.gpio_to_button = {}
        # The GPIO.BCM refers to a numbering system used in the RPi.GPIO library for Raspberry Pi,
        # The GPIO pins are based on the Broadcom chip's pin numbers, so set to GPIO.BCM
        GPIO.setmode(GPIO.BCM)
        # Initialize the configured LED pins
        for _, pin in LEDS.items():
            try:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            except RuntimeError as err:
                oradio_log.error("Error setting LED output for pin %s: %s", pin, err)
        oradio_log.debug("LEDControl initialized: All LEDs OFF")
        # Initialize the BUTTON pins
        for button_name, pin in BUTTONS.items():
            try:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            except RuntimeError as err:
                oradio_log.error("Error setting BUTTON input for pin %s: %s", pin, err)
            # dictionary for a fast channel -> name lookup
            self.gpio_to_button[pin] = button_name
            # Ensure clean slate; ignore if not previously set
            GPIO.remove_event_detect(pin)
            # The remove_event_detect is a silent function, will not raise error or exception
            # will disable event detection if active
            try:
                GPIO.add_event_detect(pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS)
            except RuntimeError as err:
                oradio_log.error("Error setting up event detection for pin %s: %s", pin, err)
        oradio_log.debug("Buttons initialized")

    def gpio_cleanup(self) -> None:
        """
        Reset the GPIO pins to their default state.
        It resets any ports which have been used and puts the port in default state
        The default state is input-mode.
        Mainly used in test environments, to get pins in the default state 
        """
        with self._lock:
            GPIO.cleanup()

    def _read_pin_state(self, io_pin: int) -> bool:
        """
        read the state of the specified io-pin
        Args:
            io_pin: int = which pin to read
        Returns:
            True = pin is HIGH
            False = pin is LOW
        """
        with self._lock:
            return bool(GPIO.input(io_pin))

################## methods for the LED pins ######################
    def set_led_on(self, led_name: str) -> None:
        """
        Turns ON the specified LED.
        Args: 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
        else:
            with self._lock:
                GPIO.output(LEDS[led_name], GPIO.LOW)

    def set_led_off(self, led_name: str) -> None:
        """
        Turns OFF the specified LED.
        Args: 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
        else:
            with self._lock:
                GPIO.output(LEDS[led_name], GPIO.HIGH)

    def get_led_state(self, led_name: str) -> Tuple[bool, Optional[str]]:
        """
        Get the state off the specified LED.
        Args: 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | 
                                                   LED_PRESET3 ]
        Returns:
            True = LED is ON
            False = LED is OFF
            None = Unknown led_name
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
            led_state = None
        else:
            with self._lock:
                led_state = not self._read_pin_state(LEDS[led_name])
            # Note led on ==> GPIO.LOW,
        return led_state

######### methods for BUTTON pins ########################

    def set_button_edge_event_callback(self, callback) -> None:
        """
        Set the callback for a change (edge_event) on a button state
        The callback will process the change event
        Args:
            callback (Callable): the reference to the callback function, upon an button event
        Returns:
        """
        if callable(callback):
            self.edge_event_callback = callback
        else:
            oradio_log.error("Callback function does not exists")

    def get_button_state(self, button_name: str) -> Tuple[bool, Optional[str]]:
        """
        Get the state off the specified button.
        Args: 
            button_name (str) precondition: must be [ BUTTON_PLAY | BUTTON_STOP] |
                                                   BUTTON_PRESET1 | BUTTON_PRESET2 | 
                                                   BUTTON_PRESET3 ]
        Returns:
            button_state = True/False | None
                True = BUTTON is ON (so pressed/touched)
                False = BUTTON is OFF (so not pressed/touched)
                None = Unknown button name
        """
        if button_name not in BUTTON_NAMES:
            oradio_log.error("Unknown button name: %s", button_name)
            button_state = None
        else:
            with self._lock:
                button_state = not self._read_pin_state(BUTTONS[button_name])
            # Note: a pressed button has value GPIO.LOW
        return button_state

    def _edge_callback(self, channel: int) -> None:
        """
        Unified handler for both press (falling) and release (rising) edges.
        One callback as button handling is the same for rising as for falling edge.
        Only difference is the state of the button. To prevent duplicated-code
        Called by gpio event detection.
        When channel has a known button_name, the configured callback is called
        Args: 
            channel (int) is the I/O-pin which detected an edge event
        Attributes:
            GPIO_MODULE_TEST
                TEST_ENABLED :
                    * extra timestamp data added to callback
                                for performance measurements
                    * state = BUTTON_PRESSED
                TEST_DISABLED = Default mode, no extra data for testing
        Returns:
            False (default): when channel refers to an unknown pin/button_name
            True : The button_name of the pin is found and callback is called 
        """
        if self.GPIO_MODULE_TEST == TEST_ENABLED:
            button_event_ts = perf_counter()  # timestamp the start of this function
        button_data = {}
        button_name = self.gpio_to_button[channel]
        if not button_name:
            return
        button_value = GPIO.input(channel)
        if button_value == GPIO.LOW:
            state = BUTTON_PRESSED
        else:
            state = BUTTON_RELEASED
        button_data["state"] = state
        button_data["name"] = button_name
        if self.edge_event_callback:
            if self.GPIO_MODULE_TEST == TEST_ENABLED:
                # When TEST_ENABLED, the module test requires the button_data, being:
                # button state = BUTTON_PRESSED
                # timing data = current time-stamp
                button_data["state"] = BUTTON_PRESSED
                button_data["data"] = button_event_ts
            self.edge_event_callback(button_data)
        else:
            oradio_log.error("no callback function found")


# Entry point for stand-alone operation
if __name__ == '__main__':
    print("Stand-alone not implemented")
