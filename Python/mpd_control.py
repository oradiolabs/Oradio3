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
    Oradio MPD control module
    - Thread-safe access with _lock
    - Automatic reconnect if MPD is down or connection drops
    - Retries commands up to MPD_RETRIES times before raising a MPDConnectionError
"""
import os
import time
import json
import threading
import unicodedata
from threading import Lock
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, RED, YELLOW, NC,
    PRESETS_FILE,
    USB_MUSIC,
)

##### Local constants ####################
MPD_HOST        = "localhost"
MPD_PORT        = 6600
MPD_RETRIES     = 3
MPD_RETRY_DELAY = 1          # seconds
MPD_CROSSFADE   = 5          # seconds
LOCK_TIMEOUT    = 5          # seconds
DEFAULT_PRESET  = "Preset1"  # For when the Play button is used and no playlist in the queue
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

@staticmethod
def _connect_to_mpd(client, log_prefix):
    """
    Attempt to connect an MPDClient instance with retries.
    Args:
        client (MPDClient): The MPD client to connect.
        log_prefix (str): Prefix for logging, useful to differentiate main client vs listener.
    Returns:
        bool: True if connected, False if all retries failed.
    """
    for attempt in range(1, MPD_RETRIES + 1):
        try:
            oradio_log.info("%s connecting to MPD service on %s:%s", log_prefix, MPD_HOST, MPD_PORT)
            client.connect(MPD_HOST, MPD_PORT)
            oradio_log.info("%s connected to MPD", log_prefix)
            return True
        except MPDConnectionError as ex_err:
            oradio_log.warning("%s connection failed (%s). Retry %d/%d", log_prefix, ex_err, attempt, MPD_RETRIES)
            time.sleep(MPD_RETRY_DELAY)
    oradio_log.error("%s failed to connect to MPD after %d attempts", log_prefix, MPD_RETRIES)
    return False

class MPDListener:
    """Singleton class that listens for MPD events and logs any errors."""

# In below code using same construct in multiple modules for singletons
# pylint: disable=duplicate-code

    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    # Underscores mark args and kwargs as 'intentionally unused'
    def __new__(cls, *_args, **_kwargs):
        """Ensure only one instance of MPDListener is created (singleton pattern)"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MPDListener, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the listener and start as run as daemon."""
        # Prevent re-initialization if the singleton is created again
        if self._initialized:
            return  # Avoid re-initialization if already done
        self._initialized = True

# In above code using same construct in multiple modules for singletons
# pylint: enable=duplicate-code

        # Start listener thread as daemon
        self._listener_thread = threading.Thread(target=self._mpd_event_listener, daemon=True)
        self._listener_thread.start()

    def _mpd_event_listener(self):
        """
        Efficient MPD event listener using idle, batching multiple events for reduced overhead.
        Logs events and automatically reconnects if needed.
        """
        oradio_log.debug("MPD event listener thread started")
        listener_client = MPDClient()

        if not _connect_to_mpd(listener_client, log_prefix="Listener"):
            return  # give up if listener cannot connect

        while True:
            try:
                # Block until one or more events occur
                events = listener_client.idle()  # pylint: disable=no-member

                # Collect all event types in a set for batched processing
                event_set = set(events)
                for event in events:
                    detail = MPD_EVENT_ACTIONS.get(event, "Unknown event")
                    oradio_log.info("MPD event: %s → %s", event, detail)

                # Handle database updates once
                if "database" in event_set:
                    try:
                        all_files = listener_client.listallinfo()
                        oradio_log.info("MPD database updated; %d files indexed", len(all_files))
                    except Exception as ex_err:
                        oradio_log.error("Failed to fetch database info: %s", ex_err)

                # Handle playlist and player events once per batch
                if event_set & {"playlist", "player"}:
                    try:
                        status = listener_client.status()
                        current_song = listener_client.currentsong()
                        oradio_log.debug("Batched update - Status: %s, Current song: %s", status, current_song)
                    except CommandError as ex_err:
                        oradio_log.error("Failed to fetch status/song info: %s", ex_err)

                # Optional: handle other events that don't fit above categories
                other_events = event_set - {"database", "playlist", "player"}
                for event in other_events:
                    oradio_log.debug("Other MPD event: %s", event)

            except (MPDConnectionError, ProtocolError, BrokenPipeError) as ex_err:
                oradio_log.warning("MPD connection lost or protocol error (%s). Reconnecting...", ex_err)
                while not _connect_to_mpd(listener_client, log_prefix="Listener"):
                    time.sleep(MPD_RETRY_DELAY)

            except Exception as ex_err:
                oradio_log.error("Unexpected listener error: %s", ex_err)
                time.sleep(1)

class MPDControl:
    """
    Thread-safe wrapper for an MPD (Music Player Daemon) client.
    Ensures that all MPD commands are executed safely in a multi-threaded
    environment. Automatically reconnects if the connection to the MPD server
    is lost.
    Attributes:
        _lock (threading.Lock): Lock to ensure thread-safe access.
        _client (MPDClient): Internal MPD client instance.
    """
    def __init__(self):
        """Initialize the MPDControl client and connect to the MPD server."""
        self._lock = Lock()
        # Connect to MPD service
        self._connected = False
        self._client = MPDClient()
        self._connect_client()
        # Trigger database update and wit until finished
        self.update_database()

    def _connect_client(self):
        """Establish a connection to the MPD server and configure crossfade."""
        if _connect_to_mpd(self._client, log_prefix="Oradio"):
            _ = self._execute("crossfade", MPD_CROSSFADE)
            self._connected = True
        else:
            oradio_log.error("Could not connect to MPD server")
            self._connected = False

    def _execute(self, command_name, *args, **kwargs):
        """
        Execute an MPD command in a thread-safe manner with automatic reconnect.
        Args:
            command_name (str): Name of the MPD command to execute.
            *args: Positional arguments to pass to the MPD command.
            **kwargs: Keyword arguments to pass to the MPD command.
        Returns:
            The result of the MPD command, or None if an error occurs.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            # Try to lock, timeout if waiting too long
            if self._lock.acquire(timeout=LOCK_TIMEOUT):    # pylint: disable=consider-using-with
                try:
                    func = getattr(self._client, command_name)
                    return func(*args, **kwargs)
                except (MPDConnectionError, ProtocolError, BrokenPipeError) as ex_err:
                    # NOTE: Normally MPD stays connected indefinitely (timeout ~1 year), but we still handle this defensively
                    oradio_log.warning("Control: Connection to MPD lost or invalid protocol (%s). Retry connecting %d/%d", ex_err, attempt, MPD_RETRIES)
                    self._connect_client()
                except CommandError as ex_err:
                    # MPD command-specific error
                    oradio_log.error("MPD command error: %s", ex_err)
                    return None
                except AttributeError:
                    oradio_log.error("Invalid MPD command: '%s'", command_name)
                    return None
                finally:
                    self._lock.release()
            else:
                oradio_log.warning("Attempt %d/%d: failed to acquire lock within %d seconds", attempt, MPD_RETRIES, LOCK_TIMEOUT)
        oradio_log.error("Failed to execute '%s' after %d retries", command_name, MPD_RETRIES)
        return None

    # Convenience methods
    def play(self, preset=None):
        """
        Start or resume playback.
        Args:
            preset (str, optional): The playlist preset to play.
        Rules:
        1. If a preset is provided, plays the associated playlist.
        2. If no preset is provided, resumes the current playlist.
        3. If no current playlist, plays the default preset playlist.
        """
        # Determine current playlist
        current_playlist_info = self._execute("playlistinfo") or []

        # Case 1: preset provided → use it
        if preset:
            if not isinstance(preset, str) or not preset.strip():
                oradio_log.error("Invalid preset provided: %r", preset)
                return
            preset = preset.strip()
        # Case 2: no preset, but there is a current playlist → resume
        elif current_playlist_info:
            _ = self._execute("play")
            oradio_log.debug("Resumed current playlist")
            return
        # Case 3: no preset and no current playlist → fallback to default preset
        else:
            preset = DEFAULT_PRESET
            oradio_log.debug("No current playlist, using default preset '%s'", preset)

        # Clear current queue before loading new playlist
        _ = self._execute("clear")

        # Resolve preset to a playlist or directory
        playlist_name = _get_preset_listname(preset, PRESETS_FILE)
        if not playlist_name:
            oradio_log.warning("No playlist found for preset: %s", preset)
            return

        # Get stored playlists safely
        stored_playlists = self._execute("listplaylists") or []
        playlist_names = [
            name.get("playlist") for name in stored_playlists
            if isinstance(name, dict) and name.get("playlist")
        ]

        if playlist_name in playlist_names:
            # Playlist exists: load sequentially
            _ = self._execute("load", playlist_name)
            _ = self._execute("random", 0)
            _ = self._execute("repeat", 1)
            oradio_log.debug("Loaded playlist '%s'", playlist_name)
        else:
            # Directory: add and shuffle
            _ = self._execute("add", playlist_name)
            _ = self._execute("shuffle")
            _ = self._execute("random", 1)
            _ = self._execute("repeat", 1)
            oradio_log.debug("Added directory '%s' and shuffled", playlist_name)

        # Start playback
        _ = self._execute("play")
        oradio_log.debug("Playback started for: %s", playlist_name)

    def play_song(self, song):
        """
        Play a single song immediately without clearing the current queue.
        The song is inserted after the currently playing song. Once finished,
        it is automatically removed from the playlist.
        Args:
            song (str): The URI or file path of the song to play.
        """
        if not song or not isinstance(song, str):
            oradio_log.error("Invalid song: %s", song)
            return

        oradio_log.debug("Attempting to play song: %s", song)

        # Get current song index safely
        status = self._execute("status") or {}
        try:
            current_index = int(status.get("song", -1))
        except (ValueError, TypeError):
            current_index = -1

        # Add the new song to the playlist and get its unique song ID
        inserted_song_id = self._execute("addid", song)
        if inserted_song_id is None:
            oradio_log.error("Failed to add song: %s", song)
            return

        # Ensure the song has been registered in playlist
        playlist = self._execute("playlistinfo") or []
        for i, song in enumerate(playlist):
            if int(song.get("id", -1)) == int(inserted_song_id):
                new_index = i
                break
        # The for ... else pattern ensures that the else is only executed if the for loop is not broken (i.e., no match is found)
        else:
            new_index = len(playlist) - 1

        # Determine where to insert: right after the current song, or at start if none
        target_index = current_index + 1 if current_index >= 0 else 0

        # Move the new song to the target position if necessary
        if new_index != target_index:
            _ = self._execute("move", new_index, target_index)

        # Start playback of the inserted song
        _ = self._execute("play", target_index)
        oradio_log.debug("Started playback at index %d for song id %s", target_index, inserted_song_id)

        # Start a background thread to remove the song once it finishes
        threading.Thread(
            target=self._remove_song_when_finished,
            args=(inserted_song_id,),
            daemon=True
        ).start()
        oradio_log.debug("Monitor removal for song id: %s", inserted_song_id)

    def _remove_song_when_finished(self, inserted_song_id):
        """
        Monitor a song and remove it after it finishes.
        Args:
            inserted_song_id (int or str): The MPD song ID to monitor and remove.
        """
        try:
            inserted_song_id = int(inserted_song_id)
        except (TypeError, ValueError):
            oradio_log.error("Invalid song ID provided: %s", inserted_song_id)
            return

        oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)

        # Wait until the song finishes or is skipped
        while True:
            time.sleep(0.5)  # Poll twice per second for responsiveness

            status = self._execute("status") or {}
            try:
                current_song_id = int(status.get("songid", -1))
            except (TypeError, ValueError):
                current_song_id = -1

            # Exit if current song changed
            if current_song_id != inserted_song_id:
                break

            # Check the elapsed time if available
            time_str = status.get("time")
            if time_str:
                # Protect agains time format errors
                try:
                    parts = time_str.strip().split(":")
                    if len(parts) != 2:
                        raise ValueError(f"Unexpected time format: {time_str}")
                    elapsed = float(parts[0])
                    duration = float(parts[1])
                    if elapsed >= duration - 0.5:
                        break
                except (AttributeError, ValueError) as ex_err:
                    oradio_log.warning("Failed to parse time for song id %s: '%s' (%s)", inserted_song_id, time_str, ex_err)

        # After finishing, remove the song from the playlist if still present
        playlist = self._execute("playlistinfo") or []
        for song in playlist:
            if isinstance(song, dict) and int(song.get("id", -1)) == inserted_song_id:
                _ = self._execute("deleteid", inserted_song_id)
                oradio_log.debug("Removed song id %s from playlist", inserted_song_id)
                break
        else:
            oradio_log.debug("Song id %s already removed", inserted_song_id)

    def pause(self):
        """Pause playback if a song is currently playing."""
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore pause: not currently playing (state=%s)", state)
            return

        # Pause playback
        _ = self._execute("pause")
        oradio_log.debug("Playback paused")

    def next(self):
        """
        Skip to the next song in the current playlist or directory.
        If a web radio is currently playing, the skip is ignored.
        Wraps around if the current playlist ends.
        """
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore next: not currently playing (state=%s)", state)
            return

        if self.is_webradio():
            oradio_log.debug("Ignore next: current item is a web radio")
            return

        # Get playlist info
        playlist_info = self._execute("playlistinfo") or []
        if not playlist_info:
            oradio_log.debug("Current playlist is empty, cannot skip to next")
            return

        # Determine current listname
        listname = self._get_current_listname()
        playlists = self._execute("listplaylists") or []
        playlist_names = [playlist.get("playlist") for playlist in playlists if isinstance(playlist, dict) and playlist.get("playlist")]

        if listname in playlist_names:
            # Wrap-around next index
            try:
                current_index = int(status.get("song", 0))
            except (TypeError, ValueError):
                current_index = 0
            next_index = (current_index + 1) % len(playlist_info)

            # Reload playlist safely
            _ = self._execute("clear")
            _ = self._execute("load", listname)

            # Play next song
            _ = self._execute("play", next_index)
            oradio_log.debug("Skipped to next song in playlist '%s', index %d", listname, next_index)
        else:
            # Playing a directory: just send MPD 'next' command
            _ = self._execute("next")
            oradio_log.debug("Skipped to next song in directory using MPD 'next' command")

    def stop(self):
        """Stop playback if a song is currently playing."""
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore stop because not playing")
            return

        # Stop play
        _ = self._execute("stop")
        oradio_log.debug("Playback stopped")

    def clear(self):
        """Clear the current MPD playlist/queue."""
        _ = self._execute("clear")
        oradio_log.debug("Current playback queue cleared")

    def add(self, playlist, song):
        """
        Create a playlist if it does not exist, and optionally add a song to it.
        Args:
            playlist (str): Name of the playlist to create or modify.
            song (str | None): Song URL or file to add. If None, only create the playlist.
        """
        # Validate playlist
        if not playlist or not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        playlist = playlist.strip()

        # Check if playlist exists
        playlists = self._execute("listplaylists") or []
        playlist_names = [name.get("playlist") for name in playlists if isinstance(name, dict) and name.get("playlist")]
        exists = playlist in playlist_names

        # Create playlist if it does not exist
        if not exists:
            oradio_log.debug("Creating playlist '%s'", playlist)
            # Add dummy song to initialize playlist
            _ = self._execute("playlistadd", playlist, "https://dummy.mp3")
            # Remove dummy to leave playlist empty
            _ = self._execute("playlistdelete", playlist, 0)
            oradio_log.debug("Playlist '%s' created", playlist)
        else:
            oradio_log.debug("Playlist '%s' already exists", playlist)

        # Add song if provided
        if song:
            if not isinstance(song, str) or not song.strip():
                oradio_log.error("Invalid song name: %r", song)
                return

            song = song.strip()
            song_path = os.path.join(USB_MUSIC, song)

            if not os.path.isfile(song_path):
                oradio_log.error("Song file does not exist: %s", song_path)
                return

            oradio_log.debug("Adding song '%s' to playlist '%s'", song, playlist)
            _ = self._execute("playlistadd", playlist, song)
            # Refresh MPD playlist info
            _ = self._execute("listplaylistinfo", playlist)
            oradio_log.debug("Song '%s' added to playlist '%s'", song, playlist)

    def remove(self, playlist, song):
        """
        Remove a song from a playlist, or delete the entire playlist.
        Args:
            playlist (str): Name of the playlist to modify.
            song (str | None): Song to remove. If None, deletes the entire playlist.
        """
        # Validate playlist
        if not playlist or not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        playlist = playlist.strip()

        if not song:
            # Delete the entire playlist
            oradio_log.debug("Attempting to remove playlist '%s'", playlist)
            playlists = self._execute("listplaylists") or []

            # Find matching playlist safely
            playlist_names = [name.get("playlist") for name in playlists if isinstance(name, dict) and name.get("playlist")]
            if playlist in playlist_names:
                _ = self._execute("rm", playlist)
                oradio_log.debug("Playlist '%s' removed", playlist)
            else:
                oradio_log.warning("Playlist '%s' does not exist", playlist)

        else:
            # Remove a single song
            if not isinstance(song, str) or not song.strip():
                oradio_log.error("Invalid song name: %r", song)
                return

            song = song.strip()
            oradio_log.debug("Attempting to remove song '%s' from playlist '%s'", song, playlist)

            items = self._execute("listplaylist", playlist) or []

            # Find the index of the song (supports dicts with "file" or strings)
            index = next(
                (i for i, entry in enumerate(items)
                 if (isinstance(entry, dict) and entry.get("file") == song) or
                    (isinstance(entry, str) and entry == song)),
                None
            )

            if index is None:
                oradio_log.warning("Song '%s' not found in playlist '%s'", song, playlist)
            else:
                _ = self._execute("playlistdelete", playlist, index)
                oradio_log.debug("Song '%s' removed from playlist '%s'", song, playlist)
                # Refresh playlist info
                _ = self._execute("listplaylistinfo", playlist)

    def update_database(self):
        """Update the MPD database first for presets, finally for everything else."""

         # Start the database update for the preset list
        for preset in ["Preset1", "Preset2", "Preset3"]:
            listname = _get_preset_listname(preset, PRESETS_FILE)
            if not listname:
                oradio_log.warning("Preset %s not found in presets file", preset)
                continue
            oradio_log.debug("Updating MPD database for prset '%s' with list '%s'", preset, listname)
            _ = self._execute("update", listname)

        # Start the database update for the rest
        oradio_log.debug("Updating MPD database")
        _ = self._execute("update")

    def is_webradio(self, preset=None, listname=None):
        """
        Check if a preset or list is a web radio URL.
        Args:
            preset (str, optional): Preset name to check.
            listname (str, optional): Playlist or directory name to check.
        Rules:
            - Both args set → invalid
            - Both args None → check currently playing song
            - Only preset → check that preset list
            - Only listname → check that list
        Returns:
            bool: True if the song or preset is a web radio URL, False otherwise.
        """
        # Input validation
        if preset and listname:
            oradio_log.error("Invalid parameters: both 'preset' and 'listname' provided")
            return False

        # Determine listname or current song
        if not preset and not listname:
            # Check currently playing song
            current_song = self._execute("currentsong") or {}
            file_uri = current_song.get("file")
        else:
            # Resolve listname from preset if needed
            if preset:
                listname = _get_preset_listname(preset, PRESETS_FILE)
                if not listname:
                    oradio_log.info("Preset '%s' has no associated playlist", preset)
                    return False

                # Verify playlist exists
                playlists = self._execute("listplaylists") or []
                valid_names = [p.get("playlist") for p in playlists if isinstance(p, dict) and p.get("playlist")]
                if listname not in valid_names:
                    oradio_log.debug("Listname '%s' not found in playlists", listname)
                    return False

            # Get first song from playlist
            songs = self._execute("listplaylist", listname) or []
            if not songs:
                oradio_log.debug("Playlist '%s' is empty", listname)
                return False

            first_song = songs[0]
            file_uri = first_song.get("file") if isinstance(first_song, dict) else first_song

        # Safe check and lowercase for URL check
        if not isinstance(file_uri, str):
            oradio_log.debug("Unexpected song entry type in '%s': %r", listname or "current song", file_uri)
            return False

        # Final check for web radio URL
        return (file_uri or "").lower().startswith(("http://", "https://"))

    def _get_current_listname(self):
        """
        Retrieve the name of the current playlist or directory.
        Returns:
            str | None: Name of the current playlist or directory, or None if the queue is empty.
        """
        info = self._execute("playlistinfo") or []
        if not info:
            return None

        first_entry = info[0]

        if not isinstance(first_entry, dict):
            oradio_log.debug("Unexpected playlistinfo entry type: %s", first_entry)
            return None

        file_path = first_entry.get("file")
        if not file_path or not isinstance(file_path, str):
            oradio_log.debug("Missing or invalid 'file' in playlistinfo entry: %s", first_entry)
            return None

        # Return the base directory name
        return os.path.basename(os.path.dirname(file_path))

    def get_directories(self):
        """
        Retrieve available directories from MPD.
        Returns:
            list[str]: Case-insensitive, alphabetically sorted list of directory names.
        """
        # Get directories
        directories = self._execute("listfiles") or []

        # Extract valid directory names
        folders = []
        for directory in directories:
            if not isinstance(directory, dict):
                oradio_log.debug("Skipping invalid directory entry: %s", directory)
                continue

            name = directory.get("directory")
            if not name or not isinstance(name, str) or not name.strip():
                oradio_log.debug("Skipping empty or invalid directory name: %s", directory)
                continue

            folders.append(name.strip())

        # Case-insensitive alphabetical sort
        return sorted(folders, key=str.casefold)

    def get_playlists(self):
        """
        Retrieve available playlists from MPD.
        Returns:
            list[dict]: Case-insensitive sorted list of dicts with keys:
                        'playlist' (str) and 'webradio' (bool).
        """
        # Get playlists from MPD
        playlists = self._execute("listplaylists") or []

        result = []
        for playlist in playlists:
            if not isinstance(playlist, dict):
                oradio_log.debug("Skipping invalid playlist entry: %s", playlist)
                continue

            name = playlist.get("playlist")
            if not name or not str(name).strip():
                oradio_log.debug("Skipping empty playlist entry: %s", playlist)
                continue

            result.append({
                "playlist": name,
                "webradio": self.is_webradio(listname=name)
            })

        # Case-insensitive alphabetical sort
        return sorted(result, key=lambda x: x["playlist"].casefold())

    def get_songs(self, listname):
        """
        List songs from a playlist or directory.
        Args:
            listname (str): Name of the playlist or directory.
        Returns:
            list[dict]: List of song dictionaries with keys:
                        'file' (str), 'artist' (str), 'title' (str).
        """
        # Protect against empty or no listname
        if not listname or not str(listname).strip():
            oradio_log.warning("Empty or None listname received")
            return []

        # Helper to sanitize artist/title
        def _safe(value, fallback):
            """Return value if a non-empty string, else fallback."""
            # Protect against values like: None, "", " ", 0, [], {}
            return value if isinstance(value, str) and value.strip() else fallback

        # Check playlists
        playlists = self._execute("listplaylists") or []
        for playlist in playlists:
            pname = playlist.get("playlist") if isinstance(playlist, dict) else None
            if pname == listname:
                songs = []
                details = self._execute("listplaylistinfo", listname) or []
                if not details:
                    oradio_log.debug("No songs found for playlist '%s'", listname)
                    return []
                # Use list comprehension for compactness
                songs = [
                    {
                        "file": _safe(d.get("file"), ""),
                        "artist": _safe(d.get("artist"), "Unknown artist"),
                        "title": _safe(d.get("title"), "Unknown title"),
                    }
                    for d in details
                    if isinstance(d, dict)
                ]
                # Preserve playlist order
                return songs

        # If not a playlist, try as a directory
        directories = self._execute("listfiles") or []
        for directory in directories:
            dname = directory.get("directory") if isinstance(directory, dict) else None
            if dname == listname:
                songs = []
                details = self._execute("lsinfo", listname) or []
                if not details:
                    oradio_log.debug("No songs found for directory '%s'", listname)
                    return []
                # Use list comprehension for compactness
                songs = [
                    {
                        "file": _safe(d.get("file"), ""),
                        "artist": _safe(d.get("artist"), "Unknown artist"),
                        "title": _safe(d.get("title"), "Unknown title"),
                    }
                    for d in details
                    if isinstance(d, dict)
                ]
                # Alphabetical sort by artist
                return sorted(songs, key=lambda x: x["artist"].casefold())

        # Return empty as list is not a known playlist or directory
        oradio_log.debug("Listname '%s' not found as playlist or directory", listname)
        return []

    def search(self, pattern):
        """
        Search for songs by artist or title, removing duplicates.
        Args:
            pattern (str): Search string to match against artist or title.
        Returns:
            list[dict]: Unique songs sorted by normalized artist and title. Each dict has keys:
                        'file', 'artist', 'title'.
        """

        def _normalize(text: str) -> str:
            """Normalize a string for comparison by removing case and diacritics."""
            if not isinstance(text, str):
                return ""
            text = text.strip().lower()
            text = unicodedata.normalize('NFD', text)
            return ''.join(c for c in text if unicodedata.category(c) != 'Mn')

        if not pattern or not isinstance(pattern, str) or not pattern.strip():
            oradio_log.debug("Empty or invalid search pattern: %s", pattern)
            return []

        pattern = pattern.strip()

        # Search artists and titles
        results = []
        results += self._execute("search", 'artist', pattern) or []
        results += self._execute("search", 'title', pattern) or []

        # Compile formatted songs
        songs = []
        for result in results:
            if not isinstance(result, dict):
                continue
            file_uri = result.get('file')
            if not file_uri or not isinstance(file_uri, str):
                continue

            artist = result.get('artist') or "Unknown artist"
            title = result.get('title') or "Unknown title"

            songs.append({
                'file': file_uri,
                'artist': artist,
                'normalized_artist': _normalize(artist),
                'title': title,
                'normalized_title': _normalize(title)
            })

        # Remove duplicates based on normalized artist and title
        seen = set()
        unique_songs = []
        for song in songs:
            key = (song['normalized_artist'], song['normalized_title'])
            if key not in seen:
                seen.add(key)
                unique_songs.append(song)

        # Sort by normalized artist and title for case- and accent-insensitive order
        return sorted(unique_songs, key=lambda x: (x['normalized_artist'], x['normalized_title']))

@staticmethod
def _get_preset_listname(preset_key, filepath):
    """
    Retrieve the playlist name associated with a given preset key from a JSON file.
    Args:
        preset_key (str): The preset key to look up (case-insensitive).
        filepath (str): Path to the JSON file containing preset mappings.
    Returns:
        str | None: The playlist name if found, otherwise None.
    """
    if not preset_key or not isinstance(preset_key, str) or not preset_key.strip():
        oradio_log.debug("Invalid preset key: %r", preset_key)
        return None

    preset_key = preset_key.strip().lower()

    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            presets = json.load(file)
            if not isinstance(presets, dict):
                oradio_log.error("Invalid JSON format in %s: expected dict", filepath)
                return None
        # Safely return value if string
        value = presets.get(preset_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        oradio_log.debug("Preset '%s' not found or invalid in %s", preset_key, filepath)
        return None
    except FileNotFoundError:
        oradio_log.error("File not found at %s", filepath)
    except json.JSONDecodeError:
        oradio_log.error("Failed to JSON decode %s", filepath)
    return None

# Start MPD Listener daemon
_mpd_listener = MPDListener()

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from oradio_utils import run_shell_script

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # Pylint PEP8 ignoring limit of max 12 branches and 50 statement is ok for test menu
    def interactive_menu():     # pylint: disable=too-many-branches, too-many-statements
        """Show menu with test options"""
        input_selection = (
            "\nSelect a function, input the number:\n"
            " 0-Quit\n"
            " 1-Play\n"
            " 2-Pause\n"
            " 3-Next\n"
            " 4-Stop\n"
            " 5-Clear\n"
            " 6-Play Preset 1\n"
            " 7-Play Preset 2\n"
            " 8-Play Preset 3\n"
            " 9-List directories\n"
            "10-Play a directory\n"
            "11-List playlists\n"
            "12-Play a playlist\n"
            "13-List playlist songs\n"
            "14-Add (song to) a playlist\n"
            "15-Remove (song from) a playlist\n"
            "16-Search song(s)\n"
            "17-Play a song\n"
            "18-Check if preset is web radio\n"
            "19-Check if current song is web radio\n"
            "20-Update Database\n"
            "21-Restart MPD Service\n"
            "Select: "
        )

        # Initialise MPD client
        mpd_client = MPDControl()

        while True:
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    print("\nExecuting: Play.\n")
                    mpd_client.play()
                case 2:
                    print("\nExecuting: Pause.\n")
                    mpd_client.pause()
                case 3:
                    print("\nExecuting: Next.\n")
                    mpd_client.next()
                case 4:
                    print("\nExecuting: Stop.\n")
                    mpd_client.stop()
                case 5:
                    print("\nExecuting: Clear.\n")
                    mpd_client.clear()
                case 6:
                    print("\nExecuting: Play Preset 1\n")
                    mpd_client.play(preset="Preset1")
                case 7:
                    print("\nExecuting: Play Preset 2\n")
                    mpd_client.play(preset="Preset2")
                case 8:
                    print("\nExecuting: Play Preset 3\n")
                    mpd_client.play(preset="Preset3")
                case 9:
                    print("\nListing directories:")
                    results = mpd_client.get_directories()
                    if not results:
                        print(f"{YELLOW}No directories found{NC}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            print(f"{GREEN}{idx:>2}. {result}{NC}")
                case 10:
                    print(f"\n{YELLOW}Play directory: to be implemented{NC}")
                case 11:
                    print("\nListing playlists:")
                    results = mpd_client.get_playlists()
                    if not results:
                        print(f"{YELLOW}No playlists found{NC}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            webradio_tag = "(webradio)" if result.get("webradio") else ""
                            print(f"{GREEN}{idx:>2}. {result.get('playlist')} {webradio_tag}{NC}")
                case 12:
                    print(f"\n{YELLOW}Play directory: to be implemented{NC}")
                case 13:
                    print("\nListing songs")
                    selection = input("Enter playlist or directory: ")
                    results = mpd_client.get_songs(selection)
                    if not results:
                        print(f"No songs found for list {selection}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            print(f"{GREEN}{idx:>3}. {result}{NC}")
                case 14:
                    print("\nAdd (song to) a playlist")
                    name = input("Enter playlist name: ")
                    song = input("Enter playlist song (playlist/songfile): ")
                    mpd_client.add(name, song)
                case 15:
                    print("\nRemove (song from) a playlist")
                    name = input("Enter playlist name: ")
                    song = input("Enter playlist song (playlist/songfile): ")
                    mpd_client.remove(name, song)
                case 16:
                    print("\nSearch song(s)")
                    pattern = input("Enter search pattern: ")
                    results = mpd_client.search(pattern)
                    for idx, result in enumerate(results, start=1):
                        print(f"{GREEN}{idx:>2}. {result.get('artist')} - {result.get('title')}{NC}")
                case 17:
                    print(f"\n{YELLOW}Play a song (to be implemented){NC}")
                case 18:
                    selection = input("\nEnter preset number 1, 2 or 3 to check: ")
                    if selection.isdigit() and int(selection) in range(1, 4):
                        if mpd_client.is_webradio(preset=f"Preset{selection}"):
                            print(f"\n{GREEN}Preset{selection} playlist is a web radio{NC}\n")
                        else:
                            print(f"\n{GREEN}Preset{selection} playlist is NOT a web radio{NC}\n")
                    else:
                        print(f"\n{YELLOW}Invalid preset. Please enter a valid number{NC}\n")
                case 19:
                    if mpd_client.is_webradio():
                        print(f"\n{GREEN}Current playlist is a web radio{NC}\n")
                    else:
                        print(f"\n{GREEN}Current playlist is NOT a web radio{NC}\n")
                case 20:
                    print("\nExecuting: Update MPD Database\n")
                    mpd_client.update_database()
                case 21:
                    print("\nExecuting: Restart MPD Service\n")
                    result, response = run_shell_script("sudo systemctl restart mpd.service")
                    if not result:
                        print(f"\n{RED}Failed to restart MPD service: {response}${NC}\n")
                    else:
                        print(f"\n{GREEN}MPD service restarted successfully{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
