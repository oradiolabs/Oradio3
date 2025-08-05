#!/usr/bin/env python3

"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Februari 1, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:  Spotify Connect

The librespot audio Spotify Connect is (un) muted when Oradio on/off. Connection stays active and music streaming
The status of the Librespot connection is monitored via Librespot events
which puts the status in two files spotactive.flag and spotplaying.flag

"""

import time
import subprocess
import threading
from multiprocessing import Queue


#### Oradio modules ####
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    MESSAGE_SPOTIFY_TYPE,
    MESSAGE_NO_ERROR,
    SPOTIFY_CONNECT_CONNECTED_EVENT,
    SPOTIFY_CONNECT_DISCONNECTED_EVENT,
    SPOTIFY_CONNECT_PLAYING_EVENT,
    SPOTIFY_CONNECT_PAUSED_EVENT
)

##### LOCAL constants ####################
# the first volume controller of Spotify in asound.conf which is put to 0% if state is off
ALSA_MIXER_SPOTCON = "VolumeSpotCon1"
# Define the flag file paths as class constants
ACTIVE_FLAG_FILE  = "/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotplaying.flag"

class SpotifyConnect:
    """ Basic Spotify functionality based on Librespot service """
    def __init__(self, message_queue=None):
        """
        Initialize with a message queue.
        The message_queue is used to send events to oradio_control.py.
        Also starts monitoring flags in a separate thread.
        """
        self.active = False
        self.playing = False
        self.message_queue = message_queue

        # Reset flag files to 0 at initialization.
        self.initialize_flags()

        # Start monitor_flags in a separate daemon thread.
        self.monitor_thread = threading.Thread(target=self.monitor_flags, daemon=True)
        self.monitor_thread.start()
        oradio_log.info("Monitor thread started.")

    def _reset_flag(self, filepath):
        """
        Writes '0' to the file at 'filepath' to reset the flag.
        """
        try:
            with open(filepath, "w", encoding="utf-8") as file:
                file.write("0")
            oradio_log.info("Successfully reset %s to 0", filepath)
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error resetting %s: %s", filepath, ex_err)

    def initialize_flags(self):
        """
        Resets both the 'active' and 'playing' flag files to 0.
        This method should be called during initialization.
        """
        self._reset_flag(ACTIVE_FLAG_FILE)
        self._reset_flag(PLAYING_FLAG_FILE)


    def _read_flag(self, filepath):
        """
        Reads the file at 'filepath' and returns True if its content is '1',
        otherwise returns False. Returns False if the file cannot be read.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                content = file.read().strip()
                return content == "1"
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error reading %s: %s", filepath, ex_err)
            return False

    def update_flags(self):
        """
        Update the 'active' and 'playing' booleans by reading their flag files.
        """
        self.active = self._read_flag(ACTIVE_FLAG_FILE)
        self.playing = self._read_flag(PLAYING_FLAG_FILE)

    def send_event(self, event):
        """
        Sends an event via the message queue.
        The message is a dictionary containing:
          - 'type': MESSAGE_SPOTIFY_TYPE (from oradio_const)
          - 'state': the event state
        """
        if self.message_queue:
            try:
                message = {"type": MESSAGE_SPOTIFY_TYPE, "state": event, "error": MESSAGE_NO_ERROR}
                self.message_queue.put(message)
                oradio_log.info("Message sent to queue: %s", message)
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Error sending message to queue: %s", ex_err)
        else:
            oradio_log.error("Message queue is not set. Cannot send event.")

    def play(self):
        """
        Unmute Spotify Connect by setting ALSA channel to 100%
        """
        try:
            subprocess.run(
                ["amixer", "-c", "DigiAMP", "sset", ALSA_MIXER_SPOTCON, "100%"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            oradio_log.info("Spotify Connect UnMuted via amixer")
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("Error unmuting via amixer: %s", ex_err)

    def pause(self):
        """
        Mute Spotify Connect by setting ALSA channel to 0%
        """
        try:
            subprocess.run(
                ["amixer", "-c", "DigiAMP", "sset", ALSA_MIXER_SPOTCON, "0%"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            oradio_log.info("Spotify Connect Muted via amixer")
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("Error muting via amixer: %s", ex_err)

    def get_state(self):
        """ Return web service state """
        return {"active": self.active, "playing": self.playing}

    def monitor_flags(self, interval=0.5):
        """
        Continuously monitors the flag files and sends events when their
        values change:
          - When active_flag goes from 0 to 1, sends SPOTIFY_CONNECT_CONNECTED_EVENT.
          - When active_flag goes from 1 to 0, sends SPOTIFY_CONNECT_DISCONNECTED_EVENT.
          - When playing_flag goes from 0 to 1, sends SPOTIFY_CONNECT_PLAYING_EVENT.
          - When playing_flag goes from 1 to 0, sends SPOTIFY_CONNECT_PAUSED_EVENT.
        """
        # Initialize previous states.
        self.update_flags()
        prev_active = self.active
        prev_playing = self.playing
        oradio_log.info("Starting flag monitoring.")
        try:
            while True:
                # Save previous state.
                prev_active, prev_playing = self.active, self.playing
                # Update current state.
                self.update_flags()

                # Check for changes in the active flag.
                if prev_active != self.active:
                    if self.active:
                        self.send_event(SPOTIFY_CONNECT_CONNECTED_EVENT)
                    else:
                        self.send_event(SPOTIFY_CONNECT_DISCONNECTED_EVENT)

                # Check for changes in the playing flag.
                if prev_playing != self.playing:
                    if self.playing:
                        self.send_event(SPOTIFY_CONNECT_PLAYING_EVENT)
                    else:
                        self.send_event(SPOTIFY_CONNECT_PAUSED_EVENT)

    #            oradio_log.info("Active Flag: %s | Playing Flag: %s", self.active, self.playing)
                time.sleep(interval)
        except KeyboardInterrupt:
            oradio_log.info("Monitoring stopped.")

if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # For testing purposes, create a message queue using multiprocessing.
    msg_queue = Queue()
    spotify = SpotifyConnect(message_queue=msg_queue)

    # Interactive test menu for play and pause commands.
    while True:
        print("\nSelect an option:")
        print("1. Play (100% volume)")
        print("2. Pause (0% volume)")
        print("q. Quit")
        choice = input("Enter your choice: ").strip()
        if choice == "1":
            spotify.play()
        elif choice == "2":
            spotify.pause()
        elif choice.lower() == "q":
            print("Exiting test mode.")
            break
        else:
            print("Invalid option. Please try again.")

# Restore checking or duplicate code
# pylint: enable=duplicate-code
