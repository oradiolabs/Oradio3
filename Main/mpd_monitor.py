#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 10, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@references:
    https://python-mpd2.readthedocs.io/en/latest/topics/commands.html
@summary:
    Oradio MPD event monitor module
    - Automatic reconnect if MPD is down or connection drops
    - Composes an MPDService connection and a background _MPDMonitorWorker
      (built on ThreadTemplate, utilities.py), so the monitor can be
      cleanly started, stopped and restarted, and reports crashes instead
      of dying silently. Mirrors the shape used by throttling_monitor.py,
      volume_control.py and backlight_service.py.
"""
from os import path
from time import sleep
from collections import defaultdict

##### Oradio modules ######################################
from log_service import oradio_log
from singleton import singleton
from mpd_service import MPDService
from utilities import ThreadTemplate
from messaging import (
    Incidents,
    IncidentMessage,
    MPD_SOURCE,
    MPD_INCIDENT_MONITOR,
)

##### LOCAL constants #####################################
# Mapping of MPD idle events to typical client actions
MPD_EVENT_ACTIONS = {
    "database":        "Database changed",                         # Consider updating your local song cache, e.g., client.listall() or client.list()
    "update":          "Database update in progress or finished",  # May want to refresh local state if needed
    "stored_playlist": "Stored playlist changed",                  # Refresh playlist info, e.g., client.listplaylists()
    "playlist":        "Current playlist changed",                 # Query client.playlistinfo() or client.playlistid()
    "player":          "Playback state changed",                   # Query client.status() and client.currentsong()
    "mixer":           "Volume/crossfade changed",                 # Query client.status() for volume or crossfade
    "output":          "Output devices changed",                   # Query client.outputs()
    "options":         "Global options changed",                   # Query client.status() (repeat, random, single, consume)
    "sticker":         "Song sticker changed",                     # Query client.sticker_list() if you use stickers
    "subscription":    "Subscription state changed",               # Typically used with mpd-subscribe
    "message":         "Message sent via MPD",                     # Rarely used; may need client.readmessages() if implemented
}

class _MPDMonitorWorker(ThreadTemplate):
    """
    Background worker that listens for MPD idle events and logs state
    changes on behalf of the owning MPDMonitor.

    One instance is created per MPDMonitor (see MPDMonitor.__init__) and
    reused across repeated start()/stop() cycles: ThreadTemplate itself is
    restartable, so a single _MPDMonitorWorker instance can be
    safe_start()ed and safe_stop()ped any number of times.

    Note:
        _execute() is a protected member of MPDService. Reaching into it
        from here (rather than from MPDService itself) is intentional:
        this worker and MPDMonitor together form the "MPD monitoring"
        feature, and MPDService deliberately doesn't grow a public
        arbitrary-command method just to serve this one caller.
    """
    def __init__(self, mpd_service: MPDService) -> None:
        """
        Args:
            mpd_service: The MPDService connection to issue idle/status/
                listall commands against. Owned and connected by the
                enclosing MPDMonitor, passed in rather than constructed
                here, so MPDMonitor controls the connection's lifetime.
        """
        super().__init__(interval=0.0, name="MPDMonitorWorker")
        self._mpd_service = mpd_service

        # Snapshot of MPD database: directory -> set of file paths.
        # Rebuilt from scratch in setup() at the start of every run.
        self._snapshot: dict[str, set] = defaultdict(set)

##### Helpers #############################################

    def _build_initial_snapshot(self) -> None:
        """Build the initial snapshot of the MPD database (directory -> files)."""
        for song in self._mpd_service._execute("listall") or []:    # pylint: disable=protected-access
            if "file" in song:
                directory = path.dirname(song["file"])
                self._snapshot[directory].add(song["file"])

    def _handle_database_update(self) -> tuple[dict, dict]:
        """
        Compare the current MPD database against the stored snapshot.

        Detects files added or removed per directory and updates the snapshot
        to reflect the current state.

        Returns:
            tuple[dict, dict]: A pair of (added_per_dir, removed_per_dir), each
                mapping a directory path to a set of affected file paths.
        """
        added_per_dir   = defaultdict(set)
        removed_per_dir = defaultdict(set)

        # Build current snapshot; guard against _execute returning None on failure.
        current_snapshot = defaultdict(set)
        for song in self._mpd_service._execute("listall") or []:    # pylint: disable=protected-access
            if "file" in song:
                directory = path.dirname(song["file"])
                current_snapshot[directory].add(song["file"])

        # Compare old and new snapshots across the union of all known directories.
        all_dirs = set(self._snapshot.keys()) | set(current_snapshot.keys())
        for directory in all_dirs:
            old_songs = self._snapshot.get(directory, set())
            new_songs = current_snapshot.get(directory, set())
            added   = new_songs - old_songs
            removed = old_songs - new_songs
            if added:
                added_per_dir[directory] = added
            if removed:
                removed_per_dir[directory] = removed

        # Replace the stored snapshot with the current state.
        self._snapshot = current_snapshot
        return added_per_dir, removed_per_dir

##### ThreadTemplate overrides ############################

    def setup(self) -> None:
        """
        Build the initial MPD database snapshot for this run.

        Runs once at the start of every safe_start(), before do_work() is
        called for the first time. The snapshot is (re)created from scratch
        here so a restart (safe_stop() then safe_start()) doesn't carry
        over a stale snapshot from a previous run.
        """
        self._snapshot = defaultdict(set)
        self._build_initial_snapshot()

    def do_work(self) -> None:
        """
        Process a single batch of MPD idle events.

        Called repeatedly by the ThreadTemplate run loop. Since
        self._interval is 0, there's effectively no delay imposed between
        calls by the loop itself -- the blocking "idle" MPD command below
        is what naturally paces this under normal conditions. The
        sleep(1) calls on the error paths exist specifically to throttle
        retries when idle/status calls are failing outright (e.g. MPD is
        down), since otherwise there'd be no pacing at all in that case.

        For each batch of events returned by a single idle call:
            - Fetches MPD status once for the whole batch.
            - Logs each event with its description.
            - Skips further processing if MPD reports an error.
            - Logs current song info for playlist/player events.
            - Diffs the database snapshot for database events.

        A falsy idle() result normally means a genuine failure (connection
        drop, lock timeout, retries exhausted -- _execute() already logs and
        publishes an incident for those). The one exception is a clean stop:
        MPDMonitor.stop() interrupts a blocking idle() by sending "noidle"
        on the connection, which also returns a falsy/empty result. stop()
        sets the stop flag before sending "noidle", so self.stopping is
        already true by the time this method wakes up -- that's what
        distinguishes the two cases so a clean stop doesn't get logged as
        an error.
        """
        # pylint: disable=protected-access

        # Block until one or more MPD events occur (or stop() interrupts via noidle()).
        events = self._mpd_service._execute("idle")

        if not events:
            if self.stopping:
                return  # Interrupted by stop(), not a real failure.
            oradio_log.error("MPD idle command returned no events or failed")
            sleep(1)
            return

        event_set  = set(events)
        detail_map = {event: MPD_EVENT_ACTIONS.get(event, "Unknown event") for event in events}

        # Fetch status once for the entire event batch.
        status = self._mpd_service._execute("status") or {}

        # Log each event with its human-readable description.
        for event, detail in detail_map.items():
            oradio_log.info("MPD event: %s → %s", event, detail)

        # Skip further processing if MPD has reported an error.
        if status.get("error", ""):
            oradio_log.error(
                "MPD reported an error during events %s: %s",
                ", ".join(events), status.get("error", ""),
            )
            sleep(1)
            return

        oradio_log.debug(
            "MPD state for events %s: %s",
            ", ".join(events), status.get("state", ""),
        )

        # Log current song info for playlist or player events.
        if event_set & {"playlist", "player"}:
            current_song: dict = self._mpd_service._execute("currentsong") or {}
            oradio_log.debug(
                "Current song: %s - %s",
                current_song.get("artist", ""), current_song.get("title", ""),
            )

        # Diff the database snapshot for database events.
        if "database" in event_set:
            added, removed = self._handle_database_update()
            for directory, files in added.items():
                oradio_log.info("[%s] Added: %d files", directory, len(files))
            for directory, files in removed.items():
                oradio_log.info("[%s] Removed: %d files", directory, len(files))

    def teardown(self) -> None:
        """Called once when the monitoring loop stops, cleanly or via crash."""
        oradio_log.debug("%s stopping", self.name)

@singleton
class MPDMonitor:
    """
    Singleton class that monitors MPD (Music Player Daemon) events.

    Composes an MPDService connection and a background _MPDMonitorWorker
    (built on ThreadTemplate, see _MPDMonitorWorker above). Construction
    only sets up state; the background polling thread is not started
    until start() is called explicitly, mirroring ThreadTemplate's own
    separation between construction and safe_start(), and matching the
    shape used by ThrottlingMonitor, VolumeControl and Backlighting.

    Features:
        - Maintains a snapshot of the MPD database (directory -> files),
          rebuilt from scratch at the start of every run (see
          _MPDMonitorWorker.setup()).
        - Listens for idle events in a background thread (see
          _MPDMonitorWorker.do_work()).
        - Logs database updates and playlist/player info.
        - A crashed worker (setup() or do_work() raising) is caught by
          ThreadTemplate, logged, and published as an incident by
          start(), rather than silently killing the thread.

    Note:
        The MPD idle command blocks the MPDService connection shared
        between the worker and MPDMonitor's own direct calls, for the
        duration of each wait. Any other _execute() call made on that
        same connection while idle is blocking will queue behind the
        lock and wait up to LOCK_TIMEOUT seconds. If concurrent command
        traffic is required, a dedicated second MPD client connection
        should be used for idle.
    """
    def __init__(self) -> None:
        """
        Initialise the MPD monitor.

        Connects to MPD and creates (but does not start) the background
        worker. Callers must call start() explicitly to begin monitoring,
        and may stop()/start() again later since the worker is
        restartable.
        """
        # Own connection to MPD, used both directly (e.g. the stand-alone
        # test harness below) and by the background worker.
        self._mpd_service = MPDService()

        # Created once; safe_start()/safe_stop() can be called on it
        # repeatedly since ThreadTemplate itself supports restarting.
        self._worker = _MPDMonitorWorker(self._mpd_service)

##### Public API ##########################################

    def start(self) -> None:
        """
        Start the background polling thread.

        Blocks until the worker signals readiness (the initial database
        snapshot has been built, see _MPDMonitorWorker.setup()), or until
        it crashes or times out. Idempotent: calling start() when the
        thread is already alive is a no-op.
        """
        if self._worker.is_alive():
            oradio_log.debug("MPD monitor thread already running")
            return

        if not self._worker.safe_start():
            oradio_log.error("MPD monitor thread failed to start")
            Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_INCIDENT_MONITOR))
            return

        if self._worker.crashed:
            oradio_log.error(
                "MPD monitor thread crashed during startup: %s", self._worker.exception,
            )
            Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_INCIDENT_MONITOR))
            return

        oradio_log.info("MPD monitor thread started")

    def stop(self) -> None:
        """
        Signal the background polling thread to stop and wait for it to exit.

        do_work() spends most of its time blocked inside a single MPD
        "idle" call, which by itself only returns on a real MPD event --
        setting the stop flag alone would have to wait for that, possibly
        for a long time. MPD's idle protocol supports interrupting a
        blocked idle() by sending "noidle" on the same connection from
        another thread, so that's done here directly, bypassing
        MPDService._execute()'s lock/retry machinery -- that lock is
        exactly what the blocked idle() call is currently holding, so
        going through _execute() here would just block until idle()
        itself returns, which is the problem being worked around.

        The stop flag is set before noidle() is sent (rather than left to
        safe_stop() below) so the worker is guaranteed to see the stop
        request as soon as the interrupted idle() call returns, instead of
        racing to loop back into another blocking idle() first.
        """
        self._worker._stop_event.set()  # pylint: disable=protected-access
        try:
            self._mpd_service._client.noidle()  # pylint: disable=protected-access
        except Exception:  # pylint: disable=broad-exception-caught
            # Not currently idling, or the connection is already down.
            # Either way, safe_stop() below still applies -- worst case it
            # waits out its normal join timeout instead of returning early.
            pass

        self._worker.safe_stop()

##### Stand-alone entry point #############################

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu(monitor: MPDMonitor) -> None:   # pylint: disable=too-many-branches,duplicate-code
        """
        Run an interactive self-test menu for MPDMonitor.

        Blocks until the user enters 0 to quit. Since the monitor no
        longer self-starts, start/stop are exposed as explicit menu
        options rather than assumed to already be running.

        Args:
            monitor: The MPDMonitor instance used to trigger test actions.
        """
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start MPD monitor\n"
            " 2-Stop MPD monitor\n"
            " 3-Trigger database update event\n"
            " 4-Show monitor thread status\n"
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
                    print("\nTriggering MPD database update event...")
                    monitor._mpd_service._execute("update")  # pylint: disable=protected-access
                case 4:
                    worker = monitor._worker  # pylint: disable=protected-access
                    print(
                        f"\nis_alive={worker.is_alive()}, "
                        f"crashed={worker.crashed}, "
                        f"exception={worker.exception}"
                    )
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topic so published messages are printed to console.
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Construct the MPD monitor (connects to MPD; does not start monitoring yet).
    mpd_monitor = MPDMonitor()

    # Launch the interactive test menu; blocks until the user quits.
    interactive_menu(mpd_monitor)

    # Stop receiving error messages.
    Incidents.unsubscribe(incident_handler.get_queue())

    # Signal the handler thread to exit and confirm it has exited.
    incident_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
