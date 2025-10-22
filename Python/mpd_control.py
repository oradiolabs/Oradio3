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
    RED, YELLOW, NC,
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
        """
        Initialize the MPDControl client and connect to the MPD server.
        """
        self._lock = Lock()
        # Connect to MPD service
        self._connected = False
        self._client = MPDClient()
        self._connect_client()
        # Start listener thread
        self._listener_thread = threading.Thread(target=self._mpd_event_listener, daemon=True)
        self._listener_thread.start()

    def _connect_to_mpd(self, client, log_prefix):
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

    def _mpd_event_listener(self):
        """
        Stand-alone listener to MPD events.
        Logs events and automatically reconnects if needed.
        """
        oradio_log.debug("MPD event listener thread started")
        listener_client = MPDClient()

        # Connect listener using shared helper
        if not self._connect_to_mpd(listener_client, log_prefix="Listener"):
            return  # give up if listener cannot connect

        # Main idle loop
        while True:
            try:
                # blocks until MPD sends events
                # Keep it simple: no need to use _execute(), as listener is only user
                events = listener_client.idle() # pylint: disable=no-member
                for event in events:
                    detail = MPD_EVENT_ACTIONS.get(event, "Unknown event")
                    oradio_log.debug("MPD event: %s → detail: %s", event, detail)
                    # Check errors on MPD events
                    try:
                        # Keep it simple: no need to use _execute(), as listener is only user
                        status = listener_client.status()   # pylint: disable=no-member
                        if status and "error" in status:
                            oradio_log.warning("MPD error detected: %s", status['error'])
                    except CommandError as ex_err:
                        oradio_log.error("Error fetching status: %s", ex_err)
            except MPDConnectionError as ex_err:
                oradio_log.warning("Listener connection lost, reconnecting: %s", ex_err)
                while not self._connect_to_mpd(listener_client, log_prefix="Listener"):
                    time.sleep(MPD_RETRY_DELAY)
            # We want to know if the listener fails and why
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Listener error: %s", ex_err)
                time.sleep(1)

    def _connect_client(self):
        """
        Establish a connection to the MPD server and configure crossfade.
        """
        if self._connect_to_mpd(self._client, log_prefix="Oradio"):
            self._execute("crossfade", MPD_CROSSFADE)
            self._connected = True
        else:
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
                    oradio_log.warning("Connection to MPD lost or invalid protocol (%s). Retry connecting %d/%d", ex_err, attempt, MPD_RETRIES)
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

    def is_connected(self):
        """
        Check whether the client is currently connected to MPD.

        Returns:
            bool: True if connected, False otherwise.
        """
        return self._connected

    # Convenience methods
    def play(self, preset=None):
        """
        Start or resume playback.

        If a preset is provided, plays the associated playlist.
        If no preset is provided, resumes the current playlist or defaults
        to the default preset if none is playing.

        Args:
            preset (str, optional): The playlist preset to play.

        Returns:
            bool: True if playback started, False if the preset playlist was not found.
        """
        # Determine what to play
        if preset is None:
            if self._execute("playlistinfo"):
                # Play current playlist
                self._execute("play")
                oradio_log.debug("Play current playlist")
                return True
            # Play default preset playlist
            oradio_log.debug("No current playlist, using default preset %s", DEFAULT_PRESET)
            preset = DEFAULT_PRESET

        # Clear the current playlist/queue
        self._execute("clear")

        # Get playlist linked to preset
        playlist_name = _get_preset_listname(preset, PRESETS_FILE)
        if not playlist_name:
            oradio_log.debug("No playlist found for preset: %s", preset)
            return False

        # Get list of names of the playlist in the playlist directory, empty on error
        stored_playlists = self._execute("listplaylists") or []
        playlist_names = [pl.get("playlist") for pl in stored_playlists]

        if playlist_name in playlist_names:
            # Playlist: load in saved order
            self._execute("load", playlist_name)
            self._execute("random", 0)      # Sets random state to sequential
            self._execute("repeat", 1)      # Sets repeat state to loop
        else:
            # Directory: add and shuffle
            self._execute("add", playlist_name)
            self._execute("shuffle")        # Shuffles the current playlist
            self._execute("random", 1)      # Sets random state to random
            self._execute("repeat", 1)      # Sets repeat state to loop

        # Play configured playlist
        self._execute("play")
        oradio_log.debug("Playing: %s", playlist_name)
        return True

    def play_song(self, song):
        """
        Play a single song immediately without clearing the current queue.

        The song is inserted after the currently playing song. Once finished,
        it is automatically removed from the playlist.

        Args:
            song (str): The URI or file path of the song to play.
        """
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
        new_index = next((i for i, s in enumerate(playlist) if int(s.get("id", -1)) == inserted_song_id), len(playlist)-1)

        # Determine where to insert: right after the current song, or at start if none
        target_index = current_index + 1 if current_index >= 0 else 0

        # Move the new song to the target position if necessary
        if new_index != target_index:
            self._execute("move", new_index, target_index)

        # Start playback of the inserted song
        self._execute("play", target_index)
        oradio_log.debug("Started playback at index %s for song id %s", target_index, inserted_song_id)

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

        Uses MPD 'idle' to efficiently wait for the song to end.

        Args:
            inserted_song_id (int): The MPD song ID to monitor and remove.
        """
        oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)

        # Wait until the song finishes or is skipped
        while True:
            time.sleep(0.5)  # Poll twice per second for responsiveness

            status = self._execute("status") or {}
            current_song_id = int(status.get("songid", -1))

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
        if any(int(song.get("id", -1)) == inserted_song_id for song in playlist):
            self._execute("deleteid", inserted_song_id)
            oradio_log.debug("Remove song id %s", inserted_song_id)
        else:
            oradio_log.debug("Song id %s already removed", inserted_song_id)

    def pause(self):
        """
        Pause playback if a song is currently playing.
        """
        status = self._execute("status")

        if status.get("state") != "play":
            oradio_log.debug("Ignore pause because not playing")
            return

        # Pause play
        self._execute("pause")
        oradio_log.debug("Playback paused")

    def next(self):
        """
        Skip to the next song in the current playlist or directory.

        If a web radio is currently playing, the skip is ignored.
        Wraps around if the current playlist ends.
        """
        status = self._execute("status")

        if status.get("state") != "play":
            oradio_log.debug("Ignore next because not playing")
            return

        if self.is_webradio():
            oradio_log.debug("Ignore next because current item is a web radio")
            return

        # Get playlist info
        playlist_info = self._execute("playlistinfo") or []
        if not playlist_info:
            oradio_log.debug("Current playlist is empty, cannot skip to next")
            return

        # Check if listname is a stored playlist
        listname = self._get_current_listname()
        playlists = self._execute("listplaylists") or []
        playlist_names = [pl.get("playlist") for pl in playlists if "playlist" in pl]
        if listname in playlist_names:
            # Determine next index with wrap-around
            try:
                current_index = int(status.get("song", 0))
            except (TypeError, ValueError):
                current_index = 0
            next_index = (current_index + 1) % len(playlist_info)

            # Reload playlist in case it changed via web interface
            self._execute("clear")
            self._execute("load", listname)

            # Play the next song
            self._execute("play", next_index)
            oradio_log.debug("Skipped to next song in playlist '%s', index %d", listname, next_index)
        else:
            # Playing a directory: just send MPD 'next' command
            self._execute("next")
            oradio_log.debug("Skipped to next song in directory using MPD 'next' command")

    def stop(self):
        """
        Stop playback if a song is currently playing.
        """
        status = self._execute("status")

        if status.get("state") != "play":
            oradio_log.debug("Ignore stop because not playing")
            return

        # Stop play
        self._execute("stop")
        oradio_log.debug("Playback stopped")

    def clear(self):
        """
        Clear the current MPD playlist/queue.
        """
        self._execute("clear")
        oradio_log.debug("Current playback queue cleared")

    def add(self, playlist, song):
        """
        Create a playlist if it does not exist, and optionally add a song to it.

        Args:
            playlist (str): Name of the playlist to create or modify.
            song (str | None): Song URL or file to add. If None, only create the playlist.
        """
        # Validate playlist
        if not playlist:  # catches '', None, False, etc.
            oradio_log.error("Playlist name cannot be empty or None")
            return

        # Check if playlist exists
        playlists = self._execute("listplaylists")
        exists = any(p.get("playlist") == playlist for p in playlists)

        # Create playlist if it does not exist
        if not exists:
            oradio_log.debug("Creating playlist '%s'", playlist)
            # Add dummy song to initialize playlist
            self._execute("playlistadd", playlist, "https://dummy.mp3")
            # Remove dummy to leave playlist empty
            self._execute("playlistdelete", playlist, 0)
            oradio_log.debug("Playlist '%s' created", playlist)
        else:
            oradio_log.debug("Playlist '%s' already exists", playlist)

        # Add song if provided
        if song:
            # Validate song
            print("file to check=", os.path.join(USB_MUSIC, song))
            if not os.path.isfile(os.path.join(USB_MUSIC, song)):
                oradio_log.error("Song file does not exist: %s", song)
            else:
                oradio_log.debug("Adding song '%s' to playlist '%s'", song, playlist)
                self._execute("playlistadd", playlist, song)
                # Refresh MPD playlist info
                self._execute("listplaylistinfo", playlist)
                oradio_log.debug("Song '%s' added to playlist '%s'", song, playlist)

    def remove(self, playlist, song):
        """
        Remove a song from a playlist, or delete the entire playlist.

        Args:
            playlist (str): Name of the playlist to modify.
            song (str | None): Song to remove. If None, deletes the entire playlist.
        """
        # Validate playlist
        if not playlist:  # catches '', None, False, etc.
            oradio_log.error("Playlist name cannot be empty or None")
            return

        if not song:
            # Delete the playlist
            oradio_log.debug("Attempting to remove playlist '%s'", playlist)
            playlists = self._execute("listplaylists")
            if any(p.get("playlist") == playlist for p in playlists):
                self._execute("rm", playlist)
                oradio_log.debug("Playlist '%s' removed", playlist)
            else:
                oradio_log.warning("Playlist '%s' does not exist", playlist)

        else:
            # Remove a single song
            oradio_log.debug("Attempting to remove song '%s' from playlist '%s'", song, playlist)
            items = self._execute("listplaylist", playlist)
            # Find the index safely
            index = next((i for i, s in enumerate(items) if s == song), None)
            # Validate song
            if index is None:
                oradio_log.warning("Song '%s' not found in playlist '%s'", song, playlist)
            else:
                self._execute("playlistdelete", playlist, index)
                oradio_log.debug("Song '%s' removed from playlist '%s'", song, playlist)
                # Refresh MPD playlist info
                self._execute("listplaylistinfo", playlist)

    def update_database(self, progress_interval=5):
        """
        Update the MPD database and log progress.

        Args:
            progress_interval (int, optional): Time in seconds between progress log messages.
        """
        # Start the database update
        self._execute("update")
        oradio_log.debug("MPD database update started")

        # Wait and show progress until MPD database updated
        last_log_time = time.time()
        while True:
            # Get the current status of MPD
            status = self._execute("status")
            updating = status.get("updating_db")

            # If update is complete log indexed files and stop
            if not updating or updating == "0":
                indexed_files = len(self._execute("listallinfo"))
                oradio_log.debug("MPD database updated; %d files indexed", indexed_files)
                break

            # Periodically log that the update is still in progress
            if time.time() - last_log_time >= progress_interval:
                oradio_log.debug("MPD database updating still in progress")
                last_log_time = time.time()

            # Small sleep to avoid busy-waiting
            time.sleep(0.5)

    def is_webradio(self, preset=None, listname=None):
        """
        Check if a preset or list is a web radio URL.

        Rules:
            - If both `preset` and `listname` are None → check currently playing song
            - If only `preset` is set → check that preset list
            - If only `listname` is set → check that list
            - If both are set → invalid input

        Args:
            preset (str, optional): Preset name to check.
            listname (str, optional): Playlist or directory name to check.

        Returns:
            bool: True if the song or preset is a web radio URL, False otherwise.
        """
        if preset and listname:
        # Check if current queue is a web radio
            oradio_log.error("Invalid parameters: both 'preset' and 'listname' were provided")
            return False

        if preset is None and listname is None:
            current_song = self._execute("currentsong") or {}
            file_uri = current_song.get("file", "")
            return file_uri.startswith(("http://", "https://"))

        if listname is None:

            # Get listname for given preset
            listname = _get_preset_listname(preset, PRESETS_FILE)
            if not listname:
                oradio_log.info("Preset '%s' has no playlist", preset)
                return False

            # Check if listname is a stored playlist
            playlists = self._execute("listplaylists") or []
            playlist_names = [pl.get("playlist") for pl in playlists if "playlist" in pl]
            if listname not in playlist_names:
                oradio_log.debug("'%s' not found in playlists", listname)
                return False

        # Get songs in that playlist
        songs = self._execute("listplaylist", listname) or []
        if not songs:
            oradio_log.debug("Playlist '%s' is empty", listname)
            return False

        # Check first entry for being a URL
        return songs[0].startswith(("http://", "https://"))

    def _get_current_listname(self):
        """
        Return the name of the current playlist or directory.

        Returns:
            str | None: Name of the current playlist or directory, or None if the queue is empty.
        """
        info = self._execute("playlistinfo")
        if info:
            # Return name of last directory
            return os.path.basename(os.path.dirname(info[0]["file"]))
        return None

    def get_directories(self):
        """
        Retrieve available directories from MPD.

        Returns:
            list[str]: Case-insensitive, alphabetically sorted list of directory names.
        """
        # Initialize
        folders = []

        # Get directories
        directories = self._execute("listfiles")

        # Extract valid directory names
        folders = [
            entry["directory"]
            for entry in directories
            if isinstance(entry, dict) and "directory" in entry
        ]

        # Sort case-insensitive alphabetically sorted list
        return sorted(folders, key=str.casefold)

    def get_playlists(self):
        """
        Retrieve available playlists from MPD.

        Returns:
            list[dict]: Case-insensitive sorted list of dicts with keys:
                        'playlist' (str) and 'webradio' (bool).
        """
        # Get playlists form MPD
        playlists = self._execute("listplaylists")

        # Extract valid playlist names
        result = [
            {
                "playlist": p["playlist"],
                "webradio": self.is_webradio(listname=p["playlist"])
            }
            for p in playlists
            if isinstance(p, dict) and p.get("playlist")
        ]

        # Sort case-insensitive alphabetically sorted list
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
        songs = []

        # Check playlists
        for playlist in self._execute("listplaylists"):
            if listname == playlist.get("playlist"):
                details = self._execute("listplaylistinfo", listname)
                songs = [
                    {
                        "file": d.get("file", ""),
                        "artist": d.get("artist", "Unknown artist"),
                        "title": d.get("title", "Unknown title"),
                    }
                    for d in details
                ]
                return songs  # Preserve playlist order

        # Check directories
        for entry in self._execute("listfiles"):
            if entry.get("directory") == listname:
                details = self._execute("lsinfo", listname)
                songs = [
                    {
                        "file": d.get("file", ""),
                        "artist": d.get("artist", "Unknown artist"),
                        "title": d.get("title", "Unknown title"),
                    }
                    for d in details
                ]
                return sorted(songs, key=lambda x: x["artist"].casefold())  # Alphabetical by artist

        # Return empty as list is not a known playlist or directory
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

        def _normalize(text: str):
            """
            Normalize a string for comparison by removing case and diacritics.

            Args:
                text (str): The input string to normalize.

            Returns:
                str: A normalized version of the input string suitable for
                     case- and accent-insensitive comparison.
            """
            text = text.strip().lower()
            text = unicodedata.normalize('NFD', text)
            return ''.join(c for c in text if unicodedata.category(c) != 'Mn')

        # Search artists and titles
        results = self._execute("search", 'artist', pattern) + self._execute("search", 'title', pattern)

        # Compile formatted songs
        songs = []
        for result in results:
            artist = result.get('artist', 'Unknown artist')
            title = result.get('title', 'Unknown title')
            songs.append({
                'file': result['file'],
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
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            presets = json.load(file)
        json_key = preset_key.lower()
        return presets.get(json_key, None)
    except FileNotFoundError:
        oradio_log.error("File not found at %s", filepath)
    except json.JSONDecodeError:
        oradio_log.error("Failed to JSON decode %s", filepath)
    return None

# Create a singleton instance to use in other modules
mpd_client = MPDControl()

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
                            print(f"{idx:>2}. {result}")
                case 10:
                    print("\nPlay directory: to be implemented")
                case 11:
                    print("\nListing playlists:")
                    results = mpd_client.get_playlists()
                    if not results:
                        print(f"{YELLOW}No playlists found{NC}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            webradio_tag = "(webradio)" if result.get("webradio") else ""
                            print(f"{idx:>2}. {result.get('playlist')} {webradio_tag}")
                case 12:
                    print("\nPlay directory: to be implemented")
                case 13:
                    print("\nListing songs")
                    selection = input("Enter playlist or directory: ")
                    results = mpd_client.get_songs(selection)
                    if not results:
                        print(f"No songs found for list {selection}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            print(f"{idx:>3}. {result}")
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
                        print(f"{idx:>2}. {result.get('artist')} - {result.get('title')}")
                case 17:
                    print("\nPlay a song (to be implemented)")
                case 18:
                    selection = input("\nEnter preset number 1, 2 or 3 to check: ")
                    if selection.isdigit() and int(selection) in range(1, 4):
                        if mpd_client.is_webradio(preset=f"Preset{selection}"):
                            print(f"\nPreset{selection} playlist is a web radio\n")
                        else:
                            print(f"\nPreset{selection} playlist is NOT a web radio\n")
                    else:
                        print(f"\n{YELLOW}Invalid preset. Please enter a valid number{NC}\n")
                case 19:
                    if mpd_client.is_webradio():
                        print("\nCurrent playlist is a web radio\n")
                    else:
                        print("\nCurrent playlist is NOT a web radio\n")
                case 20:
                    print("\nExecuting: Update MPD Database\n")
                    mpd_client.update_database()
                case 21:
                    print("\nExecuting: Restart MPD Service\n")
                    result, response = run_shell_script("sudo systemctl restart mpd.service")
                    if not result:
                        print(f"\n{RED}Failed to restart MPD service: {response}${NC}\n")
                    else:
                        print("\nMPD service restarted successfully\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
