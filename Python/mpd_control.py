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
    - Retries commands up to retries times before raising a MPDConnectionError
"""
import os
import time
import json
import threading
import unicodedata
from threading import Lock
from mpd import MPDClient, CommandError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    RED, YELLOW, NC,
    PRESETS_FILE,
    USB_MUSIC,
)

##### Local constants ####################
MPD_HOST       = "localhost"
MPD_PORT       = 6600
MPD_RETRIES    = 3
MPD_DELAY      = 1          # seconds
MPD_CROSSFADE  = 5          # seconds
DEFAULT_PRESET = "Preset1"  # For when the Play button is used and no playlist in the queue

class MPDControl:
    """
    Thread-safe wrapper for an MPD (Music Player Daemon) client
    This class ensures that all MPD commands are executed safely in a multi-threaded environment
    It also automatically reconnects if the connection to the MPD server is lost
    Attributes:
        _host (str): MPD server hostname
        _port (int): MPD server port
        _retry_delay (float): Delay in seconds between reconnect attempts
        _crossfade (int): Crossfade time in seconds
        _lock (threading.Lock): Lock to ensure thread-safe access
        _client (MPDClient): Internal MPD client instance
    """
    def __init__(self, host=MPD_HOST, port=MPD_PORT, retry_delay=MPD_DELAY, crossfade=MPD_CROSSFADE):
        """
        Initialize the MPDControl client and connect to the MPD server
        Args:
            host (str): MPD server hostname
            port (int): MPD server port
            retry_delay (float): Delay between reconnect attempts in seconds
            crossfade (int): Number of seconds for crossfade between tracks
        """
        self._host = host
        self._port = port
        self._retry_delay = retry_delay
        self._crossfade = crossfade
        self._lock = Lock()
        self._client = MPDClient()
        self._connected = False

        # Connect to MPD service
        with self._lock:
            self._connect_client()

    def _connect_client(self, retries=MPD_RETRIES):
        """
        Establish a connection to the MPD (Music Player Daemon) server and configure crossfade
        Args:
            retries (int, optional): Maximum number of attempts to connect before giving up
        """
        for attempt in range(1, retries + 1):
            try:
                oradio_log.info("Connecting to MPD service on %s:%s", self._host, self._port)
                self._client.connect(self._host, self._port)
                oradio_log.info("Setting crossfade to %d seconds", self._crossfade)
                try:
                    self._client.crossfade(self._crossfade) # pylint: disable=no-member
                except CommandError as ex_err:
                    oradio_log.warning("MPD does not support crossfade: %s", ex_err)
                # Connected to MPD service
                self._connected = True
                return
            except MPDConnectionError as ex_err:
                oradio_log.error("MPD connection failed (%s). Retry %d/%d", ex_err, attempt, retries)
                self._connected = False
                time.sleep(self._retry_delay)
        oradio_log.error("Failed to connect to MPD after %s attempts", retries)

    def _execute(self, command_name, *args, retries=MPD_RETRIES, **kwargs):
        """
        Execute an MPD command in a thread-safe manner with automatic reconnect
        Args:
            command_name (str): Name of the MPD command to execute
            *args: Positional arguments to pass to the MPD command
            retries (int): Number of reconnect attempts if the connection fails
            **kwargs: Keyword arguments to pass to the MPD command
        Returns:
            The result of the MPD command
        """
        for attempt in range(1, retries + 1):
            with self._lock:
                try:
                    func = getattr(self._client, command_name)
                    return func(*args, **kwargs)
                except (MPDConnectionError, BrokenPipeError) as ex_err:
                    # NOTE: Normally MPD stays connected indefinitely (timeout ~1 year), but we still handle this defensively
                    oradio_log.warning("MPD connection lost (%s). Retry connecting %d/%d", ex_err, attempt, retries)
                    self._connect_client()
                except CommandError as ex_err:
                    # MPD command-specific error
                    oradio_log.error("MPD command error: %s", ex_err)
                    return None
                except AttributeError:
                    oradio_log.error("Invalid MPD command: '%s'", command_name)
                    return None
        oradio_log.error("Failed to execute '%s' after %d retries", command_name, retries)
        return None

    def is_connected(self):
        """Return True if the client is currently connected"""
        return self._connected

    # Convenience methods
    def play(self, preset=None):
        """
        Start or resume playback
        Args:
            preset (str): The playlist of the preset to play
        """
        # Determine what to play
        if preset is None:
            if self._execute("playlistinfo"):
                # Play current playlist
                self._execute("play")
                oradio_log.debug("Play current playlist")
                return
            # Play default preset playlist
            oradio_log.debug("No current playlist, using default preset %s", DEFAULT_PRESET)
            preset = DEFAULT_PRESET

        # Clear the current playlist/queue
        self._execute("clear")

        # Get playlist linked to preset
        playlist_name = _get_preset_listname(preset, PRESETS_FILE)
        if not playlist_name:
            oradio_log.debug("No playlist found for preset: %s", preset)
            return

        # Get list of names of the playlist in the playlist directory
        stored_playlists = self._execute("listplaylists")
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

    def play_song(self, song):
        """
        Play song once immediately without clearing the current queue
        Once the song finishes it is removed from the queue
        Args:
            song (str): The URI or path of the song to play
        """
        oradio_log.debug("Attempting to play song: %s", song)
        status = self._execute("status")
        current_index = int(status.get("song", -1))

        # Add the new song to the playlist and get its unique song ID
        inserted_song_id = self._execute("addid", song)
        if inserted_song_id is None:
            oradio_log.error("Failed to add song: %s", song)
            return

        playlist = self._execute("playlistinfo")
        new_index = len(playlist) - 1  # Index of the newly added song
        # Determine where to insert: right after the current song, or at start if none
        target_index = current_index + 1 if current_index >= 0 else 0

        # Move the new song to the target position if needed
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
        Monitor a specific song in the playlist and remove it after it finishes
        Args:
            inserted_song_id (int): The MPD song ID of the song to monitor and remove
        """
        oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)

        # Poll MPD status periodically until the song finishes or is skipped
        while True:
            time.sleep(0.5)  # Poll twice per second for responsiveness

            status = self._execute("status")
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
        playlist = self._execute("playlistinfo")
        if any(int(song.get("id", -1)) == inserted_song_id for song in playlist):
            self._execute("deleteid", inserted_song_id)
            oradio_log.debug("Deleted song id %s", inserted_song_id)
        else:
            oradio_log.debug("Song id %s already removed from playlist", inserted_song_id)

    def pause(self):
        """Pause playback if playing"""
        status = self._execute("status")

        if status.get("state") != "play":
            oradio_log.debug("Ignore pause because not playing")
            return

        # Pause play
        self._execute("pause")
        oradio_log.debug("Playback paused")

    def next(self):
        """Skip to the next song in the current playlist or directory if playing and not a web radio"""
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
        """Stop playback if playing"""
        status = self._execute("status")

        if status.get("state") != "play":
            oradio_log.debug("Ignore stop because not playing")
            return

        # Stop play
        self._execute("stop")
        oradio_log.debug("Playback stopped")

    def clear(self):
        """Clear the current queue"""
        self._execute("clear")
        oradio_log.debug("Current playback queue cleared")

    def add(self, playlist, song):
        """
        Create a playlist if it does not exist, and optionally add a song to it
        Args:
            playlist (str): Name of the playlist to create or modify
            song (str | None): Song URL or file to add. If None, only create the playlist
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
        Remove a song from a playlist, or delete the entire playlist
        Args:
            playlist (str): Name of the playlist to modify
            song (str | None): Song to remove. If None, deletes the entire playlist
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
        Update the MPD database with progress logging
        It waits until the update is complete
        Args:
            progress_interval (int, optional): Time in seconds between progress log messages
        Returns:
            None
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
        Check if a prest or list is a web radio URL
        Rules:
            - If both `preset` and `listname` are None → check currently playing song
            - If only `preset` is set → check that preset list
            - If only `listname` is set → check that list
            - If both are set → invalid input → log error and return False
        Args:
            preset (str, optional): Preset name to check
            listname (str, optional): list name to check
        Returns:
            bool: True if the song or preset is a web radio (URL starts with 'http://' or 'https://'), False otherwise
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
        Return the name of the current playlist or directory in MPD
        Returns:
            str | None: Name of the current playlist or directory, or None if the queue is empty
        """
        info = self._execute("playlistinfo")
        if info:
            # Return name of last directory
            return os.path.basename(os.path.dirname(info[0]["file"]))
        return None

    def get_directories(self):
        """
        Get available directories
        Returns a case-insensitive sorted list of directory names
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
        Get available playlists
        Determines if each playlist is a web radio or not
        Returns a case-insensitive, alphabetically sorted list of dictionaries with keys 'playlist' and 'webradio'
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
        List songs from a playlist or a directory
        If `listname` matches a known playlist, returns the songs in playlist order
        If it matches a directory, returns songs sorted alphabetically by artist
        If not found, returns an empty list
        Args:
            listname (str): Name of the playlist or directory
        Returns:
            list[dict]: List of song dictionaries with keys:
                        - 'file' (str)
                        - 'artist' (str, defaults to 'Unknown artist')
                        - 'title' (str, defaults to 'Unknown title')
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
        Search for songs matching the given pattern in either artist or title
        - Removes duplicate songs (case- and accent-insensitive)
        - Returns a list of dictionaries: [{'file': ..., 'artist': ..., 'title': ...}, ...]
        - Results are sorted alphabetically by artist, then title
        Args:
            pattern (str): The search string to match against artist or title
        Returns:
            list[dict]: List of unique song dictionaries with 'file', 'artist', and 'title' keys
        """

        def _normalize(text: str):
            """Lowercase, trim, and remove diacritics from a string for normalized comparison"""
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
    Retrieve the playlist name associated with a given preset key from a JSON file
    Args:
        preset_key (str): The preset key for case insensitive look up
        filepath (str): Path to the JSON file containing preset mappings
    Returns:
        str | None: The playlist name if found, otherwise None
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
