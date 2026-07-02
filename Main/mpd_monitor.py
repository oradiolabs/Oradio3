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
"""
from os import path
from time import sleep
from threading import Thread
from collections import defaultdict

##### Oradio modules ######################################
from log_service import oradio_log
from singleton import singleton
from mpd_service import MPDService
from messaging import (
    Errors,
    ErrorMessage,
    MPD_SOURCE,
    MPD_ERROR_MONITOR,
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

# Timeout for thread operations (seconds)
THREAD_TIMEOUT = 3

@singleton
class MPDMonitor(MPDService):
    """
    Singleton class that monitors MPD (Music Player Daemon) events.

    The monitoring thread is started automatically during __init__; no
    separate start() call is required. Call stop() to shut down the thread.

    Features:
        - Maintains a snapshot of the MPD database (directory -> files).
        - Listens for idle events in a background thread.
        - Logs database updates and playlist/player info.

    Note:
        The MPD idle command blocks the shared MPDService connection for the
        duration of each wait. Any other _execute() call made while idle is
        blocking will queue behind the lock and wait up to LOCK_TIMEOUT seconds.
        If concurrent command traffic is required, a dedicated second MPD
        client connection should be used for idle.
    """
    def __init__(self) -> None:
        """
        Initialise MPDMonitor, build the database snapshot, and start the
        background monitoring thread.
        """
        # Initialise the parent MPDService and connect to MPD.
        super().__init__()

        # Snapshot of MPD database: directory -> set of file paths.
        self._snapshot: dict[str, set] = defaultdict(set)
        self._build_initial_snapshot()

        # Create and start the background monitoring thread.
        self._thread = Thread(target=self._listen, daemon=True)
        try:
            self._thread.start()
        except RuntimeError as ex_err:
            oradio_log.error("MPD monitor thread failed to start: %s", ex_err)
            Errors.publish(ErrorMessage(MPD_SOURCE, MPD_ERROR_MONITOR))
            return

        oradio_log.info("MPD monitor thread started")

##### Helpers #############################################

    def _build_initial_snapshot(self) -> None:
        """Build the initial snapshot of the MPD database (directory -> files)."""
        for song in self._execute("listall") or []:
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
        for song in self._execute("listall") or []:
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

##### Core ################################################

    def _listen(self) -> None:
        """
        Background thread that listens for MPD idle events.

        For each batch of events returned by a single idle call:
            - Fetches MPD status once for the whole batch.
            - Logs each event with its description.
            - Skips further processing if MPD reports an error.
            - Logs current song info for playlist/player events.
            - Diffs the database snapshot for database events.

        The loop runs forever until the program exits.
        """
        while True:

            # Block until one or more MPD events occur.
            events = self._execute("idle")

            if not events:
                oradio_log.error("MPD idle command returned no events or failed")
                sleep(1)
                continue

            event_set  = set(events)
            detail_map = {event: MPD_EVENT_ACTIONS.get(event, "Unknown event") for event in events}

            # Fetch status once for the entire event batch.
            status = self._execute("status") or {}

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
                continue

            oradio_log.debug(
                "MPD state for events %s: %s",
                ", ".join(events), status.get("state", ""),
            )

            # Log current song info for playlist or player events.
            if event_set & {"playlist", "player"}:
                current_song: dict = self._execute("currentsong") or {}
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

##### Stand-alone entry point #############################

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    def interactive_menu(monitor: MPDMonitor) -> None:   # pylint: disable=too-many-branches,duplicate-code
        """
        Run an interactive self-test menu for MPDMonitor.

        Blocks until the user enters 0 to quit.

        Args:
            monitor: The active monitor instance used to
                                      trigger test actions.
        """
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Trigger database update event\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print("\nTriggering MPD database update event...")
                    monitor._execute("update")  # pylint: disable=protected-access
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topic so published messages are printed to console.
    err_handler = DebugMessageHandler(Errors.subscribe())

    # Start the MPD monitor (also starts the background thread).
    mpd_monitor = MPDMonitor()

    # Launch the interactive test menu; blocks until the user quits.
    interactive_menu(mpd_monitor)

    # Stop receiving error messages.
    Errors.unsubscribe(err_handler.get_queue())

    # Signal the handler thread to exit and confirm it has exited.
    err_handler.stop()

    print("\nExiting test program...\n")
