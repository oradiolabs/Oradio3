#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on December 31, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
RPi Throttling Monitor
    Monitors the throttled state of a Raspberry Pi and logs state changes.
    Supports a test mode for forced throttling to validate logging.
"""
from subprocess import check_output
from threading import Thread, Event
from typing import List

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log

##### LOCAL constants ####################

# Bit meanings from vcgencmd documentation
THROTTLE_FLAGS = {
    0x1: "Under-voltage detected",
    0x2: "ARM frequency capped",
    0x4: "Currently throttled",
    0x8: "Soft temperature limit active",

    0x10000: "Under-voltage has occurred",
    0x20000: "ARM frequency capping has occurred",
    0x40000: "Throttling has occurred",
    0x80000: "Soft temperature limit has occurred",
}

# Mask for active flags (used for logging only state changes)
ACTIVE_MASK = 0x1 | 0x2 | 0x4 | 0x8

@singleton
class RPiThrottlingMonitor:
    """Singleton monitor for Raspberry Pi throttling state."""

    def __init__(self, interval: float = 1.0) -> None:
        """
        Initialize the monitor.
        
        Args:
            interval: Polling interval in seconds.
        """
        self.interval = interval
        self._thread = None
        self._stop_event = Event()
        self._last_active_flags = 0

        # Test mode attributes
        self._test_mode = False
        self._forced_value = 0

        # Start the monitor
        self.start()

    def _get_throttle_value(self) -> int:
        """
        Get current throttled state from vcgencmd or test override.

        Returns:
            Integer representing throttled flags.
        """
        if self._test_mode:
            return self._forced_value

        # Query Raspberry Pi for throttled state
        out = check_output(["vcgencmd", "get_throttled"], text=True).strip()
        return int(out.split("=")[1], 16)

    @classmethod
    def _decode_flags(cls, value: int, mask: int) -> List[str]:
        """
        Decode active throttling flags into human-readable strings.

        Args:
            value: Bitmask of throttling flags.
            mask: Mask specifying which flags to decode.

        Returns:
            List of flag descriptions.
        """
        return [
            name for bit, name in THROTTLE_FLAGS.items()
            if (value & bit) and (mask & bit)
        ]

    def _run(self) -> None:
        """Background thread function that polls throttling state."""
        while not self._stop_event.is_set():
            value = self._get_throttle_value()
            active_flags = value & ACTIVE_MASK

            # Only log when state changes
            if active_flags != self._last_active_flags:
                if active_flags:
                    reasons = self._decode_flags(value, ACTIVE_MASK)
                    oradio_log.warning("RPi throttling ENTERED: %s", ", ".join(reasons))
                else:
                    oradio_log.warning("RPi throttling CLEARED")

                self._last_active_flags = active_flags

            # Wait for event or timeout
            self._stop_event.wait(self.interval)

    def start(self) -> None:
        """Start the monitoring thread (singleton)."""
        if self._thread and self._thread.is_alive():
            return  # Already running

        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            name="rpi-throttling-monitor",
            daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitoring thread."""
        self._stop_event.set()

    # ----- Test mode API -----

    def enable_test_mode(self) -> None:
        """Enable test mode for forced throttling."""
        oradio_log.info("RPi throttling monitor TEST MODE enabled")
        self._test_mode = True

    def disable_test_mode(self) -> None:
        """Disable test mode and clear forced throttling."""
        oradio_log.info("RPi throttling monitor TEST MODE disabled")
        self._test_mode = False
        self._forced_value = 0

    def force_throttled(self, flags: int = 0x4) -> None:
        """
        Force throttled state for testing.
        
        Args:
            flags: Bitmask to simulate throttling.
                   Default 0x4 = Currently throttled.
        """
        self.enable_test_mode()
        self._forced_value = flags

    def clear_throttled(self) -> None:
        """Clear any forced throttling state in test mode."""
        self._forced_value = 0

# ----- Instantiate throttled monitor -----

# Instantiate throttling monitor
throttled_monitor = RPiThrottlingMonitor()

# ----- Standalone test menu -----
if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """Interactive console menu to test throttling monitor."""

        # Put monitor in test mode
        throttled_monitor.enable_test_mode()

        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Force RPI throttled (undervoltage)\n"
            " 2-Force RPI throttled (frequency capped)\n"
            " 3-Force RPI throttled (thermal)\n"
            " 4-Force RPI throttled (temperature)\n"
            " 5-Force RPI throttled (all)\n"
            " 6-Clear RPI throttled\n"
            "Select: "
        )

        # User command loop
        while True:

            # Get user input safely
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    throttled_monitor.disable_test_mode()
                    break
                case 1:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttled_monitor.force_throttled(0x1)
                case 2:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttled_monitor.force_throttled(0x2)
                case 3:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttled_monitor.force_throttled(0x4)
                case 4:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttled_monitor.force_throttled(0x8)
                case 5:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttled_monitor.force_throttled(0x1 | 0x2 | 0x4 | 0x8)
                case 6:
                    print("\nClear RPI throttled (TEST MODE)...\n")
                    throttled_monitor.clear_throttled()
                case _:
                    print("\nPlease input a valid number\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
