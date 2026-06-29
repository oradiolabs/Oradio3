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
from threading import Thread

##### Oradio modules ######################################
from log_service import oradio_log
from messaging import (
    SPOTIFY_SOURCE,
    SPOTIFY_CONNECTED_EVENT,
    SPOTIFY_DISCONNECTED_EVENT,
    SPOTIFY_PLAYING_EVENT,
    SPOTIFY_PAUSED_EVENT,
)

##### LOCAL constants #####################################
ALSA_MIXER_SPOTCON = "VolumeSpotCon1"
ACTIVE_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotplaying.flag"

class SpotifyConnect:
    """Basic Spotify functionality based on Librespot service."""

    def __init__(self):
        """
        Initialize with a message queue used to send events to oradio_control.py.
        Starts monitoring flags in a separate thread.
        """
        self.active = False
        self.playing = False

        # Track whether we've already warned about a missing/unreadable file
        self._warned_missing = {
            ACTIVE_FLAG_FILE: False,
            PLAYING_FLAG_FILE: False,
        }

        # Start monitor_flags in a separate daemon thread.
        self.monitor_thread = Thread(target=self.monitor_flags, daemon=True)
        self.monitor_thread.start()
        oradio_log.info("SpotifyConnect: monitor thread started.")

    def _read_flag(self, filepath: str) -> bool:
        """
        Return True iff the file's trimmed content is '1'.
        If the file is missing/unreadable, return False and log a one-time INFO.
        Once the file becomes readable again, clear the warning latch.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as flag_file:
                value = flag_file.read().strip() == "1"
            # If it was missing before and now it's back, clear the latch (optional)
            if self._warned_missing.get(filepath, False):
                oradio_log.info("Flag file %s available again.", filepath)
                self._warned_missing[filepath] = False
            return value

        except (FileNotFoundError, OSError) as ex:
            # Log only once per filepath, at INFO level (no ERRORs sent to ORMS)
            if not self._warned_missing.get(filepath, False):
                oradio_log.info(
                    "Flag file %s not readable (%s); treating as '0' until it appears.",
                    filepath,
                    ex,
                )
                self._warned_missing[filepath] = True
            return False

    def update_flags(self) -> None:
        """Update 'active' and 'playing' by reading their flag files."""
        self.active = self._read_flag(ACTIVE_FLAG_FILE)
        self.playing = self._read_flag(PLAYING_FLAG_FILE)

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
        Continuously monitor the flag files and publish events when values change.

        - active  0→1: SPOTIFY_CONNECTED
        - active  1→0: SPOTIFY_DISCONNECTED_EVENT
        - playing 0→1: SPOTIFY_PLAYING_EVENT
        - playing 1→0: SPOTIFY_PAUSED_EVENT
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
                        Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_CONNECTED_EVENT))
                    else:
                        Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_DISCONNECTED_EVENT))

                if prev_playing != self.playing:
                    if self.playing:
                        Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_PLAYING_EVENT))
                    else:
                        Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_PAUSED_EVENT))

                time.sleep(interval)
        except KeyboardInterrupt:
            oradio_log.info("SpotifyConnect: monitoring stopped.")

if __name__ == "__main__":

    print("\nStarting test program...\n")

    # Stand-alone test harness
    spotify = SpotifyConnect()

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
            break
        else:
            print("Invalid option. Please try again.")

    print("\nExiting test program...\n")
