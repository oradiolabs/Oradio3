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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio LED control module, controlling the LED:
    * Turn a single LED on or off
    * Turn all LEDs on or off
    * Blink a LED continuously
    * Turn a LED on for a fixed duration (one-shot)

"""
from threading import Thread, Timer, Event

##### Oradio modules ######################################
from log_service import oradio_log
from gpio_service import GPIOService
from singleton import singleton

##### GLOBAL constants ####################################
from constants import LED_NAMES

@singleton
class LEDControl:
    """
    Control the LEDs:
    * Turn a single LED on or off
    * Turn all LEDs on or off
    * Blink a LED continuously
    * Turn a LED on for a fixed duration (one-shot)
    """
    def __init__(self):
        """
        Class constructor: set up class variables.
        Uses an instance of GPIOService for LED I/O.
        """
        self.leds_driver = GPIOService()
        self.blink_stop_events = {}     # map led_name → threading.Event()
        self.blinking_threads = {}      # map led_name → Thread
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name: str) -> None:
        """
        Turn off a specified LED and wait for its blink thread to exit.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        if led_name in LED_NAMES:
            self._stop_blink(led_name)
            self.leds_driver.set_led_off(led_name)
            oradio_log.debug("%s turned off", led_name)
        else:
            oradio_log.error("Invalid LED name: %s", led_name)

    def turn_on_led(self, led_name: str) -> None:
        """
        Turn on a specified LED, stopping any active blink thread first.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        if led_name in LED_NAMES:
            self.turn_off_led(led_name)
            self.leds_driver.set_led_on(led_name)
            oradio_log.debug("%s turned on", led_name)
        else:
            oradio_log.error("Invalid LED name: %s", led_name)

    def turn_off_all_leds(self) -> None:
        """
        Stop all blink threads and turn every LED off.
        """
        for led_name in LED_NAMES:
            self.turn_off_led(led_name)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_all_leds(self) -> None:
        """
        Stop all blink threads and turn every LED on.
        """
        for led_name in LED_NAMES:
            self.turn_on_led(led_name)
        oradio_log.debug("All LEDs turned ON and blinking stopped")

    def oneshot_on_led(self, led_name: str, period: float = 3) -> None:
        """
        Turn on a specific LED and turn it off automatically after a delay.

        The period is rounded to one decimal place before use, as finer
        resolution is not perceptible.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
            period (float): Time in seconds before turning off the LED.
                            Rounded to one decimal place. Default is 3.
        """
        if period > 0:
            period = round(period, 1)
            if led_name in LED_NAMES:
                self.turn_on_led(led_name)
                oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, period)
                oneshot_timer = Timer(period, self.turn_off_led, args=(led_name,))
                oneshot_timer.start()
            else:
                oradio_log.error("Invalid LED name: %s", led_name)
        else:
            oradio_log.warning("Invalid period time of %f for one-shot of LED: %s", period, led_name)

    def get_led_state(self, led_name: str) -> bool:
        """
        Return the current state of a specified LED.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        Returns:
            True  if the LED is ON.
            False if the LED is OFF.
        """
        if led_name in LED_NAMES:
            return self.leds_driver.get_led_state(led_name)
        oradio_log.error("Invalid LED name: %s", led_name)
        return False

    def control_blinking_led(self, led_name: str, cycle_time: float | None = 2) -> None:
        """
        Start blinking a specified LED at the given cycle time.
        If cycle_time is None or <= 0, the LED is turned off instead.

        The LED is on for the first half of each cycle and off for the second:

            _________|^^^^^^^^^^^|____________|^^^^^^^^^^^^|____________|^^
                     |<====== cycle_time ====>|
                     |<== half =>|

        Args:
            led_name (str):       Must be one of the names defined in LED_NAMES
                                  (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
            cycle_time (float):   Duration in seconds of one complete on/off cycle.
                                  Pass None or a value <= 0 to stop blinking and turn the LED off.
        """
        def _blink(stop_evt: Event, cycle: float) -> None:
            half = cycle / 2
            while not stop_evt.is_set():
                self.leds_driver.set_led_on(led_name)
                if stop_evt.wait(half):
                    break
                self.leds_driver.set_led_off(led_name)
                if stop_evt.wait(half):
                    break
            self.leds_driver.set_led_off(led_name)

        if cycle_time is not None and cycle_time > 0:
            if led_name in LED_NAMES:
                self._stop_blink(led_name)
                stop_evt = Event()
                self.blink_stop_events[led_name] = stop_evt
                thread = Thread(target=_blink, args=(stop_evt,cycle_time), daemon=True)
                thread.start()
                self.blinking_threads[led_name] = thread
                oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)
            else:
                oradio_log.error("Invalid LED name: %s", led_name)
        else:
            self.turn_off_led(led_name)
            oradio_log.debug("%s blinking stopped and turned off", led_name)

    def _stop_blink(self, led_name: str) -> None:
        """
        Signal the blink thread for the given LED to stop, then block until
        the thread has fully exited. The LED is left in the off state by the
        thread itself before it exits.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        running_stop_event = self.blink_stop_events.pop(led_name, None)
        if running_stop_event:
            running_stop_event.set()
        active_thread = self.blinking_threads.pop(led_name, None)
        if active_thread:
            active_thread.join()

##### Stand-alone entry point #############################

if __name__ == '__main__':
    print("Stand-alone not implemented")
    print("The module test for led_control.py is at module_test/led_control_test.py")
