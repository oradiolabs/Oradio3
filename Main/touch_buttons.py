#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on April 28, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       4
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio touch buttons module with debounce, per-button callbacks, and selftest
"""
from threading import Timer
from time import monotonic

##### Oradio modules ######################################
from log_service import oradio_log
from gpio_service import GPIOService
from system_sounds import play_sound
from singleton import singleton
from messaging import (
    Commands,
    CommandMessage,
    BUTTON_SOURCE,
)

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    BUTTON_PLAY,
    BUTTON_RELEASED,
    BUTTON_SHORT_PRESS,
    BUTTON_LONG_PRESS,
    TEST_DISABLED,
    TEST_ENABLED,
    SOUND_CLICK,
)

##### LOCAL constants #####################################
BUTTON_DEBOUNCE_TIME     = 500                              # ms — ignore rapid repeats within this window
DEBOUNCE_SECONDS         = BUTTON_DEBOUNCE_TIME / 1000.0    # converted to seconds for monotonic() comparisons
BOUNCE_MS                = 10                               # hardware debounce threshold passed to GPIO.add_event_detect
LONG_PRESS_DURATION      = 6                                # seconds a button must be held to trigger a long-press event
BUTTON_LONG_PRESSED      = "button long pressed"
VALID_LONG_PRESS_BUTTONS = [BUTTON_PLAY]

@singleton
class TouchButtons:
    """
    Handle GPIO-based touch buttons with software debouncing.

    Evaluates button timing to distinguish short-press events from
    long-press events, publishing the appropriate CommandMessage for each.

    Public attributes:
        buttons_module_test (int): Controls test mode behaviour.
            TEST_DISABLED (default): normal operation.
            TEST_ENABLED: adds a TimingData instance for timing statistics
                for performance measurement.
            This is intentionally a CLASS attribute (not set in __init__):
            test code sets it via TouchButtons.buttons_module_test = TEST_ENABLED
            before the singleton is constructed, or toggles it on the class
            at any time afterwards. Because TouchButtons is a singleton, an
            instance-level assignment here would shadow the class attribute
            and silently break that external test-mode toggle pattern.
    """
    buttons_module_test = TEST_DISABLED

    def __init__(self) -> None:
        """
        Set up class variables and register GPIO button callbacks.
        """
        self.button_gpio = GPIOService()
        self.button_press_times: dict[str, float] = {}   # tracks the monotonic time of each button press
        self.last_trigger_times: dict[str, float] = {}   # tracks the last accepted press time per button
        self.long_press_timers: dict[str, Timer] = {}    # maps button name → active long-press Timer

        # Register the callback before enabling interrupts to guarantee no
        # edge event is missed between registration and the enable call.
        self.button_gpio.set_button_edge_event_callback(self._button_event_callback)
        self.button_gpio.enable_button_events()

        if self.buttons_module_test == TEST_ENABLED:
            # Avoid importing test_classes during normal operation; import only in test mode.
            from test_classes import TimingData     # pylint: disable=import-outside-toplevel
            self.timing_data = TimingData()

    def _send_message(self, button_data: dict) -> None:
        """
        Publish a CommandMessage for the given button event.

        Builds the appropriate message string from the button name and state,
        then publishes it to the COMMAND topic via Commands.publish().

        Args:
            button_data (dict): Must contain:
                'name'  (str): One of BUTTON_PLAY, BUTTON_STOP,
                               BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3.
                'state' (str): BUTTON_RELEASED or BUTTON_LONG_PRESSED.
                'data'  (Any, optional): Extra payload attached when
                               buttons_module_test is TEST_ENABLED.
        """
        if button_data["state"] == BUTTON_LONG_PRESSED:
            msg_text = BUTTON_LONG_PRESS + button_data["name"]
        else:
            msg_text = BUTTON_SHORT_PRESS + button_data["name"]

        data = button_data.get("data") if self.buttons_module_test == TEST_ENABLED else None

        command = CommandMessage(
            source=BUTTON_SOURCE,
            message=msg_text,
            data=[data] if data is not None else None,
        )
        oradio_log.debug("Send TouchButton message: %s", command)
        Commands.publish(command)

    def _button_event_callback(self, button_data: dict) -> None:
        """
        Handle a raw GPIO button edge event.

        Applies software debouncing, arms or cancels the long-press timer,
        plays the click sound, and publishes a short-press CommandMessage on
        each accepted press.

        Args:
            button_data (dict): Must contain:
                'name'  (str): One of BUTTON_PLAY, BUTTON_STOP,
                               BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3.
                'state' (str): BUTTON_RELEASED or the GPIO pressed state.
                'data'  (Any, optional): Extra timing payload attached when
                               buttons_module_test is TEST_ENABLED.
        """
        button_name = button_data["name"]
        oradio_log.debug("Button change event: %s = %s", button_name, button_data["state"])

        if button_data["state"] == BUTTON_RELEASED:
            # Cancel the pending long-press timer so a release before
            # LONG_PRESS_DURATION does not fire a long-press event.
            timer = self.long_press_timers.pop(button_name, None)
            if timer:
                timer.cancel()
            return

        # Button press detected — apply software debounce.
        now = monotonic()
        last = self.last_trigger_times.get(button_name, 0.0)
        time_diff = now - last
        if time_diff < DEBOUNCE_SECONDS:
            # Press arrived too soon after the last accepted press;
            # discard it to avoid spurious repeat events.
            if self.buttons_module_test == TEST_ENABLED:
                print(f"{YELLOW}New {button_name} event in {round(time_diff, 3)} sec",
                      f", events within the debouncing window of {DEBOUNCE_SECONDS}",
                      f" will be neglected{NC}"
                    )
                self.timing_data.neglected_callbacks[button_name] += 1
            return

        self.last_trigger_times[button_name] = now
        self.button_press_times[button_name] = now

        # Cancel any existing timer, then arm a fresh one for long-press detection.
        prev = self.long_press_timers.pop(button_name, None)
        if prev:
            prev.cancel()
        timer = Timer(LONG_PRESS_DURATION, self._long_press_timeout, args=(button_name,))
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()

        play_sound(SOUND_CLICK)
        self._send_message(button_data)

    def _long_press_timeout(self, button_name: str) -> None:
        """
        Fire a long-press event if the button is still held after LONG_PRESS_DURATION.

        Only buttons listed in VALID_LONG_PRESS_BUTTONS produce a long-press
        CommandMessage; all others are silently ignored.

        Args:
            button_name (str): One of BUTTON_PLAY, BUTTON_STOP,
                               BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3.
        """
        if not self.button_gpio.get_button_state(button_name):
            return  # Button was released before the timeout fired; ignore.

        # Disarm the timer entry since we are executing the timeout now.
        self.long_press_timers.pop(button_name, None)

        if button_name in VALID_LONG_PRESS_BUTTONS:
            self._send_message({
                "name":  button_name,
                "state": BUTTON_LONG_PRESSED,
            })

##### Stand-alone entry point #############################

if __name__ == '__main__':
    print("Stand-alone not implemented")
    print("The module test for touch_buttons.py is at module_test/touch_buttons_test.py")
