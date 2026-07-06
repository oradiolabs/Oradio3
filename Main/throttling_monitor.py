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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: RPi Throttling Monitor
    Monitors the throttled state of a Raspberry Pi and logs state changes.
    Supports a test mode for forced throttling to validate logging.

Typical usage:
    RPiThrottlingMonitor is decorated with @singleton, so constructing it
    from anywhere, any number of times always returns the same shared instance.
    Call start() to begin polling; call stop() to halt polling.
"""
from subprocess import check_output

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from utilities import ThreadTemplate
from messaging import (
    Incidents,
    IncidentMessage,
    THROTTLING_SOURCE,
    THROTTLING_FAILED,
    THROTTLING_THROTTLED,
    THROTTLING_STOPPED,
)

##### LOCAL constants #####################################

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
class RPiThrottlingMonitor(ThreadTemplate):
    """
    Singleton background monitor for Raspberry Pi throttling state.

    Polls vcgencmd get_throttled at a configurable interval and logs a
    warning (plus publishes an error message) whenever the active throttling
    state changes, once polling has been started via start(). A
    one-time-per-run boot-time check is also performed on each start() to
    surface any historical throttling events that occurred before the
    monitor was (re)started.

    Built on ThreadTemplate, which provides the restartable
    setup()/do_work()/teardown() background-thread machinery (safe_start(),
    safe_stop(), crash detection, etc.), so this class only needs to
    implement the throttling-specific behaviour.
    """
    def __init__(self) -> None:
        """
        Initialise the RPi throttling monitor.

        Construction only sets up internal state; the background polling
        thread is not started until start() is called explicitly, mirroring
        ThreadTemplate's own separation between construction and
        safe_start(). This lets callers control exactly when polling
        begins (and stop()/start() again later) rather than having it
        begin as a side effect of import.
        """
        super().__init__(name="RPiThrottlingMonitor")

        # Cache of the last observed active-flag combination. Reset in
        # setup() at the start of every run, so a restart always produces
        # a fresh log entry if the system is already throttled.
        self._last_active_flags = 0

##### Helpers #############################################

    @classmethod
    def _decode_flags(cls, value: int, mask: int) -> list[str]:
        """
        Translate a throttle bitmask into a list of human-readable strings.

        Only bits that are both set in value and covered by mask are
        included, which lets callers filter for current-state or historical
        flags independently.

        Args:
            value: Raw bitmask returned by get_throttle_value.
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

##### ThreadTemplate overrides ############################

    def setup(self) -> None:
        """
        One-time-per-run initialisation, called by ThreadTemplate before
        the polling loop starts.

        Resets the cached active-flag state and reads the full throttle
        word (including sticky historical bits), warning if any throttling
        event has occurred since the last reboot. This catches problems
        that resolved themselves before the monitor started (e.g. a brief
        brownout at boot time).
        """
        self._last_active_flags = 0

        value = self.get_throttle_value()
        if value & HISTORICAL_MASK:
            reasons = self._decode_flags(value, HISTORICAL_MASK)
            oradio_log.warning("RPi HEALTH WARNING (since boot): %s", ", ".join(reasons))
            Incidents.publish(IncidentMessage(THROTTLING_SOURCE, THROTTLING_THROTTLED))

    def do_work(self) -> None:
        """
        Poll the throttling state once and log/publish on any change.

        Called repeatedly by ThreadTemplate's run loop. Only the
        current-state flags (ACTIVE_MASK) are compared across polls,
        so sticky historical bits do not trigger repeated log entries.

        Each state transition is logged once:
        * Enter throttling – logs a warning with the active flag names
          and publishes an error message.
        * Clear throttling – logs an info message indicating the
          condition resolved.
        """
        value = self.get_throttle_value()

        # Mask to current-state bits only; ignore historical sticky flags.
        active_flags = value & ACTIVE_MASK

        # Only act when the active flags have changed since the last poll.
        if active_flags != self._last_active_flags:
            if active_flags:
                # One or more throttling conditions just became active.
                reasons = self._decode_flags(value, ACTIVE_MASK)
                oradio_log.warning("RPi throttling ENTERED: %s", ", ".join(reasons))
                Incidents.publish(IncidentMessage(THROTTLING_SOURCE, THROTTLING_THROTTLED))
            else:
                # All throttling conditions have cleared.
                oradio_log.info("RPi throttling CLEARED")

            # Update the cache so the next iteration has a baseline.
            self._last_active_flags = active_flags

    def teardown(self) -> None:
        """Report incident: Oradio never intentionally stops backlighting."""
        Incidents.publish(IncidentMessage(THROTTLING_SOURCE, THROTTLING_STOPPED))

##### Public API ##########################################

    def get_throttle_value(self) -> int:
        """
        Return the current throttle bitmask from the hardware or test stub.

        The value is obtained by running:
            vcgencmd get_throttled
        which returns a string of the form throttled=0x50000.

        Note:
            This method always queries real hardware.
            The standalone __main__ block below monkeypatches this method
            on the running instance to drive interactive tests without hardware.

        Returns:
            Integer bitmask where each set bit indicates a throttling
            condition as described in THROTTLE_FLAGS.
        """
        # Call vcgencmd and parse the hex value after the "=" delimiter.
        out = check_output(["vcgencmd", "get_throttled"], text=True).strip()
        return int(out.split("=")[1], 16)

    def start(self) -> None:
        """
        Start the background polling thread.

        Thin wrapper around ThreadTemplate.safe_start() that preserves this
        class's original public API. Idempotent: calling start() when the
        thread is already alive is a no-op (logged by safe_start()).
        """
        if self.safe_start():
            oradio_log.info("RPi throttling monitor started")
        elif self.crashed:
            oradio_log.error("RPi throttling monitor failed to start: %s", self.exception)
            Incidents.publish(IncidentMessage(THROTTLING_SOURCE, THROTTLING_FAILED))

    def stop(self) -> None:
        """
        Signal the background polling thread to stop and wait for it to exit.

        Thin wrapper around ThreadTemplate.safe_stop() that preserves this
        class's original public API.
        """
        self.safe_stop()

##### Stand-alone entry point #############################

if __name__ == "__main__":

    from time import sleep
    from constants import YELLOW, NC
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    monitor = RPiThrottlingMonitor()

    # In test mode a single-item dict is used so the poller thread and
    # this main thread share the same mutable object without needing a lock
    # becaase dict item assignment is atomic under the GIL.
    _forced_value = {"bits": 0}

    def _enable_test_mode() -> None:
        """
        Monkeypatch the singleton so get_throttle_value() returns
        _forced_value["bits"] instead of querying vcgencmd.

        This patches the instance only (not the class). The background
        thread picks up the patched method on its next poll, since attribute
        lookup checks the instance's __dict__ before the class's.
        """
        # Allow overwriting a method by a lambda function
        monitor.get_throttle_value = lambda: _forced_value["bits"]      # type: ignore[method-assign]
        oradio_log.info("RPi throttling monitor TEST MODE enabled")

    def _disable_test_mode() -> None:
        """Remove the monkeypatch, restoring normal hardware polling."""
        if "get_throttle_value" in vars(monitor):
            del monitor.__dict__["get_throttle_value"]
        oradio_log.info("RPi throttling monitor TEST MODE disabled")

    def interactive_menu() -> None:
        """
        Run an interactive console menu for manually testing the throttle monitor.

        Puts the monitor into test mode (bypassing real hardware) and lets
        the operator start/stop polling and inject each throttling
        condition (or combinations thereof) to verify that the correct log
        messages and error events are produced. Since the monitor no
        longer self-starts, start/stop are exposed as explicit menu
        options rather than assumed to already be running.

        Test mode and the monitor's running state are both cleaned up when
        the user quits.
        """
        # Enter test mode so hardware state is bypassed.
        _enable_test_mode()

        # Allow for print output to propagate
        sleep(0.5)

        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start RPi throttling monitor\n"
            " 2-Stop RPi throttling monitor\n"
            " 3-Force RPi throttled (undervoltage)\n"
            " 4-Force RPi throttled (frequency capped)\n"
            " 5-Force RPi throttled (thermal)\n"
            " 6-Force RPi throttled (temperature)\n"
            " 7-Force RPi throttled (all)\n"
            " 8-Clear RPi throttled\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    _disable_test_mode()
                    monitor.stop()  # Ensure nothing is left running on exit
                    break
                case 1:
                    print("\nStarting monitor...\n")
                    monitor.start()
                case 2:
                    print("\nStopping monitor...\n")
                    monitor.stop()
                case 3:
                    print("\nForce RPi throttled (TEST MODE)...\n")
                    _forced_value["bits"] = 0x1  # Under-voltage only
                case 4:
                    print("\nForce RPi throttled (TEST MODE)...\n")
                    _forced_value["bits"] = 0x2  # Frequency cap only
                case 5:
                    print("\nForce RPi throttled (TEST MODE)...\n")
                    _forced_value["bits"] = 0x4  # Throttled only
                case 6:
                    print("\nForce RPi throttled (TEST MODE)...\n")
                    _forced_value["bits"] = 0x8  # Soft temp limit only
                case 7:
                    print("\nForce RPi throttled (TEST MODE)...\n")
                    # Simulate all four active conditions simultaneously.
                    _forced_value["bits"] = 0x1 | 0x2 | 0x4 | 0x8
                case 8:
                    print("\nClear RPi throttled (TEST MODE)...\n")
                    _forced_value["bits"] = 0  # Simulate recovery
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topics and start message handler
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Present menu with tests
    interactive_menu()

    # Stop receiving messages
    Incidents.unsubscribe(incident_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    incident_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
