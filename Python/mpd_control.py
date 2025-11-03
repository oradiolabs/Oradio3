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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@references:
    https://www.musicpd.org/
    https://python-mpd2.readthedocs.io/

@summary:
    Oradio MPD control module
    - Automatic reconnect if MPD is down or connection drops
    - Retries commands logging error on failure
    Terminology:
    - directory/directories: read-only collection(s) of music files
    - playlist/playlists: collection(s) which can be created, saved, edited and deleted
    - mpdlist/mpdlists: the combination of directories and playlists
    - current: the directory/playlist in the playback queue
"""
from os import path
from time import sleep
# Lock is not needed if MPDControl is correctly used per thread/process; included here as a safeguard against incorrect usage
from threading import Thread, Lock
from unicodedata import normalize, category
# Use MPDConnectionError here because Python's built-in ConnectionError differs from the ConnectionError raised by the mpd2 library
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import load_presets

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, NC,
    USB_MUSIC,
)

##### Local constants ####################
MPD_HOST        = "localhost"
MPD_PORT        = 6600
MPD_RETRIES     = 3
MPD_BACKOFF     = 1         # seconds
MPD_CROSSFADE   = 5         # seconds
LOCK_TIMEOUT    = 5         # seconds
DEFAULT_PRESET  = "Preset1" # For when the Play button is used and no playlist in the queue

class MPDBase:
    """
    Thread-safe base class for interacting with an MPD (Music Player Daemon) server.
    - Automatic connection and reconnection to the MPD server.
    - Retry logic with backoff for commands and connections.
    - Safe execution of MPD commands with optional auto-reconnect.
    - Locking to prevent concurrent access from multiple threads.
    - Logging of commands, connection attempts, and errors.
    """
    def __init__(self, crossfade: int | None) -> None:
        """
        Initialize the MPDBase class and connect to the MPD server.

        Args:
            crossfade (int | None): Optional crossfade value in seconds.
                                    If None, crossfade will not be set.
        """
        self._lock = Lock()
        self._crossfade = crossfade
        self._client = MPDClient()
        self._connect_client()


    def _is_connected(self):
        """Return True if client is connected to MPD, False otherwise."""
        try:
            self._client.ping() # pylint: disable=no-member
            return True
        except (MPDConnectionError, BrokenPipeError, OSError):
            return False

    def _connect_client(self) -> None:
        """
        Establish a connection to MPD with retries and backoff.
        Optionally sets the crossfade value after a successful connection.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            try:
                # Disconnect stale connection if ping fails
                if not self._is_connected():
                    try:
                        self._client.disconnect()
                        oradio_log.debug("Disconnected stale MPD connection before reconnect")
                    except MPDConnectionError:
                        # Already disconnected, safe to ignore
                        pass

                # Connect if not connected
                if not self._is_connected():
                    self._client.connect(MPD_HOST, MPD_PORT)
                    oradio_log.info("Connected to MPD on %s:%s", MPD_HOST, MPD_PORT)
                else:
                    oradio_log.debug("MPD client already connected, skipping connect")

                # Set crossfade if specified
                if self._crossfade is not None:
                    self._execute("crossfade", self._crossfade, allow_reconnect=False)
                    oradio_log.info("MPD crossfade set to %d", self._crossfade)

                return  # Connection successful

            except (MPDConnectionError, BrokenPipeError, OSError) as ex_err:
                # Wait and retry
                oradio_log.warning("Connection attempt %d/%d failed (%s). Retrying...", attempt, MPD_RETRIES, ex_err)

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                # Catch-all for unexpected errors
                oradio_log.exception("Unexpected error during MPD connection attempt %d/%d: %s", attempt, MPD_RETRIES, ex_unexpected)

            # Avoid hammering the MPD server
            sleep(MPD_BACKOFF)

        # All retries exhausted
        oradio_log.error("Failed to connect to MPD after %d attempts", MPD_RETRIES)

    def _execute(self, command: str, *args, allow_reconnect: bool = True, **kwargs) -> object | None:
        """
        Execute an MPD command safely and efficiently.
        - Retries only on actual lost connections.
        - ProtocolErrors are handled intelligently.
        - Supports lock timeout to prevent deadlocks.

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
                # Acquire lock with timeout (therefore not using 'with')
                acquired = self._lock.acquire(timeout=LOCK_TIMEOUT) # pylint: disable=consider-using-with
                if not acquired:
                    oradio_log.warning("Timeout waiting for MPD lock (attempt %d/%d, command '%s')", attempt, MPD_RETRIES, command)
                    # Avoid hammering the MPD server
                    sleep(MPD_BACKOFF)
                    continue

                oradio_log.debug("Executing MPD command '%s' (attempt %d/%d)", command, attempt, MPD_RETRIES)

                # Execute the MPD command
                return function(*args, **kwargs)

            except CommandError as ex_cmd:
                # Handle logical errors that can safely be ignored
                ignored_errors = [
                    "Not playing",
                ]
                msg = str(ex_cmd)
                if any(err in msg for err in ignored_errors):
                    oradio_log.warning("Ignoring logical error: '%s' for command '%s'", msg, command)
                else:
                    # Invalid command
                    oradio_log.error("MPD command '%s' failed: %s", command, ex_cmd)
                return None

            except (MPDConnectionError, ProtocolError, BrokenPipeError, ConnectionResetError) as ex_err:
                oradio_log.warning("MPD connection error '%s' for command '%s'. Retry %d/%d...", ex_err, command, attempt, MPD_RETRIES)
                if allow_reconnect:
                    self._connect_client()
                # Avoid hammering the MPD server
                sleep(MPD_BACKOFF)

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

# -----MPD Control ------------------

class MPDControl(MPDBase):
    """
    Wrapper for an MPD (Music Player Daemon) client.
    Ensures that all MPD commands are executed safely.
    Automatically reconnects if the connection to the MPD server is lost.
    """
    def __init__(self) -> None:
        """Initialize the MPDControl client and connect to the MPD server."""
        # Execute MPDBase __init__ with crossfade
        super().__init__(crossfade=MPD_CROSSFADE)

    def update_database(self) -> None:
        """
        Update the MPD music database in two stages:
        - Update all preset-linked directories first (for faster preset access).
        - Then update the rest of the MPD database.
        This ensures presets remain quickly accessible and up to date
        before performing a full library refresh.
        """
        # Load presets and all available music directories
        presets = load_presets()
        directories = self.get_directories()

        # Stage 1: Update MPD database for each preset's directory (if it exists)
        for preset, mpdlist in presets.items():
            if mpdlist and mpdlist in directories:
                _ = self._execute("update", mpdlist)
                oradio_log.debug("Updating MPD database for preset '%s' (directory: '%s')", preset, mpdlist)
            else:
                oradio_log.debug("Skipping MPD update for preset '%s' (invalid or missing directory: '%s')", preset, mpdlist)

        # Stage 2: Update the rest of the MPD database
        _ = self._execute("update")
        oradio_log.debug("Updating MPD database for all remaining music files")

# -----Playback functions------------

    def play(self, preset: str | None = None) -> None:
        """
        Start or resume playback.
        - If a preset is provided, play the associated playlist.
        - If no preset is provided, resume the current playlist.
        - If no current playlist exists, play the default preset playlist.

        Args:
            preset (str | None): Optional playlist preset to play.
        """
        # Get current playlist info
        current_playlist_info = self._execute("playlistinfo") or []

        # Case 1: validate and use preset if provided
        if preset:
            if not isinstance(preset, str) or not preset.strip():
                oradio_log.error("Invalid preset provided: %r", preset)
                return
            preset = preset.strip()

        # Case 2: no preset, resume current playlist if exists
        elif current_playlist_info:
            _ = self._execute("play")
            oradio_log.debug("Resumed current playlist")
            return

        # Case 3: fallback to default preset if no preset and no current playlist
        else:
            preset = DEFAULT_PRESET
            oradio_log.debug("No current playlist, using default preset '%s'", preset)

        # Clear current queue before loading new playlist
        _ = self._execute("clear")

        # Resolve preset to playlist or directory
        presets = load_presets()
        playlist_name = presets.get(preset.lower())

        # Do nothing if no playlist_name is set
        if not playlist_name:
            oradio_log.warning("Preset '%s' does not resolve to a playlist", preset)
            return

        # Check if playlist exists in MPD playlists
        playlists = self._execute("listplaylists") or []
        playlist_names = [
            name.get("playlist") for name in playlists
            if isinstance(name, dict) and name.get("playlist")
        ]

        directories = self.get_directories()

        if playlist_name in playlist_names:
            # Playlist exists → load sequentially
            _ = self._execute("load", playlist_name)
            _ = self._execute("random", 0)
            _ = self._execute("repeat", 1)
            oradio_log.debug("Loaded playlist '%s'", playlist_name)
        elif playlist_name in directories:
            # Directory → add all songs and shuffle
            _ = self._execute("add", playlist_name)
            _ = self._execute("shuffle")
            _ = self._execute("random", 1)
            _ = self._execute("repeat", 1)
            oradio_log.debug("Added directory '%s' and shuffled", playlist_name)
        else:
            # Neither playlist nor directory found
            oradio_log.warning("Playlist or directory '%s' not found for preset '%s'", playlist_name, preset)
            return

        # Start playback
        _ = self._execute("play")
        oradio_log.debug("Playback started for: %s", playlist_name)

    def play_song(self, song: str) -> None:
        """
        Play a single song immediately without clearing the current queue.
        Inserts the song after the currently playing song and removes it after playback.

        Args:
            song (str): The URI or file path of the song to play.
        """
        if not song or not isinstance(song, str):
            oradio_log.error("Invalid song: %s", song)
            return

        oradio_log.debug("Attempting to play song: %s", song)

        # Determine the current song index
        status = self._execute("status") or {}
        try:
            current_index = int(status.get("song", -1))
        except (ValueError, TypeError):
            current_index = -1

        # Add song to playlist and get its unique MPD song ID
        inserted_song_id = self._execute("addid", song)
        if inserted_song_id is None:
            oradio_log.error("Failed to add song: %s", song)
            return

        # Find the index of the newly inserted song
        playlist = self._execute("playlistinfo") or []
        for idx, sng in enumerate(playlist):
            if int(sng.get("id", -1)) == int(inserted_song_id):
                new_index = idx
                break
        # The for ... else pattern ensures that the else is only executed if the for loop is not broken (i.e., no match is found)
        else:
            new_index = len(playlist) - 1

        # Determine insertion position (after current song)
        target_index = current_index + 1 if current_index >= 0 else 0

        # Move the new song to the target position if necessary
        if new_index != target_index:
            _ = self._execute("move", new_index, target_index)

        # Start playback of the inserted song
        _ = self._execute("play", target_index)
        oradio_log.debug("Started playback at index %d for song id %s", target_index, inserted_song_id)

        # Start background thread to remove the song once finished
        Thread(
            target=self._remove_song_when_finished,
            args=(inserted_song_id,),
            daemon=True
        ).start()
        oradio_log.debug("Monitor removal for song id: %s", inserted_song_id)

    def _remove_song_when_finished(self, inserted_song_id: int | str) -> None:
        """
        Monitor a song and remove it after it finishes.

        Args:
            inserted_song_id (int | str): MPD song ID to monitor and remove.
        """
        try:
            inserted_song_id = int(inserted_song_id)
        except (TypeError, ValueError):
            oradio_log.error("Invalid song ID provided: %s", inserted_song_id)
            return

        oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)

        # Wait until the song finishes or is skipped
        while True:
            sleep(0.5)  # Poll twice per second

            status = self._execute("status") or {}
            try:
                current_song_id = int(status.get("songid", -1))
            except (TypeError, ValueError):
                current_song_id = -1

            # Exit loop if current song changed
            if current_song_id != inserted_song_id:
                break

            # Check elapsed time
            time_str = status.get("time")
            if time_str:
                try:
                    elapsed_str, duration_str = time_str.strip().split(":")
                    elapsed = float(elapsed_str)
                    duration = float(duration_str)
                    if elapsed >= duration - 0.5:
                        break
                except (ValueError, AttributeError) as ex_err:
                    oradio_log.warning("Failed to parse time for song id %s: '%s' (%s)", inserted_song_id, time_str, ex_err)

        # Remove the song from the playlist if still present
        playlist = self._execute("playlistinfo") or []
        for song in playlist:
            if isinstance(song, dict) and int(song.get("id", -1)) == inserted_song_id:
                _ = self._execute("deleteid", inserted_song_id)
                oradio_log.debug("Removed song id %s from playlist", inserted_song_id)
                break
        else:
            oradio_log.debug("Song id %s already removed", inserted_song_id)

    def pause(self) -> None:
        """
        Pause playback if a song is currently playing.
        - If playback is not active, does nothing.
        """
        # Get current MPD status
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        # Ignore if not currently playing
        if state != "play":
            oradio_log.debug("Ignore pause: not currently playing (state=%s)", state)
            return

        # Pause playback
        _ = self._execute("pause")
        oradio_log.debug("Playback paused")

    def next(self) -> None:
        """
        Skip to the next song in the current playlist or directory.
        - If playback is not active, the skip is ignored.
        - If a web radio is currently playing, the skip is ignored.
        - Relies on _execute() to handle expected MPD logical errors.
        """
        # Get current MPD status
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        # Ignore if not currently playing
        if state != "play":
            oradio_log.debug("Ignore next: not currently playing (state=%s)", state)
            return

        # Ignore if webradio is playing
        if self.is_webradio():
            oradio_log.debug("Ignore next: current item is a web radio")
            return

        # Play next song, wrapping around if repeat is enabled
        _ = self._execute("next")
        oradio_log.debug("Skipped to next song")

    def stop(self) -> None:
        """
        Stop playback if a song is currently playing.
        - If playback is not active, does nothing.
        """
        # Get current MPD status
        status = self._execute("status") or {}
        state = status.get("state", "").lower()

        # Ignore if not currently playing
        if state != "play":
            oradio_log.debug("Ignore stop: not currently playing (state=%s)", state)
            return

        # Stop playback
        _ = self._execute("stop")
        oradio_log.debug("Playback stopped")

    def clear(self) -> None:
        """
        Clear the current MPD playlist or playback queue.
        - Removes all songs from the current playlist/queue.
        """
        # Remove all items from the playlist
        _ = self._execute("clear")
        oradio_log.debug("Current playback queue cleared")

    def add(self, playlist: str, song: str | None) -> None:
        """
        Create a playlist if it does not exist, and optionally add a song to it.
        - Validates the playlist name.
        - Creates a new playlist if it does not already exist.
        - Adds the specified song if provided and exists in USB_MUSIC.

        Args:
            playlist (str): Name of the playlist to create or modify.
            song (str | None): Song filename to add. If None, only the playlist is created.
        """
        # Validate playlist name
        if not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        # Remove leading/trailing whitespace
        playlist = playlist.strip()

        # Get existing playlists
        playlists = self._execute("listplaylists") or []
        playlist_names = [
            entry.get("playlist")
            for entry in playlists
            if isinstance(entry, dict) and entry.get("playlist")
        ]

        # Create playlist if it does not exist
        if not playlist in playlist_names:
            oradio_log.debug("Creating playlist '%s'", playlist)
            # MPD requires at least one song to create a playlist, so add and remove a dummy entry
            _ = self._execute("playlistadd", playlist, "https://dummy.mp3")
            _ = self._execute("playlistdelete", playlist, 0)
            oradio_log.debug("Playlist '%s' created", playlist)
        else:
            oradio_log.debug("Playlist '%s' already exists", playlist)

        # Add song if provided
        if song:
            if not isinstance(song, str) or not song.strip():
                oradio_log.error("Invalid song name: %r", song)
                return

            # Remove leading/trailing whitespace and add path
            song = song.strip()
            song_path = path.join(USB_MUSIC, song)

            # Verify song file exists before adding
            if not path.isfile(song_path):
                oradio_log.error("Song file does not exist: %s", song_path)
                return

            # Adding song to playlist
            _ = self._execute("playlistadd", playlist, song)

            # Force MPD to sync its in-memory and on-disk playlist state.
            _ = self._execute("listplaylistinfo", playlist)

            oradio_log.debug("Song '%s' added to playlist '%s'", song, playlist)

    def remove(self, playlist: str, song: str | None) -> None:
        """
        Remove a song from a playlist or delete the entire playlist.
        - If song is None, removes the entire playlist.
        - If song is provided, removes it from the playlist if found.
        - Logs appropriate messages if playlist or song does not exist.

        Args:
            playlist (str): Name of the playlist to modify.
            song (str | None): Song to remove. If None, deletes the entire playlist.
        """
        # Validate playlist name
        if not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        # Remove leading/trailing whitespace
        playlist = playlist.strip()

        # Delete the entire playlist if no song is specified
        if not song:
            oradio_log.debug("Attempting to remove playlist '%s'", playlist)
            playlists = self._execute("listplaylists") or []
            playlist_names = [
                entry.get("playlist")
                for entry in playlists
                if isinstance(entry, dict) and entry.get("playlist")
            ]

            if playlist in playlist_names:
                _ = self._execute("rm", playlist)
                oradio_log.debug("Playlist '%s' removed", playlist)
            else:
                oradio_log.warning("Playlist '%s' does not exist", playlist)
            return

        # Validate and remove a single song from a playlist
        if not isinstance(song, str) or not song.strip():
            oradio_log.error("Invalid song name: %r", song)
            return

        oradio_log.debug("Attempting to remove song '%s' from playlist '%s'", song, playlist)

        # Remove leading/trailing whitespace
        song = song.strip()

        # Get playlist contents
        items = self._execute("listplaylist", playlist) or []

        # Find song index in the playlist (handles dicts or plain strings)
        index = next(
            (
                i for i, entry in enumerate(items)
                if (isinstance(entry, dict) and entry.get("file") == song)
                or (isinstance(entry, str) and entry == song)
            ),
            None
        )

        if index is None:
            oradio_log.warning("Song '%s' not found in playlist '%s'", song, playlist)
            return

        # Remove song by index
        _ = self._execute("playlistdelete", playlist, index)

        # Force MPD to sync its in-memory and on-disk playlist state.
        _ = self._execute("listplaylistinfo", playlist)

        oradio_log.debug("Song '%s' removed from playlist '%s'", song, playlist)

# -----Informative functions---------

    def is_webradio(self, preset: str = None, mpdlist: str = None) -> bool:
        """
        Determine if the current song, a preset, or a playlist corresponds to a web radio URL.
        - Both 'preset' and 'mpdlist' provided → invalid, return False.
        - Neither provided → check the currently playing song.
        - Only 'preset' provided → resolve it to a playlist.
        - Only 'mpdlist' provided → check that playlist.

        Args:
            preset (str): Name of the preset to check. Default is None.
            mpdlist (str): Name of the playlist to check. Default is None.

        Returns:
            bool: True if the song or playlist starts with "http://" or "https://", False otherwise.
        """
        # Initialize result to False by default
        result = False

        # Case: both preset and mpdlist provided → invalid input
        if preset and mpdlist:
            oradio_log.error("Invalid parameters: both 'preset' and 'mpdlist' provided")
            return result

        # Case: neither preset nor mpdlist provided → check currently playing song
        if not preset and not mpdlist:
            current_song = self._execute("currentsong") or {}
            file_uri = current_song.get("file")
            if isinstance(file_uri, str):
                result = file_uri.lower().startswith(("http://", "https://"))
            else:
                oradio_log.debug("Current song missing or invalid file: %r", file_uri)
            return result

        # Case: preset provided → resolve it to a playlist
        if preset:
            presets_map = load_presets()
            mpdlist = presets_map.get(preset.lower())
            if not mpdlist:
                oradio_log.warning("No playlist found for preset: %s", preset)
                return result

        # Verify the playlist exists
        playlists = self._execute("listplaylists") or []
        valid_names = {p.get("playlist") for p in playlists if isinstance(p, dict) and p.get("playlist")}
        if mpdlist not in valid_names:
            oradio_log.debug("mpdlist '%s' not found in playlists", mpdlist)
            return result

        # Get the first song from the playlist
        songs = self._execute("listplaylist", mpdlist) or []
        if not songs:
            oradio_log.debug("Playlist '%s' is empty", mpdlist)
            return result

        first_song = songs[0]
        file_uri = first_song.get("file") if isinstance(first_song, dict) else first_song

        # Check if the first song is a web radio URL
        if isinstance(file_uri, str):
            result = file_uri.lower().startswith(("http://", "https://"))
        else:
            oradio_log.debug("Unexpected song entry type in '%s': %r", mpdlist, file_uri)
        return result

    def get_directories(self) -> list[str]:
        """
        Retrieve available directories from MPD.

        Returns:
            list[str]: Case-insensitive, alphabetically sorted list of directory names.
        """
        # Execute MPD command to list files/directories
        directories = self._execute("listfiles") or []

        # Collect valid directories
        result = []
        for directory in directories:
            # Skip invalid entries that are not dictionaries
            if not isinstance(directory, dict):
                oradio_log.debug("Skipping invalid directory entry: %s", directory)
                continue

            # Extract the 'directory' field and validate it
            name = directory.get("directory")
            if not name or not isinstance(name, str) or not name.strip():
                oradio_log.debug("Skipping empty or invalid directory name: %s", directory)
                continue

            result.append(name.strip())

        # Return a case-insensitive alphabetical sort
        return sorted(result, key=str.casefold)

    def get_playlists(self) -> list[dict]:
        """
        Retrieve available playlists from MPD.

        Returns:
            list[dict]: Case-insensitive sorted list of dicts with keys:
                        'playlist' (str) and 'webradio' (bool).
        """
        # Get the list of playlists from MPD
        playlists = self._execute("listplaylists") or []

        # Collect valid playlists
        result = []
        for playlist in playlists:
            # Skip entries that are not dictionaries
            if not isinstance(playlist, dict):
                oradio_log.debug("Skipping invalid playlist entry: %s", playlist)
                continue

            # Extract the playlist name and skip empty or None names
            name = playlist.get("playlist")
            if not name or not name.strip():
                oradio_log.debug("Skipping empty playlist entry: %s", playlist)
                continue

            # Add the playlist entry dictionary
            result.append({
                "playlist": name,
                "webradio": self.is_webradio(mpdlist=name)
            })

        # Return sorted result alphabetically by playlist name (case-insensitive)
        return sorted(result, key=lambda x: x["playlist"].casefold())

    def get_songs(self, mpdlist: str) -> list[dict[str, str]]:
        """
        Retrieve songs from a playlist or directory in MPD.

        Args:
            mpdlist (str): Name of the playlist or directory.

        Returns:
            List[Dict[str, str]]: List of song dictionaries with keys:
                - 'file' (str): file path
                - 'artist' (str): artist name
                - 'title' (str): song title
        """
        # Helper function
        def _safe(value: str, fallback: str) -> str:
            """Return the value if it is a non-empty string, otherwise return fallback."""
            return value.strip() if isinstance(value, str) and value.strip() else fallback

        # Validate input
        if not mpdlist or not str(mpdlist).strip():
            oradio_log.warning("Cannot get songs for invalid mpdlist '%s'", mpdlist)
            return []

        # Build lookup dictionaries for playlists and directories
        playlists = self._execute("listplaylists") or []
        playlists_lookup = {p.get("playlist"): p for p in playlists if isinstance(p, dict)}

        directories = self._execute("listfiles") or []
        directories_lookup = {d.get("directory"): d for d in directories if isinstance(d, dict)}

        # Determine source type
        if mpdlist in playlists_lookup:
            details = self._execute("listplaylistinfo", mpdlist) or []
            sort_by_artist = False  # preserve playlist order
            source_type = "playlist"
        elif mpdlist in directories_lookup:
            details = self._execute("lsinfo", mpdlist) or []
            sort_by_artist = True   # sort directory songs by artist
            source_type = "directory"
        else:
            oradio_log.debug("mpdlist '%s' not found as playlist or directory", mpdlist)
            return []

        if not details:
            oradio_log.debug("No songs found for %s '%s'", source_type, mpdlist)
            return []

        # Build song list
        songs: list[dict[str, str]] = [
            {
                "file": _safe(d.get("file"), ""),
                "artist": _safe(d.get("artist"), "Unknown artist"),
                "title": _safe(d.get("title"), "Unknown title"),
            }
            for d in details
            if isinstance(d, dict)
        ]

        # Sort if required
        if sort_by_artist:
            songs.sort(key=lambda x: x["artist"].casefold())

        # Return songs found
        return songs

    def search(self, pattern: str) -> list[dict[str, str]]:
        """
        Search for songs by artist or title, removing duplicates.

        Args:
            pattern (str): Search string to match against artist or title.

        Returns:
            list[dict]: Unique songs sorted by normalized artist and title. Each dict has keys:
                        'file', 'artist', 'title', 'normalized_artist', 'normalized_title'.
        """
        # Helper function
        def _normalize(text: str) -> str:
            """Normalize a string for comparison by removing case and diacritics."""
            if not isinstance(text, str):
                return ""
            text = text.strip().lower()
            text = normalize('NFD', text)
            return ''.join(c for c in text if category(c) != 'Mn')

        # Return empty list if the pattern is empty, not a string, or only whitespace
        if not pattern or not isinstance(pattern, str) or not pattern.strip():
            oradio_log.debug("Empty or invalid search pattern: %s", pattern)
            return []

        # Remove leading/trailing whitespace from the search pattern
        pattern = pattern.strip()

        # Execute searches for both 'artist' and 'title' fields and collect results
        results = [
            result
            for field in ('artist', 'title')
            for result in (self._execute("search", field, pattern) or [])
            # Keep only results that are dictionaries and have a valid 'file' field
            if isinstance(result, dict) and isinstance(result.get('file'), str)
        ]

        # Compile formatted songs with normalized fields
        songs = [
            {
                'file': result['file'],
                'artist': result.get('artist', "Unknown artist"),
                'normalized_artist': _normalize(result.get('artist', "Unknown artist")),
                'title': result.get('title', "Unknown title"),
                'normalized_title': _normalize(result.get('title', "Unknown title")),
            }
            for result in results
        ]

        # Remove duplicates based on normalized artist and title
        seen = set()
        unique_songs = [
            song for song in songs
            if (song['normalized_artist'], song['normalized_title']) not in seen
            and not seen.add((song['normalized_artist'], song['normalized_title']))
        ]

        # Sort by normalized artist and title for case- and accent-insensitive order
        return sorted(unique_songs, key=lambda x: (x['normalized_artist'], x['normalized_title']))

# Entry point for stand-alone operation
if __name__ == '__main__':

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
            "10-List playlists\n"
            "11-List playlist songs\n"
            "12-Add (song to) a playlist\n"
            "13-Remove (song from) a playlist\n"
            "14-Search song(s)\n"
            "15-Check if preset is web radio\n"
            "16-Check if current song is web radio\n"
            "17-Update Database\n"
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
                    print("\nListing playlists:")
                    results = mpd_client.get_playlists()
                    if not results:
                        print(f"{YELLOW}No playlists found{NC}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            webradio_tag = "(webradio)" if result.get("webradio") else ""
                            print(f"{GREEN}{idx:>2}. {result.get('playlist')} {webradio_tag}{NC}")
                case 11:
                    print("\nListing songs")
                    selection = input("Enter playlist or directory: ")
                    results = mpd_client.get_songs(selection)
                    if not results:
                        print(f"No songs found for list {selection}")
                    else:
                        for idx, result in enumerate(results, start=1):
                            print(f"{GREEN}{idx:>3}. {result}{NC}")
                case 12:
                    print("\nAdd (song to) a playlist")
                    name = input("Enter playlist name: ")
                    song = input("Enter playlist song (playlist/songfile): ")
                    mpd_client.add(name, song)
                case 13:
                    print("\nRemove (song from) a playlist")
                    name = input("Enter playlist name: ")
                    song = input("Enter playlist song (playlist/songfile): ")
                    mpd_client.remove(name, song)
                case 14:
                    print("\nSearch song(s)")
                    pattern = input("Enter search pattern: ")
                    results = mpd_client.search(pattern)
                    if results:
                        for idx, result in enumerate(results, start=1):
                            print(f"{GREEN}{idx:>4}. {result.get('artist')} - {result.get('title')}{NC}")
                    else:
                        print(f"\n{YELLOW}Search for pattern '{pattern}' did not return any songs{NC}")
                case 15:
                    selection = input("\nEnter preset number 1, 2 or 3 to check: ")
                    if selection.isdigit() and int(selection) in range(1, 4):
                        if mpd_client.is_webradio(preset=f"Preset{selection}"):
                            print(f"\n{GREEN}Preset{selection} playlist is a web radio{NC}\n")
                        else:
                            print(f"\n{GREEN}Preset{selection} playlist is NOT a web radio{NC}\n")
                    else:
                        print(f"\n{YELLOW}Invalid preset. Please enter a valid number{NC}\n")
                case 16:
                    if mpd_client.is_webradio():
                        print(f"\n{GREEN}Current playlist is a web radio{NC}\n")
                    else:
                        print(f"\n{GREEN}Current playlist is NOT a web radio{NC}\n")
                case 17:
                    print("\nExecuting: Update MPD Database\n")
                    mpd_client.update_database()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
