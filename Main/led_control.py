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
    * On/Off a led
    * On/Off all leds
    * Blinking
    * Oneshot blink

"""
from threading import Thread, Timer, Event
from singleton import singleton
##### oradio modules ####################
from oradio_logging import oradio_log
from gpio_service import GPIOService

##### GLOBAL constants ####################
from oradio_const import (LED_NAMES)

@singleton
class LEDControl:
    """
    Control the LEDs:
    * On/Off a led
    * On/Off all leds
    * Blinking
    * Oneshot blink
    """
    def __init__(self):
        """
        Class constructor: setup class variables
        Uses an instance of GPIOService class for LED IO-service
        """
        self.leds_driver = GPIOService()
        self.blink_stop_events = {}     # map led_name → threading.Event()
        self.blinking_threads = {}      # map led_name → Thread
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name:str) -> None:
        """
        Turns off a specified LED and waits for its blink‐thread to exit.
        :Args
            led_name (str): [precondition] shall be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
        """
        if led_name in LED_NAMES:
            self._stop_blink(led_name)
            self.leds_driver.set_led_off(led_name)
            #oradio_log.debug("%s turned off", led_name)
        else:
            oradio_log.error("Invalid LED name: %s", led_name)

    def turn_on_led(self, led_name:str) -> None:
        """
        Turns ON a specified LED and stops blink‐thread if active.
        :Args
            led_name (str): [precondition] shall be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
        """
        if led_name in LED_NAMES:
            # leds off with an implicit stop blinking silently then light it
            self.turn_off_led(led_name)
            self.leds_driver.set_led_on(led_name)
            oradio_log.debug("%s turned on", led_name)
        else:
            oradio_log.error("Invalid LED name: %s", led_name)

    def turn_off_all_leds(self) ->None:
        """
        Stops all led blinking and related threads and 
        turns every LED off.
        """
        for led_name in LED_NAMES:
            self.turn_off_led(led_name)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_all_leds(self) -> None:
        """
        Stops all led blinking and related threads and 
        turns every LED on.
        """
        for led_name in LED_NAMES:
            self.turn_on_led(led_name)
        oradio_log.debug("All LEDs turned ON and blinking stopped")

    def oneshot_on_led(self,
                       led_name : str,
                       period: float=3) ->None:
        """
        Turns on a specific LED and then turns it off after a delay.
        :Args
            led_name : [precondition] shall be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
            period : Time in seconds before turning off the LED. Default = 3
        """
        def oneshot_off_led(led_name, period):
            self.turn_off_led(led_name)
            oradio_log.debug("%s turned off after %s seconds", led_name, period)

        if period > 0:
            period = round(period,1) # more accuracy not visible, so not required
            if led_name in LED_NAMES:
                # Stop any blinking for this LED and turn it on
                self.turn_on_led(led_name)
                oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, period)
                oneshot_timer = Timer(period,oneshot_off_led, args=(led_name,period))
                oneshot_timer.start()
            else:
                oradio_log.error("Invalid LED name: %s", led_name)
        else:
            # no valid period, no timer started
            oradio_log.warning("Invalid period time of %f for oneshot of led: %s",period, led_name)

    def get_led_state(self, led_name:str)-> bool:
        """
        Get the state of the selected led_name
        :Args
            led_name = [ LED_PLAY | LED_STOP | LED_PRESET1 | LED_PRESET2 | LED_PRESET3]
        :Returns:
            True = Led is ON
            False = led is OFF
        """
        return self.leds_driver.get_led_state(led_name)

    def control_blinking_led(self,
                             led_name: str,
                             cycle_time:float = 2) -> None:
        """
        Blink the specified led,
        An Event is used for blink timing and instant stop, 
        :Args
            led_name (str): [precondition] shall be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3]
            cycle_time (float) = duration of one complete cycle for blinking  
        """

        def _blink(stop_evt: Event):
            """
            cycle_time (float) = 
            _________|^^^^^^^^^^^|____________|^^^^^^^^^^^^|____________|^^
                     |<====== cycle_time ====>| 
                     |<== half =>|
            """
            half = cycle_time / 2
            while not stop_evt.is_set():
                self.leds_driver.set_led_on(led_name)
                if stop_evt.wait(half):
                    break
                self.leds_driver.set_led_off(led_name)
                if stop_evt.wait(half):
                    break
            self.leds_driver.set_led_off(led_name)

        # if no cycle_time, just turn off selected LED
        if cycle_time is not None and cycle_time >0:
            if led_name in LED_NAMES:
                # stop and remove any existing blink for selected led
                self._stop_blink(led_name)
                # start new blink thread for selected led
                stop_evt = Event()
                self.blink_stop_events[led_name] = stop_evt
                thread = Thread(target=_blink, args=(stop_evt,), daemon=True)
                thread.start()
                self.blinking_threads[led_name] = thread
                #Henk: oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)
                oradio_log.debug("%s blinking started:", led_name)
            else:
                oradio_log.error("Invalid LED name: %s", led_name)
        else:
            self.turn_off_led(led_name)
            oradio_log.debug("%s blinking stopped and turned off", led_name)

    def _stop_blink(self, led_name) -> None:
        """
        Stop blink of selected led and stop related active threads
        :Args
            led_name (str): [precondition] shall be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
        """
        # signal any blink thread to stop
        running_stop_event = self.blink_stop_events.pop(led_name, None)
        if running_stop_event:
            running_stop_event.set()
        # remove led_name from the blinking_threads dictionary
        # and wait until selected thread really finishes
        active_thread = self.blinking_threads.pop(led_name, None)
        if active_thread:
            active_thread.join()
