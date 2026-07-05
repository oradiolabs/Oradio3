#!/usr/bin/env python3

"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on February 1, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@summary:  Spotify Connect

The librespot audio Spotify Connect is (un)muted when Oradio on/off.
Connection stays active and music keeps streaming. The status of the Librespot
connection is monitored via Librespot events which write status into two files:
spotactive.flag and spotplaying.flag.
"""
import time
import subprocess
from threading import Thread

##### Oradio modules ######################################
from log_service import oradio_log
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    SPOTIFY_SOURCE,
    SPOTIFY_CONNECTED_EVENT,
    SPOTIFY_DISCONNECTED_EVENT,
    SPOTIFY_PLAYING_EVENT,
    SPOTIFY_PAUSED_EVENT,
    SPOTIFY_INCIDENT_MONITOR,
)

##### LOCAL constants #####################################
ALSA_MIXER_SPOTCON = "VolumeSpotCon1"
ACTIVE_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE = "/home/pi/Oradio3/Spotify/spotplaying.flag"

class SpotifyConnect:
    """Basic Spotify functionality based on Librespot service."""

    def __init__(self):
        """
        Initialize SpotifyConnect and start background flag monitoring.

        Publishes command events to oradio_control.py via the Commands bus
        whenever the Librespot active/playing state changes.
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

        try:
            self.monitor_thread.start()
            oradio_log.info("SpotifyConnect: monitor thread started.")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("SpotifyConnect: monitor thread failed to start: %s", ex_err)
            Incidents.publish(IncidentMessage(SPOTIFY_SOURCE, SPOTIFY_INCIDENT_MONITOR))

    def _read_flag(self, filepath: str) -> bool:
        """
        Return True iff the file's trimmed content is '1'.

        If the file is missing or unreadable, return False and log a
        one-time INFO message. Once the file becomes readable again,
        the warning latch is cleared so the message fires once more
        if it disappears a second time.
        """
        try:
            with open(filepath, encoding="utf-8") as flag_file:
                value = flag_file.read().strip() == "1"
            if self._warned_missing.get(filepath, False):
                oradio_log.info("Flag file %s available again.", filepath)
                self._warned_missing[filepath] = False
            return value

        except (FileNotFoundError, OSError) as ex:
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

    def mute(self) -> None:
        """Mute Spotify Connect by setting the ALSA channel to 0%.

        This is an ALSA-level volume operation only; it does not pause
        Librespot playback or affect the Spotify Connect session.
        """
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

    def unmute(self) -> None:
        """Unmute Spotify Connect by setting the ALSA channel to 100%.

        This is an ALSA-level volume operation only; it does not resume
        Librespot playback or affect the Spotify Connect session.
        """
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

    def get_state(self) -> dict[str, bool]:
        """Return current state as {'active': bool, 'playing': bool}.

        Note: self.active and self.playing are written by the monitor
        thread. Reads here are safe under CPython's GIL (attribute
        assignment is atomic), but callers must not assume the values
        are perfectly in sync with one another.
        """
        return {"active": self.active, "playing": self.playing}

    # ---------- Monitor loop ----------

    def monitor_flags(self, interval: float = 0.5) -> None:
        """
        Continuously monitor flag files and publish events on state changes.

        Transitions detected:
        - active  0→1: SPOTIFY_CONNECTED_EVENT
        - active  1→0: SPOTIFY_DISCONNECTED_EVENT
        - playing 0→1: SPOTIFY_PLAYING_EVENT
        - playing 1→0: SPOTIFY_PAUSED_EVENT

        Runs until the daemon thread is terminated by the process exiting.
        """
        oradio_log.info("SpotifyConnect: starting flag monitoring.")

        while True:
            prev_active = self.active       # snapshot before update
            prev_playing = self.playing
            self.update_flags()             # now self.active/playing may differ

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

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the Spotify Connect service.

        Instantiates SpotifyConnect and loops until the user selects quit (0).
        Options cover mute/unmute (ALSA channel control).
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Unmute (100% volume)\n"
            " 2-Mute (0% volume)\n"
            "Select: "
        )

        spotify = SpotifyConnect()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    spotify.unmute()
                case 2:
                    spotify.mute()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to command and error topics so published messages are printed to console
    cmd_handler = DebugMessageHandler(Commands.subscribe())
    err_handler = DebugMessageHandler(Incidents.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Commands.unsubscribe(cmd_handler.get_queue())
    Incidents.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    cmd_handler.stop()
    err_handler.stop()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
