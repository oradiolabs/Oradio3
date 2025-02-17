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

Monitors MPD error every 10 s 
Update stand alone tests 13, 14

"""
import time
import threading
import json
from mpd import MPDClient
import subprocess
##### oradio modules ####################
from oradio_logging import oradio_log
from play_system_sound import PlaySystemSound
##### GLOBAL constants ####################
from oradio_const import *
#from internet_checker import is_internet_available

class MPDControl:
    """
    Class to manage MPD client connection and control playback safely.
    """

    def __init__(self, host="localhost", port=6600):
        self.host = host
        self.port = port
        self.client = None
        self.mpd_lock = threading.Lock()

        # Event for cancelling MPD database update
        self.mpd_update_cancel_event = threading.Event()

        # Start MPD connection maintenance thread
        self.connection_thread = threading.Thread(target=self._maintain_connection, daemon=True)
        self.connection_thread.start()
        
        # Store the PlaySystemSound instance for later use.
        self.sound_player = PlaySystemSound()

    def _connect(self):
        """Connects to the MPD server."""
        client = MPDClient()
        client.timeout = 20
        client.idletimeout = None
        try:
            client.connect(self.host, self.port)
            oradio_log.debug("Connected to MPD server.")
            return client
        except Exception as e:
            oradio_log.error(f"Failed to connect to MPD server: {e}")
            return None

    def _is_connected(self):
        """Checks if MPD is connected."""
        try:
            if self.client:
                self.client.ping()
                return True
        except Exception:
            return False
        return False


    def _maintain_connection(self):
        """Maintains a persistent connection to MPD, reconnecting as needed, and logging errors."""
        while True:
            with self.mpd_lock:
                if not self._is_connected():
                    oradio_log.error("MPD connection lost. Reconnecting...")
                    self.client = self._connect()

                if self.client:
                    try:
                        status = self.client.status()
                        if "error" in status:
                            oradio_log.error(f"MPD reported an error: {status['error']}")
                    except Exception as e:
                        oradio_log.error(f"Error checking MPD status: {e}")

        time.sleep(10)  # Check every 10 seconds

    def _ensure_client(self):
        """Ensures an active MPD client before sending commands."""
        with self.mpd_lock:
            if not self._is_connected():
                oradio_log.debug("Reconnecting MPD client...")
                self.client = self._connect()

    def play_preset(self, preset):
        """
        Plays a preset using the global PRESET_FILE_PATH.
        Uses MPD's listplaylists to determine whether the preset is a stored playlist or a directory.
        """
        self._ensure_client()
        playlist_name = self.get_playlist_name(preset, PRESET_FILE_PATH)

        if not playlist_name:
            oradio_log.debug(f"No playlist found for preset {preset}")
            return

        with self.mpd_lock:
            try:
                self.client.clear()

                # Retrieve stored playlists from MPD.
                stored_playlists = self.client.listplaylists()
                stored_playlist_names = [pl.get("playlist") for pl in stored_playlists]

                if playlist_name in stored_playlist_names:
                    # If the preset is a stored playlist, load it.
                    self.client.load(playlist_name)
                else:
                    # Otherwise, assume it's a directory and add it.
                    self.client.add(playlist_name)

                self.client.shuffle()
                self.client.random(1)
                self.client.repeat(1)
                self.client.play()

                oradio_log.debug(f"Playing: {playlist_name}")

            except Exception as e:
                oradio_log.debug(f"Error playing preset {preset}: {e}")

    def play(self):
        """Plays the current track."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                self.client.play()
                oradio_log.debug("MPD play")
            except Exception as e:
                oradio_log.error(f"Error sending play command: {e}")

    def pause(self):
        """Pauses playback."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                self.client.pause(1)
                oradio_log.debug("MPD pause")
            except Exception as e:
                oradio_log.debug(f"Error sending pause command: {e}")

    def stop(self):
        """Stops playback."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                self.client.stop()
                oradio_log.debug("MPD stop")
            except Exception as e:
                oradio_log.error(f"Error sending stop command: {e}")

    def next(self):
        """Skips to the next track only if MPD is currently playing."""
        self._ensure_client()
        with self.mpd_lock:
            try:
                status = self.client.status()
                if status.get("state") == "play":
                    self.client.next()
                    oradio_log.debug("MPD next")
                else:
                    oradio_log.debug("Cannot skip track: MPD is not playing.")
            except Exception as e:
                oradio_log.error(f"Error sending next command: {e}")

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
            oradio_log.debug(f"Database update job ID: {job_id}")

            while True:
                # Check for cancellation before each iteration
                if self.mpd_update_cancel_event.is_set():
                    oradio_log.debug("MPD database update canceled.")
                    break

                # Check for timeout
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    oradio_log.debug(f"MPD database update timed out after {timeout_seconds} seconds.")
                    break

                status = self.client.status()
                if "updating_db" in status:
                    oradio_log.debug(f"Updating... Job ID: {status['updating_db']}")
                    # Sleep in short increments to allow for prompt cancellation and timeout checks
                    for _ in range(30):
                        if self.mpd_update_cancel_event.is_set():
                            oradio_log.debug("MPD database update canceled during sleep.")
                            break
                        if time.time() - start_time > timeout_seconds:
                            oradio_log.debug(f"MPD database update timed out during sleep after {timeout_seconds} seconds.")
                            break
                        time.sleep(0.2)
                    if self.mpd_update_cancel_event.is_set():
                        break
                    if time.time() - start_time > timeout_seconds:
                        oradio_log.warning(f"MPD database update timed out after {timeout_seconds} seconds.")
                        break
                else:
                    oradio_log.debug("MPD database update completed.")
                    break
        except Exception as e:
            oradio_log.error(f"Error updating MPD database: {e}")

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
        except subprocess.CalledProcessError as e:
            oradio_log.error(f"Error restarting MPD service: {e.stderr}")
        
    @staticmethod
    def get_playlist_name(preset_key, filepath):
        """Retrieves the playlist name for a given preset key."""
        try:
            with open(filepath, 'r') as file:
                presets = json.load(file)

            json_key = preset_key.lower()
            return presets.get(json_key, None)

        except FileNotFoundError:
            oradio_log.error(f"Error: File not found at {filepath}")
        except json.JSONDecodeError:
            oradio_log.error("Error: Failed to decode JSON. Please check the file's format.")
        return None

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("\nStarting MPD Control Standalone Test...\n")
    
    # Instantiate MPDControl
    mpd = MPDControl()
    
    import random

    input_selection = ("\nSelect a function, input the number:\n"
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
                       "Select: ")

    while True:
        try:
            function_nr = int(input(input_selection))
        except ValueError:
            function_nr = -1  # Invalid input

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
                    start_time = time.time()
                    commands = [mpd_instance.play, mpd_instance.pause, mpd_instance.next, mpd_instance.stop]
                    def random_action():
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
                mpd._ensure_client()
                with mpd.mpd_lock:
                    stored_playlists = mpd.client.listplaylists()
                if not stored_playlists:
                    print("No stored playlists found.")
                else:
                    for idx, pl in enumerate(stored_playlists, start=1):
                        print(f"{idx}. {pl.get('playlist')}")
#                     try:
#                         selection = int(input("\nSelect a playlist by number: "))
#                         if 1 <= selection <= len(stored_playlists):
#                             playlist_name = stored_playlists[selection - 1].get('playlist')
#                             print(f"\nPlaying stored playlist: {playlist_name}\n")
#                             with mpd.mpd_lock:
#                                 mpd.client.clear()
#                                 mpd.client.load(playlist_name)
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
                mpd._ensure_client()
                with mpd.mpd_lock:
                    lsinfo = mpd.client.lsinfo("/")
                directories = [entry["directory"] for entry in lsinfo if "directory" in entry]
                if not directories:
                    print("No directories found.")
                else:
                    for idx, d in enumerate(directories, start=1):
                        print(f"{idx}. {d}")
#                     try:
#                         selection = int(input("\nSelect a directory by number: "))
#                         if 1 <= selection <= len(directories):
#                             directory_name = directories[selection - 1]
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
                mpd._ensure_client()
                search_query = input("Enter search query (artist or song): ")
                with mpd.mpd_lock:
                    search_results = mpd.client.search("any", search_query)
                if not search_results:
                    print("No search results found for query:", search_query)
                    continue
                print("\nSearch results:")
                for idx, song in enumerate(search_results, start=1):
                    artist = song.get("artist", "Unknown Artist")
                    title = song.get("title", song.get("file", "Unknown Title"))
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
                for idx, song in enumerate(selected_songs, start=1):
                    artist = song.get("artist", "Unknown Artist")
                    title = song.get("title", song.get("file", "Unknown Title"))
                    print(f"{idx}. {artist} - {title}")
                confirm = input("\nIs this selection OK? (y/n): ").lower()
                if confirm != "y":
                    print("Playlist creation canceled.")
                    continue
                playlist_name = input("Enter the name for the new playlist: ")
                with mpd.mpd_lock:
                    mpd.client.clear()
                    for song in selected_songs:
                        file_path = song.get("file")
                        if file_path:
                            mpd.client.add(file_path)
                    mpd.client.save(playlist_name)
                print(f"Playlist '{playlist_name}' stored successfully.")
            case 15:
                print("\nSelect a stored playlist to play...\n")
                mpd._ensure_client()
                with mpd.mpd_lock:
                    stored_playlists = mpd.client.listplaylists()
                if not stored_playlists:
                    print("No stored playlists found.")
                else:
                    for idx, pl in enumerate(stored_playlists, start=1):
                        print(f"{idx}. {pl.get('playlist')}")
                    try:
                        selection = int(input("\nSelect a playlist by number: "))
                        if 1 <= selection <= len(stored_playlists):
                            playlist_name = stored_playlists[selection - 1].get('playlist')
                            print(f"\nPlaying stored playlist: {playlist_name}\n")
                            with mpd.mpd_lock:
                                mpd.client.clear()
                                mpd.client.load(playlist_name)
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
                mpd._ensure_client()
                with mpd.mpd_lock:
                    lsinfo = mpd.client.lsinfo("/")
                directories = [entry["directory"] for entry in lsinfo if "directory" in entry]
                if not directories:
                    print("No directories found.")
                else:
                    for idx, d in enumerate(directories, start=1):
                        print(f"{idx}. {d}")
                    try:
                        selection = int(input("\nSelect a directory by number: "))
                        if 1 <= selection <= len(directories):
                            directory_name = directories[selection - 1]
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
            case _:
                print("\nInvalid selection. Please enter a valid number.\n")