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
from multiprocessing import Queue
from time import monotonic
from singleton import singleton

##### oradio modules ####################
from oradio_logging import oradio_log
from gpio_service import GPIOService
from oradio_utils import safe_put, OradioMessage
from module_test.touch_buttons_test_classes import TestGPIOService, TimingData
from system_sounds import play_sound

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
    BUTTON_PLAY, BUTTON_RELEASED,
    TEST_ENABLED, TEST_DISABLED,
    MESSAGE_BUTTON_SOURCE, MESSAGE_BUTTON_SHORT_PRESS, MESSAGE_BUTTON_LONG_PRESS,
    MESSAGE_NO_ERROR,
    SOUND_CLICK
)

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500 # ms, ignore rapid repeats
DEBOUNCE_SECONDS     = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS            = 10 # hardware debounce in GPIO.add_event_detect
LONG_PRESS_DURATION  = 6  # seconds
BUTTON_LONG_PRESSED  = "button long pressed"
VALID_LONG_PRESS_BUTTONS = [BUTTON_PLAY]

@singleton
class TouchButtons:
    """
    Handle GPIO-based touch buttons applying software debouncing.
    Evaluates the touch_buttons timing to determine whether button press is 
    short-press callbacks or along-press callbacks. 
    Attrributes:
        BUTTONS_MODULE_TEST:
            TEST_DISABLED = The module test is disabled (default)
            TEST_ENABLED  = The module test is enabled, additional code is provided
    """
    BUTTONS_MODULE_TEST = TEST_DISABLED
    def __init__(self, queue: Queue):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for button IO-service
        Args:
            queue: the shared message queue
        """
        # Check if module test is enabled
        # if enabled load the TestGPIOService() with extra test features
        if self.BUTTONS_MODULE_TEST == TEST_DISABLED:
            self.button_gpio = GPIOService()
        else:
            self.button_gpio = TestGPIOService()
        self.button_gpio.set_button_edge_event_callback(self._button_event_callback)
        self.message_queue = queue
        self.button_press_times: dict[str, float] = {}   # keep track on button press timings
        self.last_trigger_times: dict[str, float] = {}   # keep track on last button press timings
        self.long_press_timers: dict[str, Timer] = {}    # button -> Timer
        if self.BUTTONS_MODULE_TEST == TEST_ENABLED:
            # include button press timing data for statistics
            self.timing_data = TimingData()

    def _reset_timing_data(self) -> None:
        """
        Reseting the timing data class
        """
        self.timing_data.reset()

    def _send_message(self, button_data: dict) -> None:
        """
        Send current TouchButton state message to the registered queue.
        Args:
            button_data = { 'name': str,   # name of button
                            'state': str,  # state of button Pressed/Released
                           }
            state = [BUTTON_PRESSED | BUTTON_RELEASED | BUTTON_LONG_PRESSED
            name = [BUTTON_PLAY | BUTTON_STOP |
                    BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        Attributes:
        if TEST_ENABLED a data key is added with extra timestamp data
        {
          'data': float # timestamp
        }
        """
        message = {}
        message["source"] = MESSAGE_BUTTON_SOURCE
        message["error"]  = MESSAGE_NO_ERROR
        if button_data["state"] == BUTTON_LONG_PRESSED:
            message["state"] = MESSAGE_BUTTON_LONG_PRESS+button_data["name"]
        else:
            message["state"] = MESSAGE_BUTTON_SHORT_PRESS+button_data["name"]
        if self.BUTTONS_MODULE_TEST == TEST_ENABLED:
            data_list = []
            if "data" in button_data:
                data_list.append(button_data["data"])
                message["data"] = data_list
        oradio_msg = OradioMessage(**message)
        oradio_log.debug("Send TouchButton message: %s", oradio_msg)
        if not safe_put(self.message_queue, oradio_msg):
            oradio_log.error("Failure when sending message to shared queue")

    def _button_event_callback(self, button_data: dict) -> None:
        """
        callback for button events
        Args:
            button_data = { 'name': str,   # name of button
                            'state': str,  # state of button Pressed/Released
                           }
            state = [BUTTON_PRESSED | BUTTON_RELEASED | BUTTON_LONG_PRESSED
            name = [BUTTON_PLAY | BUTTON_STOP |
                    BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        Attributes:
        if TEST_ENABLED a data key is added with extra timestamp data
        {
          'data': float # timestamp
        }
        """
        button_name = button_data["name"]
        oradio_log.debug("Button change event: %s = %s", button_name, button_data['state'])
        if button_data["state"] == BUTTON_RELEASED:
            # cancel pending long-press timer (if any)
            timer = self.long_press_timers.pop(button_name, None)
            if timer:
                timer.cancel()
            return
        # a button press detected
        now = monotonic()
        last = self.last_trigger_times.get(button_name, 0.0)
        time_diff = now-last
        if (time_diff) < DEBOUNCE_SECONDS:
            # another button press detected within the debounce period
            # is considered to be a new button press.
            # The button press was to short, so will be neglected
            if self.BUTTONS_MODULE_TEST == TEST_ENABLED:
                print(f"{YELLOW}New {button_name} event in {round(time_diff, 3)} sec",
                      f",events within the debouncing window of {DEBOUNCE_SECONDS}",
                      f" will be neglected{NC}"
                    )
                self.timing_data.neglected_callback[button_name] +=1
            return  # software debounce
        self.last_trigger_times[button_name] = now
        self.button_press_times[button_name] = now
        # Cancel any existing timer, then arm a fresh one
        prev = self.long_press_timers.pop(button_name, None)
        if prev:
            prev.cancel()
        timer = Timer(LONG_PRESS_DURATION,
                        self._long_press_timeout,
                        args=(button_name,))
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()
        play_sound(SOUND_CLICK)
        self._send_message(button_data)

    def _long_press_timeout(self, button_name: str) -> None:
        """
        Fire long-press if still held after LONG_PRESS_DURATION.
        If button is in the list of VALID_LONG_PRESS_BUTTONS,
        it is allowed to put message in queue to inform controls
        Args:
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        """

        if not self.button_gpio.get_button_state(button_name):
            return  # released during wait; ignore
        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)
        button_data = {}
        if button_name in VALID_LONG_PRESS_BUTTONS:
            button_data["name"]  = button_name
            button_data["state"] = BUTTON_LONG_PRESSED
            self._send_message(button_data)

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("Stand-alone not implemented")
