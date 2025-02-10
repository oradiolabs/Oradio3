#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio MPD control module

"""
import time
import threading
import json
from mpd import MPDClient

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import *

class MPDControl:
    """
    Class to manage MPD client connection and control playback safely.
    """

    def __init__(self, host="localhost", port=6600):
        self.host = host
        self.port = port
        self.client = None
        self.mpd_lock = threading.Lock()

        # Start MPD connection maintenance thread
        self.connection_thread = threading.Thread(target=self._maintain_connection, daemon=True)
        self.connection_thread.start()

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
        """Maintains a persistent connection to MPD, reconnecting as needed."""
        while True:
            with self.mpd_lock:
                if not self._is_connected():
                    oradio_log.error("MPD connection lost. Reconnecting...")
                    self.client = self._connect()
            time.sleep(10)

    def _ensure_client(self):
        """Ensures an active MPD client before sending commands."""
        with self.mpd_lock:
            if not self._is_connected():
                oradio_log.error("Reconnecting MPD client...")
                self.client = self._connect()
                
    def play_preset(self, preset):
        """
        Plays a preset playlist using the global PRESET_FILE_PATH
        Presets as in Json file : "Preset1", "Preset2", "Preset3"
        """
        
        self._ensure_client()
        playlist_name = self.get_playlist_name(preset, PRESET_FILE_PATH)

        if not playlist_name:
            oradio_log.error(f"No playlist found for preset {preset}")
            return

        with self.mpd_lock:
            try:
                self.client.clear()
                if playlist_name.startswith("WebRadio"): # play Webradio if Prest starts with Webradio
                    self.client.load(playlist_name)
                else:
                    self.client.add(playlist_name)

                self.client.shuffle()
                self.client.random(1)
                self.client.repeat(1)
                self.client.play()

                oradio_log.debug(f"Playing playlist: {playlist_name}")

            except Exception as e:
                oradio_log.error(f"Error playing preset {preset}: {e}")


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
                oradio_log.error(f"Error sending pause command: {e}")

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
                    oradio_log.warning("Cannot skip track: MPD is not playing.")
            except Exception as e:
                oradio_log.error(f"Error sending next command: {e}")

    def update_mpd_database(self):
        """Updates the MPD database."""
        self._ensure_client()
#        with self.mpd_lock:   # No lock needed, as seperate track and MPD can handle this in parralel
        try:
            oradio_log.debug("Starting MPD database update...")
            job_id = self.client.update()
            oradio_log.debug(f"Database update job ID: {job_id}")

            while True:
                status = self.client.status()
                if "updating_db" in status:
                    oradio_log.debug(f"Updating... Job ID: {status['updating_db']}")
                    time.sleep(3)
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

    @staticmethod  # just a simple function
    def get_playlist_name(preset_key, filepath):
        """Retrieves the playlist name for a given preset key."""
        try:
            with open(filepath, 'r') as file:
                presets = json.load(file)

            # Convert preset key to lowercase to match JSON format (e.g., "Preset1" -> "preset1")
            json_key = preset_key.lower()

            return presets.get(json_key, None)

        except FileNotFoundError:
            oradio_log.error(f"Error: File not found at {filepath}")
        except json.JSONDecodeError:
            oradio_log.error("Error: Failed to decode JSON. Please check the file's format.")
        return None

# Entry point for stand-alone operation
if __name__ == '__main__':
    
    import random
    
    print("\nStarting MPD Control Standalone Test...\n")
    
    # Instantiate MPDControl
    mpd = MPDControl()

    # Show menu with test options
    input_selection = ("\nSelect a function, input the number:\n"
                       " 0 - Quit\n"
                       " 1 - Play\n"
                       " 2 - Stop\n"
                       " 3 - Pause\n"
                       " 4 - Next Track\n"
                       " 5 - Play Preset 1\n"
                       " 6 - Play Preset 2\n"
                       " 7 - Play Preset 3\n"
                       " 8 - Update Database\n"
                       " 9 - Stress Test\n"
                       "Select: ")

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(input_selection))
        except ValueError:
            function_nr = -1  # Invalid input

        # Execute selected function
        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print("\nExecuting: Play\n")
                mpd.play()
            case 2:
                print("\nExecuting: Pause\n")
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
                print("\nExecuting: Stress Test\n")
                
                def stress_test(mpd_instance, duration=10):
                    """Stress test: Random MPD actions in parallel for a given duration."""
                    start_time = time.time()
                    commands = [mpd_instance.play, mpd_instance.pause, mpd_instance.next, mpd_instance.stop]

                    def random_action():
                        while time.time() - start_time < duration:
                            action = random.choice(commands)
                            action()
                            time.sleep(random.uniform(0.1, 0.5))  # Random delay between commands

                    # Launch multiple threads
                    thread_list = [threading.Thread(target=random_action) for _ in range(5)]
                    
                    # Start all threads
                    for thread in thread_list:
                        thread.start()
                    
                    # Wait for all threads to complete
                    for thread in thread_list:
                        thread.join()

                    print("\nStress test completed.\n")

                stress_test(mpd)

            case _:
                print("\nInvalid selection. Please enter a valid number.\n")
