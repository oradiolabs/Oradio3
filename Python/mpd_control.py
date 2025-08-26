# pylint: disable=E1101,E1102
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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio MPD control module and playlist test scripts

Update Play_song, did not play immediate when in MPD in pause

Update MPD monitor errors to prevent hangs
"""
import time
import json
import threading
import subprocess
import unicodedata
#import socket
from mpd import MPDClient

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import PRESETS_FILE

##### Local constants ####################
CROSSFADE = 5
DEFAULT_PRESET_KEY = "preset1"  # When the Play button is used and no playlist is in the queue, this one is used

class MPDControl:
    """Class managing MPD behaviour"""

    def __init__(self, host: str = "localhost", port: int = 6600):
        """Class constructor: Initialize class variables"""
        self.host = host
        self.port = port
        self._crossfade_done = False
        self.client = self._connect()
        self.current_playlist = None
        self.last_status_error = None
        self.mpd_lock = threading.Lock()

        # Event for cancelling MPD database update
        self.mpd_update_cancel_event = threading.Event()

        # start the separate monitor thread
        thread = threading.Thread(target=self._monitor_errors, daemon=True)
        thread.start()

    def _connect(self) -> MPDClient:
        """Connect to MPD service"""
        client = MPDClient()
        client.timeout = 20
        client.idletimeout = None
        try:
            client.connect(self.host, self.port)
            oradio_log.info("Connected to MPD at %s:%s", self.host, self.port)

            # only set CROSSFADE the first time
            if not self._crossfade_done:
                try:
                    client.crossfade(CROSSFADE)
                    oradio_log.info("Set crossfade to %ss", CROSSFADE)
                except Exception as mpd_err: # pylint: disable=broad-exception-caught
                    oradio_log.warning("Crossfade failed: %s", mpd_err)
                else:
                    self._crossfade_done = True

            return client

        except Exception as con_err: # pylint: disable=broad-exception-caught
            oradio_log.error("MPD connect failed: %s", con_err)
            return None

    def _is_connected(self) -> bool:
        """Check if connected to MPD service"""
        if not self.client:
            return False
        try:
            self.client.ping()
            return True
        except Exception: # pylint: disable=broad-exception-caught
            return False

    def _ensure_client(self):
        """If not connected, connect to MPD service"""
        with self.mpd_lock:
            if not self._is_connected():
                oradio_log.info("Reconnecting MPD client…")
                self.client = self._connect()

    def _monitor_errors(self):
        """
        Background thread: every 10 s check MPD status, log any new
        'error' field, and reconnect on failure. Never blocks playback.
        """
        while True:
            try:
                # Ensure we have a live connection
                if not self._is_connected():
                    oradio_log.info("MPD monitor: reconnecting…")
                    self.client = self._connect()
                    # Reset so the first status error gets logged
                    self.last_status_error = None

                # If we're still not connected, skip this cycle
                if not self._is_connected():
                    time.sleep(10)
                    continue

                # Pull status once
                status = self.client.status()
                err = status.get("error")

                # New error → log once
                if err and err != self.last_status_error:
                    oradio_log.error("MPD reported error: %s", err)
                    self.last_status_error = err

                # Error cleared → log once
                elif not err and self.last_status_error is not None:
                    oradio_log.info("MPD error cleared")
                    self.last_status_error = None

            except Exception as ex_err: # pylint: disable=broad-exception-caught
                # On any exception, drop the client so _is_connected() will fail
                oradio_log.warning("MPD monitor exception: %s", ex_err)
                self.client = None
                self.last_status_error = None

            # Wait a bit before the next check
            time.sleep(10)


    def play_preset(self, preset):
        """
        Plays a preset using the global PRESETS_FILE.
        Uses MPD's listplaylists to determine whether the preset is a stored playlist or a directory.
        """
        self._ensure_client()
        playlist_name = self.get_playlist_name(preset, PRESETS_FILE)

        if not playlist_name:
            oradio_log.debug("No playlist found for preset: %s", preset)
            return False

        with self.mpd_lock:
            try:
                self.client.clear()

                # Retrieve stored playlists from MPD.
                stored_playlists = self.client.listplaylists()
                stored_playlist_names = [pl.get("playlist") for pl in stored_playlists]

                if playlist_name in stored_playlist_names:
                    # Stored playlist: load in saved order
                    self.client.load(playlist_name)
                    self.client.random(0)  # turn OFF random play
                    self.client.repeat(1)  # 0 = play once through; 1 = loop
                    # Store current playlist name
                    self.current_playlist = playlist_name
                else:
                    # Directory: add then shuffle/repeat as before
                    self.client.add(playlist_name)
                    self.client.shuffle()  # one-time scramble
                    self.client.random(1)  # enable random as tracks advance
                    self.client.repeat(1)  # loop directory indefinitely
                    # Clear current playlist name
                    self.current_playlist = None

                self.client.play()
                oradio_log.debug("Playing: %s", playlist_name)
                return True

            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.debug("Error playing preset %s: %s", preset, ex_err)
                return False


    def play(self):
        """Plays the current track; if the queue is empty, load the default preset."""
        self._ensure_client()
        try:
            if self.current_queue_filled():
                with self.mpd_lock:
                    self.client.play()
                oradio_log.debug("MPD play current playlist")
                return

            # Queue is empty: load/play default preset (outside lock to avoid deadlock)
            success = self.play_preset(DEFAULT_PRESET_KEY)
            if not success:
                oradio_log.warning("Default preset '%s' failed or was undefined", DEFAULT_PRESET_KEY)
            else:
                oradio_log.info("Queue empty; loaded default preset '%s'", DEFAULT_PRESET_KEY)

        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Error in play(): %s", ex_err)

    def pause(self):
        """Pauses playback."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                self.client.pause(1)
                oradio_log.debug("MPD pause")
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.debug("Error sending pause command: %s", ex_err)

    def stop(self):
        """Stops playback."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                self.client.stop()
                oradio_log.debug("MPD stop")
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Error sending stop command: %s", ex_err)

    def next(self):
        """Skips to the next track only if MPD is currently playing."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                status = self.client.status()
                if status.get("state") == "play":
                    # Next song playlist / directory
                    if self.current_playlist:
                        # Go to next song in the list, to first if at the end
                        next_index = int(status.get("song")) + 1
                        if next_index >= len(self.client.playlistinfo()):
                            next_index = 0
                        # Playlist may have changed through playlist_add() / playlist_remove() via the web interface
                        self.client.clear()
                        self.client.load(self.current_playlist)
                        # Play the next song
                        self.client.play(next_index)
                    else:
                        self.client.next()
                    oradio_log.debug("MPD next")
                else:
                    oradio_log.debug("Cannot skip track: MPD is not playing.")
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Error sending next command: %s", ex_err)

    def current_queue_filled(self) -> bool:
        """
        Return True if MPD's current queue has at least one item.
        Safe: returns False on errors or if not connected.
        """
        self._ensure_client()
        with self.mpd_lock:
            if not self._is_connected():
                return False
            try:
                queue = self.client.playlistinfo()
            except Exception as exc: # pylint: disable=broad-exception-caught
                oradio_log.warning("current_queue_filled: failed to get playlistinfo: %s", exc)
                return False
        return bool(queue)



    def update_mpd_database(self):
        """Updates the MPD database with a timeout."""
        self._ensure_client()
        # Reset cancellation event at the start of the update
        self.mpd_update_cancel_event.clear()
        timeout_seconds = 60  # timeout to stop updating
        start_time = time.time()

        try:
            oradio_log.debug("Starting MPD database update...")
            job_id = self.client.update()
            oradio_log.debug("Database update job ID: %s", job_id)

            while True:
                # Check for cancellation before each iteration
                if self.mpd_update_cancel_event.is_set():
                    oradio_log.debug("MPD database update canceled.")
                    break

                # Check for timeout
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    oradio_log.debug("MPD database update timed out after %s seconds", timeout_seconds)
                    break

                status = self.client.status()
                if "updating_db" in status:
                    oradio_log.debug("Updating... Job ID: %s", status['updating_db'])
                    # Sleep in short increments to allow for prompt cancellation and timeout checks
                    for _ in range(30):
                        if self.mpd_update_cancel_event.is_set():
                            oradio_log.debug("MPD database update canceled during sleep.")
                            break
                        if time.time() - start_time > timeout_seconds:
                            oradio_log.debug("MPD database update timed out during sleep after %s seconds.", timeout_seconds)
                            break
                        time.sleep(0.2)
                    if self.mpd_update_cancel_event.is_set():
                        break
                    if time.time() - start_time > timeout_seconds:
                        oradio_log.warning("MPD database update timed out after %s seconds.", timeout_seconds)
                        break
                else:
                    oradio_log.debug("MPD database update completed.")
                    break
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error updating MPD database: %s", ex_err)

    def start_update_mpd_database_thread(self):
        """Starts the MPD database update in a separate thread."""
        update_thread = threading.Thread(target=self.update_mpd_database, daemon=True)
        update_thread.start()
        oradio_log.debug("MPD database update thread started.")
        return update_thread

    def cancel_update(self):
        """Cancels the ongoing MPD database update."""
        self.mpd_update_cancel_event.set()
        oradio_log.debug("MPD database update cancellation requested.")

    def restart_mpd_service(self):
        """Restarts the MPD service using systemctl."""
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", "mpd"],
                capture_output=True,
                text=True,
                check=True
            )
            oradio_log.debug("MPD service restarted successfully.")
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("Error restarting MPD service: %s", ex_err.stderr)

    def get_directories(self):
        """
        Get available directories
        Return case-insensitive sorted list
        """
        # Connect if not connected
        self._ensure_client()

        # Initialize
        folders = []

        # Get directories
        try:
            with self.mpd_lock:
                directories = self.client.listfiles()

            # Parse directories for name only; only include if "directory" key exists
            for entry in directories:
                if "directory" in entry:
                    folders.append(entry["directory"])

        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error getting directories: %s", ex_err)

        # Sort alphabetically, ignore case
        return sorted(folders, key=str.casefold)

    def get_playlists(self):
        """
        Get available playlists
        Determine if the list is a web radio or not
        Return list case-insensitive sorted bij playlist name
        """
        # Connect if not connected
        self._ensure_client()

        # Initialize
        result = []

        # Get playlists
        try:
            with self.mpd_lock:
                playlists = self.client.listplaylists()
                # Parse playlists for name and web radio or not
                for playlist in playlists:
                    # Get playlist name
                    playlist_name = playlist.get("playlist", None)
                    # Check if any element in the playlist is a url, meaning a web radio
                    webradio = False
                    for element in self.client.listplaylistinfo(playlist_name):
                        file_path = element.get("file", "")
                        if file_path.startswith(("http://", "https://")):
                            webradio = True
                            break
                    # Add playlist and if webradio or not to result
                    if webradio:
                        result.append({"playlist": playlist_name, "webradio": True})
                    else:
                        result.append({"playlist": playlist_name, "webradio": False})

        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error getting playlists: %s", ex_err)

        # Sort alphabetically, ignore case
        return sorted(result, key=lambda x: x["playlist"].lower())

    def get_songs(self, playlist_name):
        """
        List the songs in playlist_name
        Return [{file: ..., artist:..., title:...}, ...]
        """
        # Connect if not connected
        self._ensure_client()

        # Initialize
        songs = []

        # Get songs
        try:
            # Get playlists and directories
            with self.mpd_lock:
                playlists = self.client.listplaylists()
                directories = self.client.listfiles()

            # Check playlists
            for playlist in playlists:
                if playlist_name == playlist.get('playlist'):
                    # Get playlist song details; minimize lock to mpd interaction
                    with self.mpd_lock:
                        details = self.client.listplaylistinfo(playlist_name)
                    for detail in details:
                        songs.append({
                            'file': detail['file'],
                            'artist': detail.get('artist', 'Unknown artist'),
                            'title': detail.get('title', 'Unknown title')
                        })

                    # return list of songs in order they are listed in the playlist
                    return songs

            # Check directories
            for entry in directories:
                # Only consider entries that are directories.
                if "directory" in entry and playlist_name == entry["directory"]:
                    # Get directory song details; minimize lock to mpd interaction
                    with self.mpd_lock:
                        details = self.client.lsinfo(entry["directory"])
                    for detail in details:
                        songs.append({
                            'file': detail['file'],
                            'artist': detail.get('artist', 'Unknown artist'),
                            'title': detail.get('title', 'Unknown title')
                        })

                    # return alphabetically sorted list of songs
                    return sorted(songs, key=lambda x: x['artist'].lower())

        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error getting songs for '%s': %s", playlist_name, ex_err)

        # Return empty as list is not an known playlist or directory
        return songs

    def search(self, pattern):
        """
        List the songs matching the pattern in artist or title attributes
        Remove duplicates songs
        Sort songs alphabetically, first on artist, then on title
        Return [{file: ..., artist:..., title:...}, ...]
        """
        # Function to lowercase + trim + strip diacritics (accents)
        def _normalize(text):
            text = text.strip().lower()
            text = unicodedata.normalize('NFD', text)
            text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
            return text

        # Connect if not connected
        self._ensure_client()

        try:
            # Search artists and titles; minimize lock to mpd interaction
            with self.mpd_lock:
                results = self.client.search('artist', pattern)
                results = results + self.client.search('title', pattern)

            # Initialize
            songs = []

            # Parse search results in expected format
            for result in results:
                songs.append({
                      'file': result['file'],
                      'artist': result.get('artist', 'Unknown artist'),
                      'normalized_artist': _normalize(result.get('artist', 'Unknown artist')),
                      'title': result.get('title', 'Unknown title'),
                      'normalized_title': _normalize(result.get('title', 'Unknown title'))
                })

            # Filter songs to be unique based on artist and title
            found = set()
            unique = []

            # Filter songs to be unique based on normalized artist and normalized title
            for song in songs:
                key = (song['normalized_artist'], song['normalized_title'])
                if key not in found:
                    found.add(key)
                    unique.append(song)

            # Return list of songs with attributes file, artist, title. Sorted by artist, then title, ignore case
            return sorted(unique, key=lambda x: (x['normalized_artist'], x['normalized_title']))

        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error searching for songs with pattern '%s' in artist or title attribute: %s", pattern, ex_err)
            return []

    def play_song(self, song):
        """
        Play song once without clearing the current queue.
        The song is appended, moved immediately after the current song, and played.
        Once it finishes, it is removed from the queue.
        """
        # Connect if not connected
        self._ensure_client()

        try:
            oradio_log.debug("Attempting to play song: %s", song)
            with self.mpd_lock:
                status = self.client.status()
                state = status.get("state", "stop")
                # Now treat both "play" and "pause" states similarly.
                if state in ("play", "pause") and "song" in status:
                    current_song_index = int(status.get("song"))
                    inserted_song_id = int(self.client.addid(song))
                    playlist = self.client.playlistinfo()
                    new_song_index = len(playlist) - 1
                    target_index = current_song_index + 1
                    if new_song_index != target_index:
                        self.client.move(new_song_index, target_index)
                    # Force jump to the inserted song regardless of pause or play state.
                    self.client.play(target_index)
                    oradio_log.debug("Started playback at index %s", target_index)
                else:
                    inserted_song_id = int(self.client.addid(song))
                    self.client.play()
                    oradio_log.debug("Started playback as no song was currently playing")

            threading.Thread(
                target=self._remove_song_when_finished,
                args=(inserted_song_id,),
                daemon=True
            ).start()
            oradio_log.debug("Started monitoring removal for song id: %s", inserted_song_id)

        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error playing song '%s': %s", song, ex_err)

    def _remove_song_when_finished(self, inserted_song_id):
        """
        Polls MPD status until the song with inserted_song_id is finished,
        then removes it from the playlist.
        """
        try:
            oradio_log.debug("Monitoring song id %s until finish", inserted_song_id)
            while True:
                time.sleep(1)
                with self.mpd_lock:
                    status = self.client.status()
                    current_song_id = int(status.get("songid", -1))
                    if current_song_id != inserted_song_id:
                        break
                    time_str = status.get("time", None)
                    if time_str:
                        try:
                            elapsed_str, duration_str = time_str.split(":")
                            elapsed = float(elapsed_str)
                            duration = float(duration_str)
                            if elapsed >= duration - 1:
                                break
                        except Exception: # pylint: disable=broad-exception-caught
                            pass
            with self.mpd_lock:
                playlist = self.client.playlistinfo()
                # Check if the inserted song is still in the playlist
                still_present = any(int(song.get("id", -1)) == inserted_song_id for song in playlist)
                if still_present:
                    self.client.deleteid(inserted_song_id)
                    oradio_log.debug("Deleted song id %s", inserted_song_id)
                else:
                    oradio_log.debug("Song id %s already removed from the playlist", inserted_song_id)
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error removing song with id '%s': %s", inserted_song_id, ex_err)

    def playlist_add(self, playlist, song):
        """
        Create playlist if not exist
        Add song to playlist
        Return success | fail
        """
        # Connect if not connected
        self._ensure_client()

        try:
            with self.mpd_lock:
                if song is None:
                    oradio_log.debug("Attempting to create playlist: '%s'", playlist)
                    # Check if playlist already exists
                    playlists = self.client.listplaylists()
                    if not any(d.get("playlist") == playlist for d in playlists):
                        # Create playlist and add dummy url
                        self.client.playlistadd(playlist, "https://dummy.mp3")
                        # Delete the dummy → playlist is now empty
                        self.client.playlistdelete(playlist, 0)
                        oradio_log.debug("Created playlist '%s'", playlist)
                    else:
                        oradio_log.warning("Playlist '%s' already exists", playlist)
                else:
                    oradio_log.debug("Attempting to add song '%s' to playlist: '%s'", song, playlist)
                    # Add song to playlist, creating playlist if it does not exist
                    self.client.playlistadd(playlist, song)
                    oradio_log.debug("Song '%s' added to playlist '%s'", song, playlist)
                    # Getting the list of songs for the playlist will update the mpd database
                    self.client.listplaylistinfo(playlist)
            return True
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            if song is None:
                oradio_log.error("Error creating playlist '%s': %s", playlist, ex_err)
            else:
                oradio_log.error("Error adding song '%s' to playlist '%s': %s", song, playlist, ex_err)
            return False

    def playlist_remove(self, playlist, song):
        """
        Remove song from playlist
        Remove playlist
        Return success | fail
        """
        # Connect if not connected
        self._ensure_client()

        try:
            with self.mpd_lock:
                if song is None:
                    oradio_log.debug("Attempting to remove playlist: '%s'", playlist)
                    playlists = self.client.listplaylists()
                    if any(d.get("playlist") == playlist for d in playlists):
                        # Delete playlist
                        self.client.rm(playlist)
                        oradio_log.debug("Playlist '%s' removed", playlist)
                    else:
                        oradio_log.warning("Playlist '%s' does not exist", playlist)
                else:
                    oradio_log.debug("Attempting to remove song '%s' from playlist: '%s'", song, playlist)
                    # Get playlist songs
                    items = self.client.listplaylist(playlist)
                    # Find index of song in playlist
                    index = items.index(song)
                    # Remove song from playlist
                    self.client.playlistdelete(playlist, index)
                    oradio_log.debug("Song '%s' removed from playlist '%s'", song, playlist)
                    # Getting the list of songs for the playlist will update the mpd database
                    self.client.listplaylistinfo(playlist)
            return True
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            if song is None:
                oradio_log.error("Error removing playlist '%s': %s", playlist, ex_err)
            else:
                oradio_log.error("Error removing song '%s' from playlist '%s': %s", song, playlist, ex_err)
            return False

    def preset_is_webradio(self, preset):
        """
        Check if playlist is a web radio
        Return success | fail
        """
        # Connect if not connected
        self._ensure_client()
        playlist = self.get_playlist_name(preset, PRESETS_FILE)

        try:
            # Get entries from the specific playlist
            with self.mpd_lock:
                entries = self.client.listplaylistinfo(playlist)

            # Iterate through entries
            for song in entries:
                if song.get("file", "").startswith(("http://", "https://")):
                    oradio_log.debug("'%s' with playlist '%s' is a web radio", preset, playlist)
                    # Web radio if entry is url
                    return True

        except Exception: # pylint: disable=broad-exception-caught
            pass

        # No entries or no http(s) or errors all mean 'no web radio'
        oradio_log.debug("'%s' with playlist '%s' is a playlist", preset, playlist)
        return False

    def current_is_webradio(self):
        """Returns if current song is a web radio or not"""
        self._ensure_client()
        try:
            with self.mpd_lock:
                # Get current song
                current_song = self.client.currentsong()

            # Return True if the "file" is a URL
            return current_song.get("file", "").startswith(("http://", "https://"))
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error checking if current song is a web radio: %s", ex_err)
            return False

    @staticmethod
    def get_playlist_name(preset_key, filepath):
        """Retrieves the playlist name for a given preset key."""
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                presets = json.load(file)

            json_key = preset_key.lower()
            return presets.get(json_key, None)

        except FileNotFoundError:
            oradio_log.error("Error: File not found at %s", filepath)
        except json.JSONDecodeError:
            oradio_log.error("Error: Failed to decode JSON. Please check the file's format.")
        return None

# mpd_control.py
# —————————————————————————————————————————————————————————————
# Singleton factory for MPDControl
#
# This function ensures that only one MPDControl instance is ever created
# during the process lifetime. On the first call it constructs and returns
# the MPDControl (opening the MPD connection); all later calls simply
# return that same object, preventing duplicate MPDClient connections
# and redundant setup such as crossfade re-configuration.

_mpd_singleton: MPDControl | None = None

def get_mpd_control(host: str = "localhost", port: int = 6600) -> MPDControl:
    """
    Return the one-and-only MPDControl instance.
    Subsequent calls reuse the same object.
    """
    global _mpd_singleton # pylint: disable=global-statement
    if _mpd_singleton is None:
        _mpd_singleton = MPDControl(host, port)
    return _mpd_singleton


# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print("\nStarting MPD Control Standalone Test...\n")

    # Instantiate MPDControl
    mpd = MPDControl()

    import random

    INPUT_SELECTION = ("\nSelect a function, input the number:\n"
                       " 0  - Quit\n"
                       " 1  - Play\n"
                       " 2  - Stop\n"
                       " 3  - Pause\n"
                       " 4  - Next Track\n"
                       " 5  - Play Preset 1\n"
                       " 6  - Play Preset 2\n"
                       " 7  - Play Preset 3\n"
                       " 8  - Update Database\n"
                       " 9  - Cancel Database Update\n"
                       "10  - Stress Test\n"
                       "11  - Restart MPD Service\n"
                       "12  - List Available Stored Playlists\n"
                       "13  - List Available Directories\n"
                       "14  - Create and Store a New Playlist\n"
                       "15  - Select a Stored Playlist to Play\n"
                       "16  - Select an Available Directory to Play\n"
                       "17  - Check if preset is web radio\n"
                       "18  - Check if current song is web radio\n"
                       "Select: ")

    while True:
        try:
            function_nr = int(input(INPUT_SELECTION))  # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1  # pylint: disable=invalid-name

        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print("\nExecuting: Play\n")
                mpd.play()
            case 2:
                print("\nExecuting: Stop\n")
                mpd.stop()
            case 3:
                print("\nExecuting: Pause\n")
                mpd.pause()
            case 4:
                print("\nExecuting: Next Track\n")
                mpd.next()
            case 5:
                print("\nExecuting: Play Preset 1\n")
                mpd.play_preset("Preset1")
            case 6:
                print("\nExecuting: Play Preset 2\n")
                mpd.play_preset("Preset2")
            case 7:
                print("\nExecuting: Play Preset 3\n")
                mpd.play_preset("Preset3")
            case 8:
                print("\nExecuting: Update MPD Database\n")
                mpd.start_update_mpd_database_thread()
            case 9:
                print("\nExecuting: Cancel Database Update\n")
                mpd.cancel_update()
            case 10:
                print("\nExecuting: Stress Test\n")
                def stress_test(mpd_instance, duration=10):
                    """Stress test MPD service by running random commands"""
                    start_time = time.time()
                    commands = [mpd_instance.play, mpd_instance.pause, mpd_instance.next, mpd_instance.stop]
                    def random_action():
                        """Run random command for a period of time"""
                        while time.time() - start_time < duration:
                            action = random.choice(commands)
                            action()
                            time.sleep(random.uniform(0.1, 0.5))
                    thread_list = [threading.Thread(target=random_action) for _ in range(5)]
                    for thread in thread_list:
                        thread.start()
                    for thread in thread_list:
                        thread.join()
                    print("\nStress test completed.\n")
                stress_test(mpd)
            case 11:
                print("\nExecuting: Restart MPD Service\n")
                mpd.restart_mpd_service()
            case 12:
                print("\nListing available stored playlists...\n")
                mpd._ensure_client()    # pylint: disable=protected-access
                with mpd.mpd_lock:
                    lists = mpd.client.listplaylists()
                if not lists:
                    print("No stored playlists found.")
                else:
                    for idx, pl in enumerate(lists, start=1):
                        print(f"{idx}. {pl.get('playlist')}")
#                     try:
#                         selection = int(input("\nSelect a playlist by number: "))
#                         if 1 <= selection <= len(lists):
#                             list_name = lists[selection - 1].get('playlist')
#                             print(f"\nPlaying stored playlist: {list_name}\n")
#                             with mpd.mpd_lock:
#                                 mpd.client.clear()
#                                 mpd.client.load(list_name)
#                                 mpd.client.shuffle()
#                                 mpd.client.random(1)
#                                 mpd.client.repeat(1)
#                                 mpd.client.play()
#                         else:
#                             print("Invalid selection.")
#                     except ValueError:
#                         print("Invalid input, please enter a number.")
            case 13:
                print("\nListing available directories...\n")
                mpd._ensure_client()    # pylint: disable=protected-access
                with mpd.mpd_lock:
                    lsinfo = mpd.client.lsinfo("/")
                dirs = [entry["directory"] for entry in lsinfo if "directory" in entry]
                if not dirs:
                    print("No directories found.")
                else:
                    for idx, d in enumerate(dirs, start=1):
                        print(f"{idx}. {d}")
#                     try:
#                         selection = int(input("\nSelect a directory by number: "))
#                         if 1 <= selection <= len(dirs):
#                             directory_name = dirs[selection - 1]
#                             print(f"\nPlaying directory: {directory_name}\n")
#                             with mpd.mpd_lock:
#                                 mpd.client.clear()
#                                 mpd.client.add(directory_name)
#                                 mpd.client.shuffle()
#                                 mpd.client.random(1)
#                                 mpd.client.repeat(1)
#                                 mpd.client.play()
#                         else:
#                             print("Invalid selection.")
#                     except ValueError:
#                         print("Invalid input, please enter a number.")
            case 14:
                print("\nCreating and storing a new playlist...\n")
                mpd._ensure_client()    # pylint: disable=protected-access
                search_query = input("Enter search query (artist or song): ")
                with mpd.mpd_lock:
                    search_results = mpd.client.search("any", search_query)
                if not search_results:
                    print("No search results found for query:", search_query)
                    continue
                print("\nSearch results:")
                for idx, item in enumerate(search_results, start=1):
                    artist = item.get("artist", "Unknown Artist")
                    title = item.get("title", item.get("file", "Unknown Title"))
                    print(f"{idx}. {artist} - {title}")
                selection_input = input("\nEnter song numbers to add (comma-separated): ")
                try:
                    selections = [int(num.strip()) for num in selection_input.split(",")]
                except ValueError:
                    print("Invalid input. Please enter numbers separated by commas.")
                    continue
                selected_songs = []
                for num in selections:
                    if 1 <= num <= len(search_results):
                        selected_songs.append(search_results[num-1])
                    else:
                        print(f"Number {num} is out of range, ignoring.")
                if not selected_songs:
                    print("No valid songs selected.")
                    continue
                print("\nSelected songs:")
                for idx, item in enumerate(selected_songs, start=1):
                    artist = item.get("artist", "Unknown Artist")
                    title = item.get("title", item.get("file", "Unknown Title"))
                    print(f"{idx}. {artist} - {title}")
                confirm = input("\nIs this selection OK? (y/n): ").lower()
                if confirm != "y":
                    print("Playlist creation canceled.")
                    continue
                list_name = input("Enter the name for the new playlist: ")
                with mpd.mpd_lock:
                    mpd.client.clear()
                    for item in selected_songs:
                        filename = item.get("file")
                        if filename:
                            mpd.client.add(filename)
                    mpd.client.save(list_name)
                print(f"Playlist '{list_name}' stored successfully.")
            case 15:
                print("\nSelect a stored playlist to play...\n")
                mpd._ensure_client()    # pylint: disable=protected-access
                with mpd.mpd_lock:
                    lists = mpd.client.listplaylists()
                if not lists:
                    print("No stored playlists found.")
                else:
                    for idx, pl in enumerate(lists, start=1):
                        print(f"{idx}. {pl.get('playlist')}")
                    try:
                        selection = int(input("\nSelect a playlist by number: "))
                        if 1 <= selection <= len(lists):
                            list_name = lists[selection - 1].get('playlist')
                            print(f"\nPlaying stored playlist: {list_name}\n")
                            with mpd.mpd_lock:
                                mpd.client.clear()
                                mpd.client.load(list_name)
                                mpd.client.shuffle()
                                mpd.client.random(1)
                                mpd.client.repeat(1)
                                mpd.client.play()
                        else:
                            print("Invalid selection.")
                    except ValueError:
                        print("Invalid input, please enter a number.")
            case 16:
                print("\nSelect an available directory to play...\n")
                mpd._ensure_client()    # pylint: disable=protected-access
                with mpd.mpd_lock:
                    lsinfo = mpd.client.lsinfo("/")
                dirs = [entry["directory"] for entry in lsinfo if "directory" in entry]
                if not dirs:
                    print("No directories found.")
                else:
                    for idx, d in enumerate(dirs, start=1):
                        print(f"{idx}. {d}")
                    try:
                        selection = int(input("\nSelect a directory by number: "))
                        if 1 <= selection <= len(dirs):
                            directory_name = dirs[selection - 1]
                            print(f"\nPlaying directory: {directory_name}\n")
                            with mpd.mpd_lock:
                                mpd.client.clear()
                                mpd.client.add(directory_name)
                                mpd.client.shuffle()
                                mpd.client.random(1)
                                mpd.client.repeat(1)
                                mpd.client.play()
                        else:
                            print("Invalid selection.")
                    except ValueError:
                        print("Invalid input, please enter a number.")
            case 17:
                selection = int(input("\nEnter preset number 1, 2 or 3 to check: "))
                if mpd.preset_is_webradio(f"preset{selection}"):
                    print(f"\npreset{selection} is a web radio\n")
                else:
                    print(f"\npreset{selection} is NOT a web radio\n")
            case 18:
                if mpd.current_is_webradio():
                    print("\nCurrent song is a web radio\n")
                else:
                    print("\nCurrent song is NOT a web radio\n")
            case _:
                print("\nInvalid selection. Please enter a valid number.\n")
