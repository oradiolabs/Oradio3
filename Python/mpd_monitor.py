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
from threading import Thread, Lock
from collections import defaultdict
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log

##### Local constants ####################
MPD_HOST        = "localhost"
MPD_PORT        = 6600
MPD_RETRIES     = 3
MPD_RETRY_DELAY = 0.5       # seconds
LOCK_TIMEOUT    = 5         # seconds
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

        self._lock = Lock()
        self._client = MPDClient()
        self._connected = False     # Track connection state
        self._connect_client()      # Connect on init

        # Snapshot of MPD database: directory -> set of file paths
        self._snapshot = defaultdict(set)
        self._build_initial_snapshot()

        # Background thread for monitoring MPD events
        self._thread = Thread(target=self._listen, daemon=True)
        self._running = False

# -----Helper methods----------------

# In below code using same construct in mpd_control module
# pylint: disable=duplicate-code

    def _connect_client(self) -> None:
        """
        Attempt to establish a connection to the MPD server with retry logic.
        - Skips connect if already connected.
        - Retries up to MPD_RETRIES times on actual connection failure.
        - Uses exponential backoff for retry delays.
        - Sets crossfade after successful connection.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            try:
                # Only connect if not already connected
                if not self._connected:
                    # Attempt to connect to the MPD server
                    self._client.connect(MPD_HOST, MPD_PORT)
                    self._connected = True
                    oradio_log.info("Connected to MPD on %s:%s", MPD_HOST, MPD_PORT)
                else:
                    oradio_log.debug("MPD client already connected, skipping connect")

                return  # Connection successful, exit method

            except MPDConnectionError as ex_err:
                # Connection failed; mark as disconnected
                self._connected = False
                # Wait and retry using exponential backoff
                delay = MPD_RETRY_DELAY * (2 ** (attempt - 1))
                oradio_log.warning("Connection attempt %d/%d failed (%s). Retrying in %.1f seconds...", attempt, MPD_RETRIES, ex_err, delay)
                sleep(delay)

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                # Catch-all for unexpected errors
                self._connected = False
                oradio_log.exception("Unexpected error during MPD connection attempt %d/%d: %s", attempt, MPD_RETRIES, ex_unexpected)
                sleep(MPD_RETRY_DELAY * (2 ** (attempt - 1)))

        # All retries exhausted
        oradio_log.error("Failed to connect to MPD after %d attempts", MPD_RETRIES)

    def _execute(self, command: str, *args, allow_reconnect: bool = True, **kwargs) -> object | None:
        """
        Execute an MPD command safely and efficiently.
        - Retries only on actual lost connections.
        - ProtocolErrors are handled intelligently.
        - Supports lock timeout to prevent deadlocks.
        - Uses exponential backoff for retries.
        Args:
            command (str): MPD command to execute.
            *args: Positional arguments.
            allow_reconnect (bool): Prevent recursive reconnects.
            **kwargs: Keyword arguments.
        Returns:
            Result of the command, or None if all retries fail.
        """
        function = getattr(self._client, command, None)
        if not callable(function):
            oradio_log.error("Invalid MPD command: '%s'", command)
            return None

        for attempt in range(1, MPD_RETRIES + 1):
            acquired = False
            try:
                # Acquire lock with timeout
                acquired = self._lock.acquire(timeout=LOCK_TIMEOUT)
                if not acquired:
                    oradio_log.warning("Timeout waiting for MPD lock (attempt %d/%d, command '%s')", attempt, MPD_RETRIES, command)
                    sleep(MPD_RETRY_DELAY * (2 ** (attempt - 1)))
                    continue

                oradio_log.debug("Executing MPD command '%s' (attempt %d/%d)", command, attempt, MPD_RETRIES)

                # Execute the MPD command
                return function(*args, **kwargs)

            except CommandError as ex_err:
                # Command invalid; connection is still alive
                oradio_log.error("MPD command '%s' failed: %s", command, ex_err)
                return None

            except (MPDConnectionError, BrokenPipeError, ConnectionResetError) as ex_err:
                # Connection lost; mark disconnected
                self._connected = False
                oradio_log.warning("MPD connection lost during '%s' (%s). Retry %d/%d...", command, ex_err, attempt, MPD_RETRIES)
                if allow_reconnect:
                    self._connect_client()
                sleep(MPD_RETRY_DELAY * (2 ** (attempt - 1)))

            except ProtocolError as ex_err:
                # Only reconnect if the connection is actually dead
                if not self._connected:
                    oradio_log.warning("ProtocolError indicates lost connection during '%s' (%s). Reconnecting...", command, ex_err)
                    if allow_reconnect:
                        self._connect_client()
                    sleep(MPD_RETRY_DELAY * (2 ** (attempt - 1)))
                else:
                    # Connection alive; fail command without reconnect
                    oradio_log.error("ProtocolError while executing '%s', connection appears alive: %s", command, ex_err)
                    return None

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                # Unexpected runtime error
                oradio_log.exception("Unexpected error executing MPD command '%s': %s", command, ex_unexpected)
                return None

            finally:
                # Release lock if acquired
                if acquired:
                    self._lock.release()

        # All retries exhausted
        oradio_log.error("Failed to execute MPD command '%s' after %d retries", command, MPD_RETRIES)
        return None

# In above code using same construct in mpd_control module
# pylint: enable=duplicate-code

    def _build_initial_snapshot(self) -> None:
        """Build the initial snapshot of the MPD database (directory -> files)."""
        for song in self._execute("listall"):
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

    print("Running MPD event monitor")
    mpd_monitor.start()
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        mpd_monitor.stop()
    print("Exiting MPD event monitor")
