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
    Oradio MPD event tmonitor module
    - Automatic reconnect if MPD is down or connection drops
"""
from os import path
from time import sleep
from threading import Thread
from collections import defaultdict

##### oradio modules ####################
from oradio_logging import oradio_log
from singleton import singleton
from mpd_service import MPDService

##### Local constants ####################
# Mapping of MPD idle events to typical client actions
MPD_EVENT_ACTIONS = {
    "database": "Database changed",                      # Consider updating your local song cache, e.g., client.listall() or client.list()
    "update": "Database update in progress or finished", # May want to refresh local state if needed
    "stored_playlist": "Stored playlist changed",        # Refresh playlist info, e.g., client.listplaylists()
    "playlist": "Current playlist changed",              # Query client.playlistinfo() or client.playlistid()
    "player": "Playback state changed",                  # Query client.status() and client.currentsong()
    "mixer": "Volume/crossfade changed",                 # Query client.status() for volume or crossfade
    "output": "Output devices changed",                  # Query client.outputs()
    "options": "Global options changed",                 # Query client.status() (repeat, random, single, consume)
    "sticker": "Song sticker changed",                   # Query client.sticker_list() if you use stickers
    "subscription": "Subscription state changed",        # Typically used with mpd-subscribe
    "message": "Message sent via MPD",                   # rarely used, may need client.readmessages() if implemented
}

@singleton
class MPDMonitor(MPDService):
    """
    Singleton class that monitors MPD (Music Player Daemon) events.
    - Maintains a snapshot of the MPD database (directory -> files)
    - Listens for events in a background thread
    - Logs errors, database updates, and playlist/player info
    """
    def __init__(self):
        """Initialize the MPDMonitor client and connect to the MPD server."""
        # Execute MPDService __init__
        super().__init__()

        # Snapshot of MPD database: directory -> set of file paths
        self._snapshot = defaultdict(set)
        self._build_initial_snapshot()

        # Background thread for monitoring MPD events
        self._thread = Thread(target=self._listen, daemon=True)
        self._running = False

# -----Helper methods----------------

    def _build_initial_snapshot(self) -> None:
        """Build the initial snapshot of the MPD database (directory -> files)."""
        for song in self._execute("listall") or []:
            if 'file' in song:
                directory = path.dirname(song['file'])
                self._snapshot[directory].add(song['file'])

    def _handle_database_update(self) -> (dict, dict):
        """
        Compare the current MPD database with the snapshot.
        Detect added and removed files per directory.
        Logs warnings for invalid songs or missing directories.

        Returns:
            added_per_dir: dict of newly added files per directory
            removed_per_dir: dict of removed files per directory
        """
        added_per_dir = defaultdict(set)
        removed_per_dir = defaultdict(set)

        # Build current snapshot
        all_songs = self._execute("listall")
        current_snapshot = defaultdict(set)
        for song in all_songs:
            if 'file' in song:
                directory = path.dirname(song['file'])
                current_snapshot[directory].add(song['file'])

        # Compare old and new snapshots
        all_dirs = set(self._snapshot.keys()) | set(current_snapshot.keys())
        for directory in all_dirs:
            old_songs = self._snapshot.get(directory, set())
            new_songs = current_snapshot.get(directory, set())
            added = new_songs - old_songs
            removed = old_songs - new_songs
            if added:
                added_per_dir[directory] = added
            if removed:
                removed_per_dir[directory] = removed

        # Update the snapshot
        self._snapshot = current_snapshot
        return added_per_dir, removed_per_dir

# -----Listener----------------------

    def _listen(self) -> None:
        """
        Background thread that listens for MPD events.
        For each event:
        - Checks MPD status for errors
        - Logs database updates
        - Logs playlist/player info if no error
        """
        self._running = True
        while self._running:

            # Block until one or more events occur
            events = self._execute("idle")

            if not events:
                oradio_log.error("MPD idle command returned no events or failed")
                sleep(1)
                continue

            event_set = set(events)
            detail_map = {event: MPD_EVENT_ACTIONS.get(event, "Unknown event") for event in events}

            # Fetch status once for the whole batch
            status = self._execute("status") or {}

            # Log each event
            for event, detail in detail_map.items():
                oradio_log.info("MPD event: %s → %s", event, detail)

            # Log status error once if present
            if status.get("error", ""):
                oradio_log.error("MPD reported an error during events %s: %s", ", ".join(events), status.get("error", ""))
                sleep(1)
                continue  # Skip further processing if error exists

            oradio_log.debug("MPD state for events %s: %s", ", ".join(events), status.get("state", ""))

            # Handle playlist/player events
            if event_set & {"playlist", "player"}:
                current_song: dict = self._execute("currentsong") or {}
                oradio_log.debug("Current song: %s - %s", current_song.get("artist", ""), current_song.get("title", ""))

            # Handle database events
            if "database" in event_set:
                added, removed = self._handle_database_update()
                for directory, files in added.items():
                    oradio_log.info("[%s] Added: %d files", directory, len(files))
                for directory, files in removed.items():
                    oradio_log.info("[%s] Removed: %d files", directory, len(files))

            sleep(1)

# -----Public methods----------------

    def start(self) -> None:
        """Start the background listener thread if not already running."""
        if not self._thread.is_alive():
            self._thread.start()
            oradio_log.info("MPD Event Monitor started")

    def stop(self) -> None:
        """Stop the background listener thread and wait for it to terminate."""
        self._running = False
        self._thread.join(timeout=2)
        oradio_log.info("MPD Event Monitor stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    mpd_monitor = MPDMonitor()
    print("Running MPD event monitor")
    mpd_monitor.start()
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        mpd_monitor.stop()
    print("Exiting MPD event monitor")
