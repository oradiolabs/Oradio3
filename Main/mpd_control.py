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
from threading import Thread
from unicodedata import normalize, category

##### Oradio modules ######################################
from log_service import oradio_log
from utilities import load_presets
from mpd_service import MPDService

##### GLOBAL constants ####################################
from constants import USB_MUSIC

##### LOCAL constants #####################################
MPD_CROSSFADE  = 5          # seconds
DEFAULT_PRESET = "Preset1"  # Used when Play is pressed and the queue is empty

# Sentinel URI used solely to satisfy MPD's requirement that a playlist must
# contain at least one entry before it can be saved. Immediately removed after
# the playlist is created. Also used by _sanitize_playlists() to clean up any
# entries left behind by an interrupted create sequence.
_PLAYLIST_DUMMY_URI = "https://dummy.mp3"

class MPDControl(MPDService):
    """
    Wrapper for an MPD (Music Player Daemon) client.
    Ensures that all MPD commands are executed safely.
    Automatically reconnects if the connection to the MPD server is lost.
    """
    def __init__(self) -> None:
        """
        Initialise the MPDControl client, connect to the MPD server, and
        sanitise any playlists left dirty by a previously interrupted run.
        """
        # Initialise the parent MPDService with crossfade.
        super().__init__(crossfade=MPD_CROSSFADE)

        # Remove any dummy entries left by a prior interrupted playlist creation.
        self._sanitize_playlists()

    def update_database(self) -> None:
        """
        Update the MPD music database in two stages.

        Stage 1: Update each preset-linked directory first for faster preset
        access. Stage 2: Update the full MPD library. This ensures presets
        remain quickly accessible before the broader refresh completes.
        """
        presets     = load_presets()
        directories = self.get_directories()

        # Stage 1: Update MPD database for each preset's directory (if it exists).
        for preset, mpdlist in presets.items():
            if mpdlist and mpdlist in directories:
                _ = self._execute("update", mpdlist)
                oradio_log.debug("Updating MPD database for preset '%s' (directory: '%s')", preset, mpdlist)
            else:
                oradio_log.debug("Skipping MPD update for preset '%s' (invalid or missing directory: '%s')", preset, mpdlist)

        # Stage 2: Update the rest of the MPD database.
        _ = self._execute("update")
        oradio_log.debug("Updating MPD database for all remaining music files")

##### Helpers #############################################

    def _create_empty_playlist(self, playlist: str) -> None:
        """
        Create an empty MPD playlist.

        MPD requires at least one entry before a playlist can be saved, so a
        dummy URI is added and immediately removed. The playlist contents are
        verified afterwards; any surviving dummy entries are removed and logged
        as warnings to guard against a failed delete step.

        Args:
            playlist: Name of the playlist to create.
        """
        self._execute("playlistadd", playlist, _PLAYLIST_DUMMY_URI)
        self._execute("playlistdelete", playlist, 0)

        # Verify the playlist is clean; remove any surviving dummy entries.
        # Iterate in reverse so index-based deletion stays valid.
        contents = self._execute("listplaylist", playlist) or []
        for i, entry in reversed(list(enumerate(contents))):
            uri = entry.get("file") if isinstance(entry, dict) else entry
            if uri == _PLAYLIST_DUMMY_URI:
                self._execute("playlistdelete", playlist, i)
                oradio_log.warning(
                    "Removed stale dummy entry at index %d from playlist '%s'", i, playlist,
                )

    def _sanitize_playlists(self) -> None:
        """
        Remove any dummy entries left in playlists by a previous interrupted run.

        Called once during __init__ to ensure no playlist permanently contains
        the sentinel URI from a prior failed create sequence.
        """
        for entry in self._execute("listplaylists") or []:
            name = entry.get("playlist") if isinstance(entry, dict) else None
            if not name:
                continue
            contents = self._execute("listplaylist", name) or []
            for i, item in reversed(list(enumerate(contents))):
                uri = item.get("file") if isinstance(item, dict) else item
                if uri == _PLAYLIST_DUMMY_URI:
                    self._execute("playlistdelete", name, i)
                    oradio_log.warning(
                        "Startup cleanup: removed stale dummy entry from playlist '%s'", name,
                    )

    def _current_uri(self) -> str | None:
        """Return the URI of the currently playing song."""
        current_song = self._execute("currentsong") or {}
        file_uri = current_song.get("file")

        if isinstance(file_uri, str):
            return file_uri

        oradio_log.debug("Current song missing or invalid file: %r", file_uri)
        return None

    def _playlist_first_uri(self, mpdlist: str) -> str | None:
        """
        Return the URI of the first entry in an MPD playlist.

        Args:
            mpdlist: Name of the playlist to check.
        """
        playlists = self._execute("listplaylists") or []
        print(playlists)
        valid_names = {
            p["playlist"]
            for p in playlists
            if isinstance(p, dict) and p.get("playlist")
        }
        print(valid_names)

        if mpdlist not in valid_names:
            oradio_log.debug("mpdlist '%s' not found in playlists", mpdlist)
            return None

        songs = self._execute("listplaylist", mpdlist) or []
        if not songs:
            oradio_log.debug("Playlist '%s' is empty", mpdlist)
            return None

        first_song = songs[0]
        file_uri = (
            first_song.get("file")
            if isinstance(first_song, dict)
            else first_song
        )

        if isinstance(file_uri, str):
            return file_uri

        oradio_log.debug(
            "Unexpected song entry type in '%s': %r",
            mpdlist,
            file_uri,
        )
        return None

##### Playback functions ##################################

    def play(self, preset: str | None = None) -> None:
        """
        Start or resume playback.

        Behaviour when no preset is given and the queue is filled:
            - Already playing → do nothing.
            - Paused → resume playback.
            - Stopped, queue is a playlist → play from the first song.
            - Stopped, queue is a directory → shuffle and play a random song.

        Behaviour when the queue is empty (preset used as fallback):
            - If preset is None, DEFAULT_PRESET is used.
            - Preset resolves to nothing → do nothing.
            - Preset resolves to a playlist → load and play from the first song.
            - Preset resolves to a directory → add all songs, shuffle, and play.

        Args:
            preset: Optional preset name to load and play.
        """
        songs_in_queue = self._execute("playlistinfo") or []

        # No preset and queue filled: resume current playlist.
        if preset is None and songs_in_queue:
            status = self._execute("status") or {}
            state  = status.get("state", "").lower()

            if state == "play":
                oradio_log.debug("Playing current playlist")
                return

            if state == "pause":
                oradio_log.debug("Resuming current playlist")
                _ = self._execute("play")
                return

            playlist = status.get("lastloadedplaylist")

            if state == "stop" and playlist:
                oradio_log.debug("Play first song of playlist '%s'", playlist)
                _ = self._execute("play", 0)
            else:
                # songs_in_queue is not empty, so safe to read the first entry.
                parent_dir = path.dirname(songs_in_queue[0].get("file"))
                directory  = path.basename(parent_dir)
                oradio_log.debug("Play random song of directory '%s'", directory)
                _ = self._execute("shuffle")
                _ = self._execute("play")

            return

        # Validate and use preset if provided.
        if preset:
            if not isinstance(preset, str) or not preset.strip():
                oradio_log.error("Invalid preset provided: %r", preset)
                return
            preset = preset.strip()
        else:
            preset = DEFAULT_PRESET
            oradio_log.debug("No current playlist, using default preset '%s'", preset)

        _ = self._execute("clear")

        presets  = load_presets()
        listname = presets.get(preset.lower())

        if not listname:
            oradio_log.warning("Preset '%s' does not resolve to a playlist", preset)
            return

        playlists = self._execute("listplaylists") or []
        playlist_names = [
            name.get("playlist") for name in playlists
            if isinstance(name, dict) and name.get("playlist")
        ]

        directories = self.get_directories()

        if listname in playlist_names:
            _ = self._execute("load", listname)
            oradio_log.debug("Loaded playlist '%s'", listname)
        elif listname in directories:
            _ = self._execute("add", listname)
            _ = self._execute("shuffle")
            oradio_log.debug("Added directory '%s' and shuffled", listname)
        else:
            oradio_log.warning("Playlist or directory '%s' not found for preset '%s'", listname, preset)
            return

        # Disable MPD's own random mode; shuffle was applied at load time for directories.
        _ = self._execute("random", 0)

        # Never stop playing music.
        _ = self._execute("repeat", 1)

        _ = self._execute("play")
        oradio_log.debug("Playback started for: %s", listname)

    def play_song(self, song: str) -> None:
        """
        Play a single song immediately without clearing the current queue.
        Inserts the song after the currently playing song and removes it after playback.

        Args:
            song: The URI or file path of the song to play.
        """
        if not song or not isinstance(song, str):
            oradio_log.error("Invalid song: %s", song)
            return

        oradio_log.debug("Attempting to play song: %s", song)

        status = self._execute("status") or {}
        try:
            current_index = int(status.get("song", -1))
        except (ValueError, TypeError):
            current_index = -1

        inserted_song_id = self._execute("addid", song)
        if inserted_song_id is None:
            oradio_log.error("Failed to add song: %s", song)
            return

        # Find the queue index of the newly inserted song by matching its MPD song ID.
        playlist = self._execute("playlistinfo") or []
        new_index = None
        for idx, sng in enumerate(playlist):
            if int(sng.get("id", -1)) == int(inserted_song_id):
                new_index = idx
                break

        if new_index is None:
            oradio_log.error("Inserted song id %s not found in playlist", inserted_song_id)
            return

        target_index = current_index + 1 if current_index >= 0 else 0

        if new_index != target_index:
            _ = self._execute("move", new_index, target_index)

        _ = self._execute("play", target_index)
        oradio_log.debug("Started playback at index %d for song id %s", target_index, inserted_song_id)

        Thread(
            target=self._remove_song_when_finished,
            args=(inserted_song_id,),
            daemon=True
        ).start()
        oradio_log.debug("Monitor removal for song id: %s", inserted_song_id)

    def _remove_song_when_finished(self, inserted_song_id: int | str) -> None:
        """
        Monitor a song and remove it from the queue after it finishes.

        Args:
            inserted_song_id: MPD song ID to monitor and remove.
        """
        try:
            inserted_song_id = int(inserted_song_id)
        except (TypeError, ValueError):
            oradio_log.error("Invalid song ID provided: %s", inserted_song_id)
            return

        oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)

        while True:
            sleep(0.5)  # Poll twice per second

            status = self._execute("status") or {}
            try:
                current_song_id = int(status.get("songid", -1))
            except (TypeError, ValueError):
                current_song_id = -1

            if current_song_id != inserted_song_id:
                break

            time_str = status.get("time")
            if time_str:
                try:
                    elapsed_str, duration_str = time_str.strip().split(":")
                    elapsed  = float(elapsed_str)
                    duration = float(duration_str)
                    if elapsed >= duration - 0.5:
                        break
                except (ValueError, AttributeError) as ex_err:
                    # Transient parse failure; will retry on next poll cycle.
                    oradio_log.debug(
                        "Transient time parse failure for song id %s: '%s' (%s)",
                        inserted_song_id, time_str, ex_err,
                    )

        # Remove the song from the queue if still present.
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
        Does nothing if playback is not active.
        """
        status = self._execute("status") or {}
        state  = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore pause: not currently playing (state=%s)", state)
            return

        _ = self._execute("pause")
        oradio_log.debug("Playback paused")

    def next(self) -> None:
        """
        Skip to the next song in the current playlist or directory.

        Does nothing if playback is not active or a web radio is playing.
        Relies on _execute() to handle expected MPD logical errors.
        """
        status = self._execute("status") or {}
        state  = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore next: not currently playing (state=%s)", state)
            return

        if self.is_webradio():
            oradio_log.debug("Ignore next: current item is a web radio")
            return

        _ = self._execute("next")
        oradio_log.debug("Skipped to next song")

    def stop(self) -> None:
        """
        Stop playback if a song is currently playing.
        Does nothing if playback is not active.
        """
        status = self._execute("status") or {}
        state  = status.get("state", "").lower()

        if state != "play":
            oradio_log.debug("Ignore stop: not currently playing (state=%s)", state)
            return

        _ = self._execute("stop")
        oradio_log.debug("Playback stopped")

    def clear(self) -> None:
        """
        Clear the current MPD playlist or playback queue.
        Removes all songs from the current playlist/queue.
        """
        _ = self._execute("clear")
        oradio_log.debug("Current playback queue cleared")

    def add(self, playlist: str, song: str | None) -> None:
        """
        Create a playlist if it does not exist, and optionally add a song to it.

        Rejects empty, non-string, or whitespace-only playlist names.
        Creates a new playlist if it does not already exist.
        Adds the specified song if provided and the file exists in USB_MUSIC.

        Args:
            playlist: Name of the playlist to create or modify.
            song: Song filename to add. If None, only the playlist is created.
        """
        if not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        playlist = playlist.strip()

        playlists = self._execute("listplaylists") or []
        playlist_names = [
            entry.get("playlist")
            for entry in playlists
            if isinstance(entry, dict) and entry.get("playlist")
        ]

        if playlist not in playlist_names:
            oradio_log.debug("Creating playlist '%s'", playlist)
            self._create_empty_playlist(playlist)
            oradio_log.debug("Playlist '%s' created", playlist)
        else:
            oradio_log.debug("Playlist '%s' already exists", playlist)

        if song:
            if not isinstance(song, str) or not song.strip():
                oradio_log.error("Invalid song name: %r", song)
                return

            song      = song.strip()
            song_path = path.join(USB_MUSIC, song)

            if not path.isfile(song_path):
                oradio_log.error("Song file does not exist: %s", song_path)
                return

            _ = self._execute("playlistadd", playlist, song)

            # Force MPD to sync its in-memory and on-disk playlist state.
            _ = self._execute("listplaylistinfo", playlist)

            oradio_log.debug("Song '%s' added to playlist '%s'", song, playlist)

    def remove(self, playlist: str, song: str | None) -> None:
        """
        Remove a song from a playlist or delete the entire playlist.

        If song is None, removes the entire playlist.
        If song is provided, removes it from the playlist if found.
        Logs appropriate messages if the playlist or song does not exist.

        Args:
            playlist: Name of the playlist to modify.
            song: Song to remove. If None, deletes the entire playlist.
        """
        if not isinstance(playlist, str) or not playlist.strip():
            oradio_log.error("Playlist name cannot be empty or invalid: %s", playlist)
            return

        playlist = playlist.strip()

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

        if not isinstance(song, str) or not song.strip():
            oradio_log.error("Invalid song name: %r", song)
            return

        oradio_log.debug("Attempting to remove song '%s' from playlist '%s'", song, playlist)

        song  = song.strip()
        items = self._execute("listplaylist", playlist) or []

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

        _ = self._execute("playlistdelete", playlist, index)

        # Force MPD to sync its in-memory and on-disk playlist state.
        _ = self._execute("listplaylistinfo", playlist)

        oradio_log.debug("Song '%s' removed from playlist '%s'", song, playlist)

##### Informative functions ###############################

    def is_webradio(self, preset: str = None, mpdlist: str = None) -> bool:
        """
        Determine if the current song, a preset, or a playlist is a web radio stream.

        Exactly one of the following cases applies:
          - Neither preset nor mpdlist: check the currently playing song.
          - preset: resolve it to a playlist and check that playlist.
          - mpdlist: check that playlist directly.

        Args:
            preset: Name of the preset to check.
            mpdlist: Name of the playlist to check.

        Returns:
            True if the URI starts with "http://" or "https://".
        """
        if preset and mpdlist:
            oradio_log.error(
                "Invalid parameters: both 'preset' and 'mpdlist' provided"
            )
            return False

        if preset:
            presets_map = load_presets()
            mpdlist = presets_map.get(preset.lower())
            if not mpdlist:
                oradio_log.warning("No playlist found for preset: %s", preset)
                return False

        file_uri = (
            self._current_uri()
            if mpdlist is None
            else self._playlist_first_uri(mpdlist)
        )

        return (
            isinstance(file_uri, str)
            and file_uri.lower().startswith(("http://", "https://"))
        )

    def get_directories(self) -> list[str]:
        """
        Retrieve available directories from MPD.

        Returns:
            list[str]: Case-insensitive alphabetically sorted list of directory names.
        """
        directories = self._execute("listfiles") or []

        result = []
        for directory in directories:
            if not isinstance(directory, dict):
                oradio_log.debug("Skipping invalid directory entry: %s", directory)
                continue

            name = directory.get("directory")
            if not name or not isinstance(name, str) or not name.strip():
                oradio_log.debug("Skipping empty or invalid directory name: %s", directory)
                continue

            result.append(name.strip())

        return sorted(result, key=str.casefold)

    def get_playlists(self) -> list[dict]:
        """
        Retrieve available playlists from MPD.

        Returns:
            list[dict]: Case-insensitive sorted list of dicts with keys:
                        'playlist' (str) and 'webradio' (bool).
        """
        playlists = self._execute("listplaylists") or []

        result = []
        for playlist in playlists:
            if not isinstance(playlist, dict):
                oradio_log.debug("Skipping invalid playlist entry: %s", playlist)
                continue

            name = playlist.get("playlist")
            if not name or not name.strip():
                oradio_log.debug("Skipping empty playlist entry: %s", playlist)
                continue

            result.append({
                "playlist": name,
                "webradio": self.is_webradio(mpdlist=name)
            })

        return sorted(result, key=lambda x: x["playlist"].casefold())

    def get_songs(self, mpdlist: str) -> list[dict[str, str]]:
        """
        Retrieve songs from a playlist or directory in MPD.

        Args:
            mpdlist (str): Name of the playlist or directory.

        Returns:
            list[dict[str, str]]: List of song dicts, each with keys:
                'file' (str), 'artist' (str), 'title' (str).
                Playlist songs preserve their stored order; directory songs
                are sorted by artist name (case-insensitive).
        """
        def _safe(value: str, fallback: str) -> str:
            """Return value if it is a non-empty string, otherwise return fallback."""
            return value.strip() if isinstance(value, str) and value.strip() else fallback

        if not mpdlist or not str(mpdlist).strip():
            oradio_log.warning("Cannot get songs for invalid mpdlist '%s'", mpdlist)
            return []

        playlists        = self._execute("listplaylists") or []
        playlists_lookup = {p.get("playlist"): p for p in playlists if isinstance(p, dict)}

        directories        = self._execute("listfiles") or []
        directories_lookup = {d.get("directory"): d for d in directories if isinstance(d, dict)}

        if mpdlist in playlists_lookup:
            details       = self._execute("listplaylistinfo", mpdlist) or []
            sort_by_artist = False  # playlists have a user-defined order; preserve it
            source_type    = "playlist"
        elif mpdlist in directories_lookup:
            details        = self._execute("lsinfo", mpdlist) or []
            sort_by_artist = True   # directory songs have no inherent order; sort by artist
            source_type    = "directory"
        else:
            oradio_log.debug("mpdlist '%s' not found as playlist or directory", mpdlist)
            return []

        if not details:
            oradio_log.debug("No songs found for %s '%s'", source_type, mpdlist)
            return []

        songs: list[dict[str, str]] = [
            {
                "file":   _safe(d.get("file"),   ""),
                "artist": _safe(d.get("artist"), "Unknown artist"),
                "title":  _safe(d.get("title"),  "Unknown title"),
            }
            for d in details
            if isinstance(d, dict)
        ]

        if sort_by_artist:
            songs.sort(key=lambda x: x["artist"].casefold())

        return songs

    def search(self, pattern: str) -> list[dict[str, str]]:
        """
        Search for songs by artist or title, removing duplicates.

        Args:
            pattern (str): Search string to match against artist or title.

        Returns:
            list[dict[str, str]]: Unique songs sorted by normalised artist then title.
                Each dict has keys: 'file', 'artist', 'title',
                'normalized_artist', 'normalized_title'.
        """
        def _normalize(text: str) -> str:
            """Normalise a string for comparison by removing case and diacritics."""
            if not isinstance(text, str):
                return ""
            text = text.strip().lower()
            text = normalize('NFD', text)
            return ''.join(c for c in text if category(c) != 'Mn')

        if not pattern or not isinstance(pattern, str) or not pattern.strip():
            oradio_log.debug("Empty or invalid search pattern: %s", pattern)
            return []

        pattern = pattern.strip()

        results = [
            result
            for field in ('artist', 'title')
            for result in (self._execute("search", field, pattern) or [])
            if isinstance(result, dict) and isinstance(result.get('file'), str)
        ]

        songs = [
            {
                'file':               result['file'],
                'artist':             result.get('artist', "Unknown artist"),
                'normalized_artist':  _normalize(result.get('artist', "Unknown artist")),
                'title':              result.get('title', "Unknown title"),
                'normalized_title':   _normalize(result.get('title', "Unknown title")),
            }
            for result in results
        ]

        # Deduplicate by normalised artist + title using an explicit loop so
        # set.add() is not used as a side-effect inside a comprehension condition.
        seen         = set()
        unique_songs = []
        for song in songs:
            key = (song['normalized_artist'], song['normalized_title'])
            if key not in seen:
                seen.add(key)
                unique_songs.append(song)

        return sorted(unique_songs, key=lambda x: (x['normalized_artist'], x['normalized_title']))

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import GREEN, YELLOW, NC    # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    # Pylint PEP8 ignoring limit of max 12 branches and 50 statements is ok for test menu
    def interactive_menu():     # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for MPDControl.
        Blocks until the user enters 0 to quit.
        """
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

        mpd_client = MPDControl()

        while True:
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            match function_nr:
                case 0:
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
                    results   = mpd_client.get_songs(selection)
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

    print("\nStarting test program...\n")

    interactive_menu()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
