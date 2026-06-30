#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 22, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Classes for testing and simulations
"""
##### GLOBAL constants ####################################
from constants import BUTTON_NAMES

class TimingData:
    """
    Accumulates timing measurements for performance statistics.

    avg_time is a computed property derived from sum_time and sum_count;
    callers do not need to update it manually.

    Attributes:
        min_time (float):             Minimum measured time. Initialised to
                                      infinity so any real measurement becomes
                                      the new minimum immediately.
        max_time (float):             Maximum measured time.
        sum_time (float):             Sum of all measured times.
        sum_count (int):              Number of measurements recorded.
        valid_callbacks (dict):       Count of valid callbacks per button name.
        neglected_callbacks (dict):   Count of neglected callbacks per button name.
    """

    # Guarantees any real measurement is recorded as the new minimum.
    _MIN_TIME_INIT = float("inf")

    def __init__(self):
        """
        Initialise all timing counters to their starting values.

        min_time is set to infinity so that any real measurement is
        immediately recorded as the new minimum.
        """
        self._reset_timing_data()

    def _reset_timing_data(self) -> None:
        """Reset all timing data and callback counters to their initial values."""
        self.min_time  = self._MIN_TIME_INIT
        self.max_time  = 0.0
        self.sum_time  = 0.0
        self.sum_count = 0
        self.valid_callbacks     = {button: 0 for button in BUTTON_NAMES}
        self.neglected_callbacks = {button: 0 for button in BUTTON_NAMES}

    @property
    def avg_time(self) -> float:
        """Average measured time, computed from sum_time and sum_count."""
        return self.sum_time / self.sum_count if self.sum_count > 0 else 0.0

    def __repr__(self) -> str:
        """Return a compact, unambiguous representation for debugging."""
        return (
            f"TimingData("
            f"min={self.min_time:.3f}, "
            f"max={self.max_time:.3f}, "
            f"avg={self.avg_time:.3f}, "
            f"count={self.sum_count})"
        )

    def __str__(self) -> str:
        """Return a human-readable multi-line summary of all timing statistics."""
        lines = [
            "Timing statistics:",
            f"  min  : {self.min_time:.3f}",
            f"  max  : {self.max_time:.3f}",
            f"  avg  : {self.avg_time:.3f}",
            f"  count: {self.sum_count}",
            f"  sum  : {self.sum_time:.3f}",
            "  valid callbacks:",
        ]
        for button, count in self.valid_callbacks.items():
            lines.append(f"    {button}: {count}")
        lines.append("  neglected callbacks:")
        for button, count in self.neglected_callbacks.items():
            lines.append(f"    {button}: {count}")
        return "\n".join(lines)
