#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on July 13, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Log Health Monitor
    Monitors log_service's dropped-log-record counter and publishes an
    incident whenever records have been dropped due to a saturated log
    queue.

    log_service must not import other Oradio modules (see the NOTE near
    the top of log_service.py), so it cannot publish incidents itself.
    Rather than log_service calling back into this module, this dedicated
    monitor polls SafeLogger.dropped_count so it depends on both log_service
    and messaging while neither of those depends on it.
"""

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from utilities import ThreadTemplate
from messaging import (
    Incidents,
    IncidentMessage,
    LOG_SOURCE,
    LOG_START_FAILED,
    LOG_QUEUE_OVERFLOW,
    LOG_QUEUE_RECOVERED,
    LOG_LISTENER_DEAD,
    LOG_STOPPED,
)

@singleton
class LogHealthMonitor(ThreadTemplate):
    """
    Singleton background monitor for log_service's dropped-record counter.

    Polls SafeLogger.dropped_count and publishes an incident whenever the
    count has increased since the last poll, once polling has been started
    via start(). Built on ThreadTemplate, which provides the restartable
    setup()/do_work()/teardown() background-thread machinery (safe_start(),
    safe_stop(), crash detection, etc.), so this class only needs to
    implement the log-health-specific behaviour.
    """
    def __init__(self) -> None:
        """
        Initialise the log health monitor.

        Construction only sets up internal state; the background polling
        thread is not started until start() is called explicitly, mirroring
        ThreadTemplate's own separation between construction and
        safe_start(). This lets callers control exactly when polling
        begins (and stop()/start() again later) rather than having it
        begin as a side effect of import.
        """
        super().__init__(name="LogHealthMonitor")

        # Cache of the last observed dropped_count, and the last observed
        # queue_full / listener_alive states. All three are reset in
        # setup() at the start of every run -- see setup()'s docstring for
        # why dropped_count and the other two are baselined differently.
        self._last_dropped = 0
        self._was_full = False
        self._was_alive = True

##### ThreadTemplate overrides ############################

    def setup(self) -> None:
        """
        One-time-per-run initialisation, called by ThreadTemplate before
        the polling loop starts.

        dropped_count is a monotonic total, so it's baselined to
        log_service's current value -- otherwise a (re)start would
        immediately re-report already-known historical drops as new.

        queue_full and listener_alive are live states, not counters, so
        they're reset to their healthy defaults (not full / alive)
        instead of baselined to their current value. That means if either
        is already unhealthy when the monitor (re)starts, the very first
        do_work() poll reports it as a transition.
        """
        self._last_dropped = oradio_log.dropped_count
        self._was_full = False
        self._was_alive = True

    def do_work(self) -> None:
        """
        Poll log_service's health once and publish on any state transition.

        Checks three signals from log_service (see the docstrings on
        SafeLogger.queue_full / .listener_alive for what each one
        distinguishes):
        - listener_alive: False means the queue can never drain again --
          the most severe, terminal case. Reported once, on the
          alive-to-dead transition, since it never resolves itself.
        - queue_full: reported on both the healthy-to-full and the
          full-to-healthy (recovered) transitions.
        - dropped_count: cumulative; only used here to log the exact
          tally moving, since the transitions above already cover the
          incident-worthy events.

        Reporting on transitions only (rather than every poll while a
        condition persists) avoids repeatedly publishing the same incident.
        """
        listener_alive = oradio_log.listener_alive
        queue_full = oradio_log.queue_full
        current_dropped = oradio_log.dropped_count

        if listener_alive != self._was_alive:
            if not listener_alive:
                oradio_log.error("Log queue listener thread is no longer running; the log queue will not drain")
                Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_LISTENER_DEAD))
            self._was_alive = listener_alive

        if queue_full != self._was_full:
            if queue_full:
                oradio_log.warning("Log queue is full; new records are being dropped")
                Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_QUEUE_OVERFLOW))
            else:
                oradio_log.info("Log queue has drained and is accepting records again")
                Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_QUEUE_RECOVERED))
            self._was_full = queue_full

        if current_dropped > self._last_dropped:
            oradio_log.debug(
                "Dropped record count now %d (+%d)",
                current_dropped, current_dropped - self._last_dropped
            )
            self._last_dropped = current_dropped

    def teardown(self) -> None:
        """Report incident: Oradio never intentionally stops log health monitoring."""
        Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_STOPPED))

##### Public API ##########################################

    def start(self) -> None:
        """
        Start the background polling thread.

        Thin wrapper around ThreadTemplate.safe_start() that preserves this
        class's original public API. Idempotent: calling start() when the
        thread is already alive is a no-op.
        """
        if self.is_alive():
            oradio_log.debug("Log health monitor already running")
            return

        if not self.safe_start():
            oradio_log.error("Log health monitor failed to start")
            Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_START_FAILED))
            return

        if self.crashed:
            oradio_log.error("Log health monitor crashed during startup: %s", self.exception)
            Incidents.publish(IncidentMessage(LOG_SOURCE, LOG_START_FAILED))
            return

        oradio_log.info("Log health monitor started")

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

    monitor = LogHealthMonitor()

    def _force_drop(count: int) -> None:
        """
        Directly bump oradio_log's internal drop counter to simulate a
        saturated log queue, without actually having to flood the queue
        with thousands of log calls.
        """
        for _ in range(count):
            # pylint: disable=protected-access
            oradio_log._queue_handler._dropped += 1
        oradio_log.info("Simulated %d dropped record(s) (TEST MODE)", count)

    def _print_status() -> None:
        """
        Print log_service's current health signals as SafeLogger reports
        them right now. queue_full and listener_alive aren't safe to force
        interactively (that would mean either flooding the real production
        log queue with thousands of records, or actually killing the real
        listener thread for the rest of the process) -- so this just
        surfaces the live values instead.
        """
        print(
            f"\nqueue_size={oradio_log.queue_size}  "
            f"queue_full={oradio_log.queue_full}  "
            f"listener_alive={oradio_log.listener_alive}  "
            f"dropped_count={oradio_log.dropped_count}\n"
        )

    def interactive_menu() -> None:
        """
        Run an interactive console menu for manually testing the log
        health monitor.

        Lets the operator start/stop polling, simulate dropped-record
        counts, and inspect log_service's live health signals, to verify
        that the correct log messages and incident events are produced.
        Since the monitor no longer self-starts, start/stop are exposed
        as explicit menu options rather than assumed to already be
        running.
        """
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start log health monitor\n"
            " 2-Stop log health monitor\n"
            " 3-Simulate 1 dropped record\n"
            " 4-Simulate 50 dropped records\n"
            " 5-Show current log queue health\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    monitor.stop()  # Ensure nothing is left running on exit
                    break
                case 1:
                    print("\nStarting monitor...\n")
                    monitor.start()
                case 2:
                    print("\nStopping monitor...\n")
                    monitor.stop()
                case 3:
                    print("\nSimulate 1 dropped record (TEST MODE)...\n")
                    _force_drop(1)
                case 4:
                    print("\nSimulate 50 dropped records (TEST MODE)...\n")
                    _force_drop(50)
                case 5:
                    _print_status()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topics and start message handler
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Allow for print output to propagate
    sleep(0.5)

    # Present menu with tests
    interactive_menu()

    # Stop receiving messages
    Incidents.unsubscribe(incident_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    incident_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
