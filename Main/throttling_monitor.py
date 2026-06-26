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
@summary: RPi Throttling Monitor
    Monitors the throttled state of a Raspberry Pi and logs state changes.
    Supports a test mode for forced throttling to validate logging.

Typical usage:
    This module is self-starting: the module-level throttling_monitor
    instance is created automatically when the module is imported, so no
    explicit initialisation is required by the caller.

    Callers should use the module-level singleton rather than constructing
    their own instance::

        from throttling_monitor import throttling_monitor

    For interactive testing run this file directly::

        python throttling_monitor.py
"""
from subprocess import check_output
from threading import Thread, Event

##### oradio modules ################
from singleton import singleton
from log_service import oradio_log
from messaging import (
    Errors,
    ErrorMessage,
    THROTTLING_SOURCE,
    THROTTLING_ERROR_THROTTLED,
)

##### LOCAL constants ###############

# Throttle flag definitions: Bit meanings from vcgencmd documentation.
# The lower nibble (bits 0-3) reflects the current hardware state; the
# upper nibble (bits 16-19) is a sticky historical record that is set once
# and never cleared until the next reboot.
THROTTLE_FLAGS = {
    # Current state (cleared as soon as condition disappears)
    0x00001: "Under-voltage detected",        # Supply voltage below ~4.63 V
    0x00002: "ARM frequency capped",          # Clock reduced due to thermal/power
    0x00004: "Currently throttled",           # CPU actively running below rated speed
    0x00008: "Soft temperature limit active", # Core approaching thermal threshold

    # Historical events (sticky since last boot)
    0x10000: "Under-voltage has occurred",
    0x20000: "ARM frequency capping has occurred",
    0x40000: "Throttling has occurred",
    0x80000: "Soft temperature limit has occurred",
}

# Bit mask covering only the "current state" flags (bits 0–3).
# Used to detect real-time transitions without being confused by the sticky
# historical flags in the upper word.
ACTIVE_MASK = 0x1 | 0x2 | 0x4 | 0x8

# Bit mask covering only the sticky "historical event" flags (bits 16–19).
# Used at startup to surface throttling events that occurred before the
# monitor started, e.g. a brief brownout at boot time.
HISTORICAL_MASK = 0xFFFF0000

@singleton
class RPiThrottlingMonitor:
    """
    Singleton background monitor for Raspberry Pi throttling state.

    Polls vcgencmd get_throttled at a configurable interval and logs a
    warning (plus publishes an error message) whenever the active throttling
    state changes. A one-time boot-time check is also performed to surface
    any historical throttling events that occurred before the monitor started.

    The class is decorated with @singleton so only one instance ever
    exists, regardless of how many times the constructor is called. Callers
    should use the module-level throttling_monitor instance rather than
    constructing their own.

    Attributes:
        interval (float): Polling interval in seconds between state reads.
    """
    def __init__(self, interval: float = 1.0) -> None:
        """
        Initialise the monitor, run a startup diagnostic, and start polling.

        The constructor intentionally performs side-effects (starting a
        background thread) so that callers simply use the module-level
        throttling_monitor instance and monitoring begins immediately.

        Args:
            interval: Seconds between consecutive vcgencmd polls.
                      Defaults to 1.0.
        """
        self.interval = interval

        # Background worker thread; created in start().
        self._thread = None

        # Event used to signal the polling loop to stop cleanly.
        self._stop_event = Event()

        # Cache of the last observed active-flag combination. Initialised to
        # "no flags set" so the very first poll always produces a log entry if
        # the system is already throttled at startup.
        self._last_active_flags = 0

        # When _test_mode is True, _get_throttle_value() returns _forced_value
        # instead of querying vcgencmd, allowing tests to drive any state.
        self._test_mode = False
        self._forced_value = 0

        # Read the full throttle word (including sticky historical bits) and
        # warn if any throttling event has occurred since the last reboot.
        # This catches problems that resolved themselves before the monitor
        # started (e.g. a brief brownout at boot time).
        value = self._get_throttle_value()
        if value & HISTORICAL_MASK:
            reasons = self._decode_flags(value, HISTORICAL_MASK)
            oradio_log.warning("RPi HEALTH WARNING (since boot): %s", ", ".join(reasons))
            Errors.publish(ErrorMessage(THROTTLING_SOURCE, THROTTLING_ERROR_THROTTLED))

        # Start the background polling thread.
        self.start()

    def _get_throttle_value(self) -> int:
        """
        Return the current throttle bitmask from the hardware or test stub.

        In normal operation the value is obtained by running::

            vcgencmd get_throttled

        which returns a string of the form throttled=0x50000. In test
        mode the pre-configured _forced_value is returned instead.

        Returns:
            Integer bitmask where each set bit indicates a throttling
            condition as described in THROTTLE_FLAGS.
        """
        if self._test_mode:
            # Return the injected test value directly; skip hardware query.
            return self._forced_value

        # Call vcgencmd and parse the hex value after the "=" delimiter.
        out = check_output(["vcgencmd", "get_throttled"], text=True).strip()
        return int(out.split("=")[1], 16)

    @classmethod
    def _decode_flags(cls, value: int, mask: int) -> list[str]:
        """
        Translate a throttle bitmask into a list of human-readable strings.

        Only bits that are both set in value and covered by mask are
        included, which lets callers filter for current-state or historical
        flags independently.

        Args:
            value: Raw bitmask returned by _get_throttle_value.
            mask:  Bit mask specifying which flags are of interest.
                   Use ACTIVE_MASK for current-state flags or
                   HISTORICAL_MASK for historical flags.

        Returns:
            A list of descriptive strings, one per active flag. Returns an
            empty list if no flags match.
        """
        return [
            name
            for bit, name in THROTTLE_FLAGS.items()
            if (value & bit) and (mask & bit)  # Flag is set AND within the requested mask
        ]

    def _run(self) -> None:
        """
        Background thread body: poll throttling state and log changes.

        Runs until _stop_event is set (via stop). Only the
        current-state flags (ACTIVE_MASK) are compared across polls,
        so sticky historical bits do not trigger repeated log entries.

        Each state transition is logged once:
        * Enter throttling – logs a warning with the active flag names
          and publishes an error message.
        * Clear throttling – logs an info message indicating the
          condition resolved.
        """
        while not self._stop_event.is_set():
            value = self._get_throttle_value()

            # Mask to current-state bits only; ignore historical sticky flags.
            active_flags = value & ACTIVE_MASK

            # Only act when the active flags have changed since the last poll.
            if active_flags != self._last_active_flags:
                if active_flags:
                    # One or more throttling conditions just became active.
                    reasons = self._decode_flags(value, ACTIVE_MASK)
                    oradio_log.warning("RPi throttling ENTERED: %s", ", ".join(reasons))
                    Errors.publish(ErrorMessage(THROTTLING_SOURCE, THROTTLING_ERROR_THROTTLED))
                else:
                    # All throttling conditions have cleared.
                    oradio_log.info("RPi throttling CLEARED")

                # Update the cache so the next iteration has a baseline.
                self._last_active_flags = active_flags

            # Block for interval seconds (or until stop() is called).
            # Using wait() instead of sleep() allows stop() to interrupt
            # immediately rather than waiting for the next poll cycle.
            self._stop_event.wait(self.interval)

    def start(self) -> None:
        """
        Start the background polling thread.

        Idempotent: calling start() when the thread is already alive is a
        no-op. Logs an error if the thread fails to start.
        """
        if self._thread and self._thread.is_alive():
            return  # Thread is already running; nothing to do.

        # Reset the stop signal before creating a fresh thread.
        self._stop_event.clear()

        self._thread = Thread(
            target=self._run,
            name="rpi-throttling-monitor",
            daemon=True,  # Thread is killed automatically when the main process exits.
        )
        try:
            self._thread.start()
            oradio_log.info("RPi throttling monitor started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("RPi throttling monitor failed to start: %s", ex_err)

    def stop(self) -> None:
        """
        Signal the background polling thread to stop and wait for it to exit.

        Sets the stop event, which causes the thread's wait() call to
        return early, and then blocks until the thread has fully exited.
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join()

##### Test mode API #################

    def enable_test_mode(self) -> None:
        """
        Switch the monitor into test mode.

        In test mode _get_throttle_value returns _forced_value
        instead of calling vcgencmd, making it possible to exercise the
        monitor's state-change logic without actual hardware throttling.

        Calling this method when test mode is already active is a no-op.
        """
        if not self._test_mode:
            oradio_log.info("RPi throttling monitor TEST MODE enabled")
            self._test_mode = True

    def disable_test_mode(self) -> None:
        """
        Restore normal (hardware) operation and clear any forced value.

        Clears both the test-mode flag and the forced value so the next
        poll reads real hardware state.
        """
        oradio_log.info("RPi throttling monitor TEST MODE disabled")
        self._test_mode = False
        self._forced_value = 0  # Ensure no stale forced value lingers after re-enabling

    def force_throttled_test(self, flags: int = 0x4) -> None:
        """
        Simulate a throttled state for testing purposes.

        Enables test mode (if not already active) and sets the forced value
        to flags. The background thread will pick up the change on its
        next poll cycle and log accordingly.

        Args:
            flags: Bitmask to simulate. Defaults to 0x4 ("Currently
                   throttled"). Multiple conditions can be combined with
                   bitwise OR, e.g. 0x1 | 0x4.
        """
        self.enable_test_mode()
        self._forced_value = flags

    def clear_throttled_test(self) -> None:
        """
        Simulate a return to the non-throttled state in test mode.

        Sets the forced value to 0 (no flags active). Test mode remains
        enabled; call disable_test_mode to exit test mode entirely.
        """
        self._forced_value = 0

# Creating the singleton here ensures the monitor starts as soon as this
# module is imported, without requiring any explicit setup by the caller.
throttling_monitor = RPiThrottlingMonitor()

##### Stand-alone entry point #######

if __name__ == "__main__":

    from time import sleep
    from constants import YELLOW, NC            # pylint: disable=wrong-import-position
    from messaging import DebugMessageHandler   # pylint: disable=ungrouped-imports,wrong-import-position

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """
        Run an interactive console menu for manually testing the throttle monitor.

        Puts the monitor into test mode and lets the operator inject each
        throttling condition (or combinations thereof) to verify that the
        correct log messages and error events are produced.

        The monitor is restored to normal operation when the user quits.
        """
        # Enter test mode so hardware state is bypassed.
        throttling_monitor.enable_test_mode()

        # Allow for print output to propagate
        sleep(0.5)

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

        while True:

            # Safely parse integer input; treat non-numeric input as invalid.
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1  # Sentinel that falls through to the default case

            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    throttling_monitor.disable_test_mode()  # Restore hardware polling
                    break
                case 1:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttling_monitor.force_throttled_test(0x1)  # Under-voltage only
                case 2:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttling_monitor.force_throttled_test(0x2)  # Frequency cap only
                case 3:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttling_monitor.force_throttled_test(0x4)  # Throttled only
                case 4:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    throttling_monitor.force_throttled_test(0x8)  # Soft temp limit only
                case 5:
                    print("\nForce RPI throttled (TEST MODE)...\n")
                    # Simulate all four active conditions simultaneously.
                    throttling_monitor.force_throttled_test(0x1 | 0x2 | 0x4 | 0x8)
                case 6:
                    print("\nClear RPI throttled (TEST MODE)...\n")
                    throttling_monitor.clear_throttled_test()  # Simulate recovery
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Subscribe to error topics and start message handler
    err_handler = DebugMessageHandler(Errors.subscribe())

    # Present menu with tests
    interactive_menu()

    # Stop receiving messages
    Errors.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    err_handler.stop()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
