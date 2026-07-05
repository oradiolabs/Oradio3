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
        * Register a callback for button edge events

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from time import perf_counter
from threading import Lock
from RPi import GPIO

##### Oradio modules ######################################
from log_service import oradio_log
from singleton import singleton
from messaging import (
    Incidents,
    IncidentMessage,
    GPIO_SOURCE,
    GPIO_INCIDENT_SERVICE,
    GPIO_INCIDENT_BUTTONS,
)

##### GLOBAL constants ####################################
from constants import (
    LED_NAMES, LED_PLAY, LED_STOP,
    LED_PRESET1, LED_PRESET2, LED_PRESET3,
    BUTTON_NAMES, BUTTON_PLAY, BUTTON_STOP,
    BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3,
    BUTTON_PRESSED, BUTTON_RELEASED,
    TEST_ENABLED, TEST_DISABLED
)

##### LOCAL constants #####################################
# LED GPIO PINS
LEDS: dict[str, int] = {
    LED_PLAY   : 15,
    LED_STOP   : 23,
    LED_PRESET1: 24,
    LED_PRESET2: 25,
    LED_PRESET3: 7,
}
# BUTTONS GPIO PINS
BUTTONS: dict[str, int] = {
    BUTTON_PLAY   : 9,
    BUTTON_STOP   : 6,
    BUTTON_PRESET1: 11,
    BUTTON_PRESET2: 5,
    BUTTON_PRESET3: 10,
}

# Software debounce window in milliseconds passed to GPIO.add_event_detect.
# 10 ms is intentionally short: the higher-level state machine handles
# sustained-press logic, so we only need to suppress contact chatter.
BOUNCE_MS = 10

LED_ON = True
LED_OFF = False

@singleton
class GPIOService:
    """
    Thread-safe singleton for GPIO control and status.

    Manages output pins for configured LEDs and input pins for configured
    buttons. Supports registering a callback for button edge events and
    provides logging for debugging.

    Public attributes:
        gpio_module_test (int): Controls test mode behaviour.
            TEST_DISABLED (default): normal operation.
            TEST_ENABLED: overrides button state to BUTTON_PRESSED and
                attaches a perf_counter timestamp to button callbacks for
                performance measurement.
            This is intentionally a CLASS attribute (not set in __init__):
            test code sets it via GPIOService.gpio_module_test = TEST_ENABLED
            before the singleton is constructed, or toggles it on the class
            at any time afterwards. Because GPIOService is a singleton, an
            instance-level assignment here would shadow the class attribute
            and silently break that external test-mode toggle pattern.

    Internal attributes:
        edge_event_callback: Callable registered via
            set_button_edge_event_callback(); invoked on every button edge.
        gpio_to_button (dict[int, str]): Reverse map from BCM pin number to
            button name, built at init time to avoid dict iteration inside
            the interrupt handler.
    """
    gpio_module_test = TEST_DISABLED

    def __init__(self) -> None:
        """
        Initialise and configure all GPIO pins.

        Sets BCM pin numbering, configures LED pins as outputs (initially
        HIGH, i.e. off), configures button pins as inputs with pull-up
        resistors, builds the channel-to-button-name reverse lookup used in
        _edge_callback to avoid dict iteration at interrupt time, and clears
        any previously registered edge-detection handlers.
        """
        self._lock = Lock()
        self.edge_event_callback = None

        # Fast channel -> name reverse lookup used in _edge_callback.
        self.gpio_to_button = {}

        # GPIO.BCM uses Broadcom chip pin numbers rather than physical board positions.
        GPIO.setmode(GPIO.BCM)

        # Initialise configured LED pins as outputs, starting HIGH (off).
        for _, pin in LEDS.items():
            try:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            except RuntimeError as err:
                oradio_log.error("Error setting LED output for pin %s: %s", pin, err)
                Incidents.publish(IncidentMessage(GPIO_SOURCE, GPIO_INCIDENT_SERVICE))

        # Initialise button pins as inputs with internal pull-up resistors.
        for button_name, pin in BUTTONS.items():
            try:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            except RuntimeError as err:
                oradio_log.error("Error setting BUTTON input for pin %s: %s", pin, err)
                Incidents.publish(IncidentMessage(GPIO_SOURCE, GPIO_INCIDENT_SERVICE))

            self.gpio_to_button[pin] = button_name

            # Ensure clean slate: disable if set, do nothing if not previously set.
            GPIO.remove_event_detect(pin)

        oradio_log.debug("GPIO initialized")

    def gpio_cleanup(self) -> None:
        """
        Reset all GPIO pins to their default input state.

        Calls GPIO.cleanup() to release all configured pins and return them
        to input mode with no pull-up or pull-down resistors.

        Warning:
            Calling this in production will disable all GPIO functionality
            until the service is re-initialised. Intended primarily for test
            environments where pins must be returned to a known state between
            test runs.
        """
        with self._lock:
            GPIO.cleanup()

    def _read_pin_state(self, io_pin: int) -> bool:
        """
        Read the current logic level of the specified GPIO pin.

        Args:
            io_pin (int): BCM pin number to read.

        Returns:
            bool: True if the pin is HIGH, False if LOW.
        """
        with self._lock:
            return bool(GPIO.input(io_pin))

##### methods for the LED pins ############################

    def set_led_on(self, led_name: str) -> None:
        """
        Turn on the specified LED.

        Has no effect and logs an error if led_name is not recognised.

        Args:
            led_name (str): One of LED_PLAY, LED_STOP, LED_PRESET1,
                LED_PRESET2, LED_PRESET3.
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
        else:
            with self._lock:
                GPIO.output(LEDS[led_name], GPIO.LOW)

    def set_led_off(self, led_name: str) -> None:
        """
        Turn off the specified LED.

        Has no effect and logs an error if led_name is not recognised.

        Args:
            led_name (str): One of LED_PLAY, LED_STOP, LED_PRESET1,
                LED_PRESET2, LED_PRESET3.
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
        else:
            with self._lock:
                GPIO.output(LEDS[led_name], GPIO.HIGH)

    def get_led_state(self, led_name: str) -> bool | None:
        """
        Return the current state of the specified LED.

        Args:
            led_name (str): One of LED_PLAY, LED_STOP, LED_PRESET1,
                LED_PRESET2, LED_PRESET3.

        Returns:
            bool: True if the LED is on, False if off.
            None: If led_name is not recognised; an error is also logged.
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Unknown led name: %s", led_name)
            return None

        # LEDs are active-low: GPIO.LOW means ON, GPIO.HIGH means OFF.
        return not self._read_pin_state(LEDS[led_name])

##### methods for BUTTON pins #############################

    def set_button_edge_event_callback(self, callback) -> None:
        """
        Register the callback invoked on every button edge event.

        Must be called before enable_button_events() to avoid a race
        condition where a button press fires before the callback is set,
        causing the event to be silently dropped.

        Args:
            callback (Callable): Function to call when a button state
                changes. Receives a dict with "state" and "name" keys.
        """
        if callable(callback):
            self.edge_event_callback = callback
        else:
            oradio_log.error("Callback is not callable")

    def get_button_state(self, button_name: str) -> bool | None:
        """
        Return the current state of the specified button.

        Args:
            button_name (str): One of BUTTON_PLAY, BUTTON_STOP,
                BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3.

        Returns:
            bool: True if the button is pressed, False if released.
            None: If button_name is not recognised; an error is also logged.
        """
        if button_name not in BUTTON_NAMES:
            oradio_log.error("Unknown button name: %s", button_name)
            return None

        # A pressed button reads GPIO.LOW; invert so True means pressed.
        return not self._read_pin_state(BUTTONS[button_name])

    def enable_button_events(self) -> None:
        """
        Enable GPIO edge detection on all button pins.

        Must be called after set_button_edge_event_callback(). If events
        were enabled first, a button press occurring between the two calls
        would invoke a None callback and be silently dropped. Publishes
        GPIO_INCIDENT_BUTTONS and returns early if no callback has been
        registered.
        """
        if not callable(self.edge_event_callback):
            oradio_log.error("Cannot enable button events: callback not set")
            Incidents.publish(IncidentMessage(GPIO_SOURCE, GPIO_INCIDENT_BUTTONS))
            return

        for pin in BUTTONS.values():
            try:
                # Ensure clean slate: disable if set, do nothing if not previously set.
                GPIO.remove_event_detect(pin)
                GPIO.add_event_detect(pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS)
            except RuntimeError as err:
                oradio_log.error("Error enabling event detection for pin %s: %s", pin, err)

        oradio_log.debug("Button event detection enabled")

    def _edge_callback(self, channel: int) -> None:
        """
        Unified handler for both press (falling) and release (rising) edges.

        Called by the RPi.GPIO event detection system when any button pin
        changes state. Looks up the button name from the channel number,
        reads the current pin level to determine press or release, and
        forwards the event to the registered callback.

        Args:
            channel (int): BCM pin number on which the edge was detected.

        Note:
            When gpio_module_test is TEST_ENABLED, the reported state is
            unconditionally overridden to BUTTON_PRESSED (regardless of
            actual pin level) and a perf_counter timestamp is attached under
            the "data" key. This behaviour exists solely for performance
            measurement and must not be relied upon in production code.
        """
        if not callable(self.edge_event_callback):
            oradio_log.error("No callback function found")
            return

        # Capture timestamp immediately if test mode is active so the
        # measurement reflects the true start of this handler.
        button_event_ts = perf_counter() if self.gpio_module_test == TEST_ENABLED else None

        button_name = self.gpio_to_button.get(channel)
        if not button_name:
            oradio_log.warning("Edge event on unknown channel %s — ignored", channel)
            return

        button_value = GPIO.input(channel)
        state = BUTTON_PRESSED if button_value == GPIO.LOW else BUTTON_RELEASED

        button_data = {
            "state": state,
            "name": button_name,
        }

        if self.gpio_module_test == TEST_ENABLED:
            # Override state to BUTTON_PRESSED and attach the timing
            # timestamp for performance measurement.
            button_data["state"] = BUTTON_PRESSED
            button_data["data"] = button_event_ts

        self.edge_event_callback(button_data)

##### Stand-alone entry point #############################

if __name__ == '__main__':
    print("Stand-alone not implemented")
    print("The module test for gpio_service.py is at module_test/gpio_service_test.py")
