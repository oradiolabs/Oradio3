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
    * Classes for testing and simulations
"""
from oradio_const import (BUTTON_NAMES)
    
class TimingData():
    """
    This class keeps track on timing measurements for performance statistics:
    min_time:  the minimal timing measured
    max_time:  the maximal timing measured
    sum_time:  the sum of all measured timings
    sum_count: the number of measured timings
    avg_time:  the average timing
    valid_callbacks: The number of valid callbacks for each button
    self.neglected_callback: The number of neglected callbacks for each button
    """
    def __init__(self):
        """
        Create instance
        """
        self._reset_timing_data()
        
    def _reset_timing_data(self):
        """
        Set/Reset the timing data for performance measurements
        """
        # timing data
        self.min_time  = 10000      # the minimal timing measured
        self.max_time  = 0          # the maximal timing measured
        self.sum_time  = 0.0        # the sum of all measured timings
        self.sum_count = 0          # the number of measured timings
        self.avg_time  = 0.0        # the average timing
        self.valid_callbacks    = {} # The number of valid callbacks for each button
        self.neglected_callback = {} # The number of neglected callbacks for each button
        for button in BUTTON_NAMES:
            self.valid_callbacks[button] = 0
            self.neglected_callback[button] = 0   
