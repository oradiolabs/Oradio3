#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Jan 22, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       
    * Class extensions for touch buttons testing and simulations
"""
from threading import Event
from time import sleep, perf_counter
##### local oradio import modules ####################
from RPi import GPIO
from gpio_service import GPIOService, BUTTONS
from oradio_const import (
    BUTTON_NAMES, BUTTON_PLAY,
    TEST_DISABLED,
    YELLOW, NC,
    )

class TimingData:
    """
    Class for timing data statistics during testing
    """
    def __init__(self):
        self.reset()

    def reset(self):
        """
        reseting the timing data
        """
        self.min_time  = 10000      # the minimal timing measured
        self.max_time  = 0          # the maximal timing measured
        self.sum_time  = 0.0        # the sum of all measured timings
        self.sum_count = 0          # the number of measured timings
        self.avg_time  = 0.0        # the average timing
        self.valid_callbacks    = {}# The number of valid callbacks for each button
        self.neglected_callback = {}# The number of valid callbacks for each button
        for button in BUTTON_NAMES:
            self.valid_callbacks[button]=0
        self.neglected_callback = {}
        for button in BUTTON_NAMES:
            self.neglected_callback[button]=0

class TestGPIOService(GPIOService):
    """
    Class with additional methods for testing purposes only
    Based on GPIOService baseclass
    :Args
        The new class inherits from GPIOService, and extends it with extra test methods:
        * simulate_button_play_events_burst()
        * simulate_all_buttons_events_burst()
        * simulate_button_press_and_release()
    """

    def simulate_button_play_events_burst(self,
                                          burst_freq: int,
                                          stop_burst: Event) -> tuple[bool,int]:
        """ 
        simulate a button press by submitting a callback for BUTTON_PLAY
        :Args
            burst_freq = number of events per second
            stop_burst = an event to stop the burst
        :Returns
            status = True/False
                    False = Test is disabled
                    True = Test is enabled
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        status = True
        if self.GPIO_MODULE_TEST == TEST_DISABLED:
            status = False
        else:
            while not stop_burst.is_set():
                self._edge_callback(BUTTONS[BUTTON_PLAY])
                nr_of_events +=1
                sleep(1/burst_freq)
        return status, nr_of_events

    def simulate_all_buttons_events_burst(self,
                                          burst_freq: int,
                                          stop_burst: Event) -> tuple[bool,int]:
        """ 
        simulate all button press by submitting a callback for all buttons in a sequence
        :Args
            burst_freq = nr of events per second
            stop_burst = an event to stop the burst
        :Returns
            status = True/False
                    False = Test is disabled
                    True = Test is enabled
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        status = True
        if self.GPIO_MODULE_TEST == TEST_DISABLED:
            status = False
        else:
            while not stop_burst.is_set():
                for button in BUTTON_NAMES:
                    self._edge_callback(BUTTONS[button])
                    nr_of_events +=1
                sleep(1/burst_freq)
        return status, nr_of_events

    def simulate_button_press_and_release(self,
                                          button_name: str,
                                          press_timing : float)-> None:
        """ 
        simulate a BUTTON_STOP button press according specified press timing,
        by submitting a callback for specified button
        :Args
            button_name = name of button [ BUTTON_PLAY | BUTTON_STOP] |
                                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3 ]
            press_timing = press time in float seconds for BUTTON_STOP 
        """
        # set the button pin to an output with GPIO,LOW as a button press
        GPIO.setup(BUTTONS[button_name], GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(BUTTONS[button_name], GPIO.LOW)
        self._edge_callback(BUTTONS[button_name])
        # show a progressing time indicator during press period
        start_time = perf_counter()
        elapsed_time = 0.0
        while elapsed_time < press_timing:
            sleep(0.2)
            print(f"{YELLOW}*", end=" ", flush=True)
            elapsed_time = perf_counter()-start_time
        print(f"{YELLOW}button press timing was {NC} ",press_timing, end=" ", flush=True)
        # set the button pin to GPIO,HIGH as a button release
        GPIO.output(BUTTONS[button_name], GPIO.HIGH)
        self._edge_callback(BUTTONS[button_name])
        # reset the button pin back to an input
        GPIO.setup(BUTTONS[button_name], GPIO.IN, pull_up_down=GPIO.PUD_UP)
