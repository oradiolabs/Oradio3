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
import os
import time
import threading
from collections import defaultdict
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log

##### Local constants ####################
MPD_HOST        = "localhost"
MPD_PORT        = 6600
MPD_RETRIES     = 3
MPD_RETRY_DELAY = 1     # seconds
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

class MPDEventMonitor:
    """
    Singleton class that monitors MPD (Music Player Daemon) events.
    - Maintains a snapshot of the MPD database (directory -> files)
    - Listens for events in a background thread
    - Logs errors, database updates, and playlist/player info
    """

# In below code using same construct in multiple modules for singletons
# pylint: disable=duplicate-code

    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    # Underscores mark args and kwargs as 'intentionally unused'
    def __new__(cls, *_args, **_kwargs):
        """Ensure only one instance of MPDEventMonitor is created (singleton pattern)"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MPDEventMonitor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Initialize the listener by setting up the D-Bus main loop integration,
        connecting to the system bus, finding the wifi device, and subscribing
        to the 'StateChanged' signal
        """
        # Prevent re-initialization if the singleton is created again
        if self._initialized:
            return  # Avoid re-initialization if already done
        self._initialized = True

# In above code using same construct in multiple modules for singletons
# pylint: enable=duplicate-code

        self._client = MPDClient()
        self._connect_client()

        # Snapshot of MPD database: directory -> set of file paths
        self.snapshot = defaultdict(set)
        self._build_initial_snapshot()

        # Background thread for monitoring MPD events
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.running = False

# -----Helper methods----------------

# In below code using same construct in mpd_control module
# pylint: disable=duplicate-code

    def _connect_client(self) -> None:
        """
        Attempt to establish a connection to the MPD server with retry logic.
        - Tries to connect up to MPD_RETRIES times.
        - Waits MPD_RETRY_DELAY seconds between attempts.
        - On success, sets the MPD crossfade parameter for smoother transitions.
        - Logs detailed status and failure information.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            try:
                # Attempt to connect to the MPD server
                self._client.connect(MPD_HOST, MPD_PORT)

                oradio_log.info("Connected to MPD on %s:%s", MPD_HOST, MPD_PORT)
                return

            except MPDConnectionError as ex_err:
                # Log the failure and retry after a delay
                oradio_log.warning("Connection to MPD failed (%s). Retry %d/%d", ex_err, attempt, MPD_RETRIES)
                time.sleep(MPD_RETRY_DELAY)

        # If all retries fail, log a final error
        oradio_log.error("Failed to connect to MPD after %d attempts", MPD_RETRIES)

    def _execute(self, command: str, *args, **kwargs) -> object | None:
        """
        Execute an MPD command in a fault-tolerant manner.
        - Validates the command before execution.
        - Automatically reconnects and retries on connection-related errors.
        - Gracefully handles invalid or failed MPD commands.

        Args:
            command (str): The name of the MPD command to execute.
            *args: Positional arguments for the MPD command.
            **kwargs: Keyword arguments for the MPD command.

        Returns:
            The result of the MPD command, or None if an error occurs after all retries.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            try:
                # Retrieve the MPD command method dynamically
                function = getattr(self._client, command, None)
                if not callable(function):
                    oradio_log.error("Invalid MPD command: '%s'", command)
                    return None

                # Execute the MPD command safely
                return function(*args, **kwargs)

            except CommandError as ex_err:
                # MPD command-specific failure (e.g., invalid args)
                oradio_log.error("MPD command error: %s", ex_err)
                return None

            except (MPDConnectionError, ProtocolError, BrokenPipeError) as ex_err:
                # Connection issue — log and retry after reconnect
                oradio_log.warning("MPD connection or protocol error (%s). Retry %d/%d", ex_err, attempt, MPD_RETRIES)
                self._connect_client()
                time.sleep(MPD_RETRY_DELAY)  # brief pause before retrying

        # All retries failed
        oradio_log.error("Failed to execute MPD command '%s' after %d retries", command, MPD_RETRIES)
        return None

# In above code using same construct in mpd_control module
# pylint: enable=duplicate-code

    def _build_initial_snapshot(self) -> None:
        """Build the initial snapshot of the MPD database (directory -> files)."""
        for song in self._execute("listall"):
            if 'file' in song:
                directory = os.path.dirname(song['file'])
                self.snapshot[directory].add(song['file'])

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
                directory = os.path.dirname(song['file'])
                current_snapshot[directory].add(song['file'])

        # Compare old and new snapshots
        all_dirs = set(self.snapshot.keys()) | set(current_snapshot.keys())
        for directory in all_dirs:
            old_songs = self.snapshot.get(directory, set())
            new_songs = current_snapshot.get(directory, set())
            added = new_songs - old_songs
            removed = old_songs - new_songs
            if added:
                added_per_dir[directory] = added
            if removed:
                removed_per_dir[directory] = removed

        # Update the snapshot
        self.snapshot = current_snapshot
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
        self.running = True
        while self.running:

            # Block until one or more events occur
            events = self._execute("idle")

            if not events:
                oradio_log.error("MPD idle command returned no events or failed")
                time.sleep(1)
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
                time.sleep(1)
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

            time.sleep(1)

# -----Public methods----------------

    def start(self) -> None:
        """Start the background listener thread if not already running."""
        if not self.thread.is_alive():
            self.thread.start()
            oradio_log.info("MPD Event Monitor started")

    def stop(self) -> None:
        """Stop the background listener thread and wait for it to terminate."""
        self.running = False
        self.thread.join(timeout=2)
        oradio_log.info("MPD Event Monitor stopped")

# Create singleton monitor running in background
mpd_monitor = MPDEventMonitor()

# Entry point for stand-alone operation
if __name__ == '__main__':

    print("Running MPD event monitor")
    mpd_monitor.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        mpd_monitor.stop()
    print("Exiting MPD event monitor")
