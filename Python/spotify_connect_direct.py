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
@summary:  Spotify Connect

The librespot audio Spotify Connect is (un) muted when Oradio on/off.
Connection stays active and music streaming. The status of the Librespot
connection is monitored via Librespot events which put status in two files:
spotactive.flag and spotplaying.flag.
"""

import time
import subprocess
import threading
from multiprocessing import Queue
from queue import Full  # for queue-full errors in send_event

# Oradio modules
from oradio_logging import oradio_log

# Constants from oradio_const
from oradio_const import (
    MESSAGE_NO_ERROR,
    MESSAGE_SPOTIFY_SOURCE,
    SPOTIFY_CONNECT_CONNECTED_EVENT,
    SPOTIFY_CONNECT_DISCONNECTED_EVENT,
    SPOTIFY_CONNECT_PLAYING_EVENT,
    SPOTIFY_CONNECT_PAUSED_EVENT,
)

# Local constants
ALSA_MIXER_SPOTCON = "VolumeSpotCon1"
ACTIVE_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotplaying.flag"


class SpotifyConnect:
    """Basic Spotify functionality based on Librespot service."""

    def __init__(self, message_queue=None):
        """
        Initialize with a message queue used to send events to oradio_control.py.
        Starts monitoring flags in a separate thread.
        """
        self.active = False
        self.playing = False
        self.message_queue = message_queue

        # Track whether we've already warned about a missing/unreadable file
        self._warned_missing = {
            ACTIVE_FLAG_FILE: False,
            PLAYING_FLAG_FILE: False,
        }

        # Start monitor_flags in a separate daemon thread.
        self.monitor_thread = threading.Thread(target=self.monitor_flags, daemon=True)
        self.monitor_thread.start()
        oradio_log.info("SpotifyConnect: monitor thread started.")

    # ---------- Flag reading ----------

    def _read_flag(self, filepath: str) -> bool:
        """
        Read 'filepath' and return True if its trimmed content is '1', else False.
        If the file is missing or unreadable, return False and warn only once.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                return file.read().strip() == "1"
        except FileNotFoundError:
            if not self._warned_missing.get(filepath, False):
                oradio_log.warning(
                    "Flag file %s not found; treating as '0' until it appears.", filepath
                )
                self._warned_missing[filepath] = True
            return False
        except OSError as ex_err:
            if not self._warned_missing.get(filepath, False):
                oradio_log.warning(
                    "Could not read flag %s (%s); treating as '0' until readable.",
                    filepath,
                    ex_err,
                )
                self._warned_missing[filepath] = True
            return False

    def update_flags(self) -> None:
        """Update 'active' and 'playing' by reading their flag files."""
        self.active = self._read_flag(ACTIVE_FLAG_FILE)
        self.playing = self._read_flag(PLAYING_FLAG_FILE)

    # ---------- Events to oradio_control ----------

    def send_event(self, event: str) -> None:
        """
        Send an event via the message queue. The message dict contains:
          - 'source': MESSAGE_SPOTIFY_SOURCE
          - 'state' : the event (string)
          - 'error' : MESSAGE_NO_ERROR
        """
        if not self.message_queue:
            oradio_log.error("SpotifyConnect: message queue not set; cannot send event.")
            return

        try:
            message = {
                "source": MESSAGE_SPOTIFY_SOURCE,
                "state": event,
                "error": MESSAGE_NO_ERROR,
            }
            self.message_queue.put(message)
            oradio_log.info("SpotifyConnect: message sent to queue: %s", message)
        except Full:
            oradio_log.error("SpotifyConnect: message queue is full; event dropped.")
        except (OSError, ValueError) as ex_err:
            oradio_log.error("SpotifyConnect: error sending event to queue: %s", ex_err)

    # ---------- Control (mute/unmute) ----------

    def play(self) -> None:
        """Unmute Spotify Connect by setting ALSA channel to 100%."""
        try:
            subprocess.run(
                ["amixer", "-c", "DigiAMP", "sset", ALSA_MIXER_SPOTCON, "100%"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            oradio_log.info("SpotifyConnect: unmuted via amixer.")
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("SpotifyConnect: error unmuting via amixer: %s", ex_err)

    def pause(self) -> None:
        """Mute Spotify Connect by setting ALSA channel to 0%."""
        try:
            subprocess.run(
                ["amixer", "-c", "DigiAMP", "sset", ALSA_MIXER_SPOTCON, "0%"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            oradio_log.info("SpotifyConnect: muted via amixer.")
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("SpotifyConnect: error muting via amixer: %s", ex_err)

    def get_state(self) -> dict:
        """Return current state as a dict: {'active': bool, 'playing': bool}."""
        return {"active": self.active, "playing": self.playing}

    # ---------- Monitor loop ----------

    def monitor_flags(self, interval: float = 0.5) -> None:
        """
        Continuously monitor the flag files and send events when values change.

        - active  0→1: SPOTIFY_CONNECT_CONNECTED_EVENT
        - active  1→0: SPOTIFY_CONNECT_DISCONNECTED_EVENT
        - playing 0→1: SPOTIFY_CONNECT_PLAYING_EVENT
        - playing 1→0: SPOTIFY_CONNECT_PAUSED_EVENT
        """
        self.update_flags()
        prev_active = self.active
        prev_playing = self.playing
        oradio_log.info("SpotifyConnect: starting flag monitoring.")

        try:
            while True:
                prev_active, prev_playing = self.active, self.playing
                self.update_flags()

                if prev_active != self.active:
                    if self.active:
                        self.send_event(SPOTIFY_CONNECT_CONNECTED_EVENT)
                    else:
                        self.send_event(SPOTIFY_CONNECT_DISCONNECTED_EVENT)

                if prev_playing != self.playing:
                    if self.playing:
                        self.send_event(SPOTIFY_CONNECT_PLAYING_EVENT)
                    else:
                        self.send_event(SPOTIFY_CONNECT_PAUSED_EVENT)

                time.sleep(interval)
        except KeyboardInterrupt:
            oradio_log.info("SpotifyConnect: monitoring stopped.")


if __name__ == "__main__":
    # Stand-alone test harness
    msg_queue = Queue()
    spotify = SpotifyConnect(message_queue=msg_queue)

    # Simple interactive test for amixer control
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
