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
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
@summary:  Spotify Connect
    The librespot audio Spotify Connect is (un)muted when Oradio on/off.
    Connection stays active and music keeps streaming. The status of the
    Librespot connection is monitored via Librespot events which write
    status into two files: spotactive.flag and spotplaying.flag.
"""
import subprocess

##### Oradio modules ######################################
from log_service import oradio_log
from utilities import ThreadTemplate
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
    SPOTIFY_FAILED,
    SPOTIFY_STOPPED,
)

##### LOCAL constants #####################################
ALSA_MIXER_SPOTCON = "VolumeSpotCon1"
ACTIVE_FLAG_FILE   = "/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE  = "/home/pi/Oradio3/Spotify/spotplaying.flag"
MONITOR_INTERVAL   = 0.5  # seconds between flag file polls

class _SpotifyMonitorWorker(ThreadTemplate):
    """
    Background worker that polls the Librespot flag files and publishes
    connect/play state-change events.

    One instance is created per SpotifyConnect object (see
    SpotifyConnect.__init__) and reused across repeated start()/stop()
    cycles: ThreadTemplate itself is restartable, so a single
    _SpotifyMonitorWorker instance can be safe_start()ed and safe_stop()ped
    any number of times.
    """
    def __init__(self, spotify: "SpotifyConnect") -> None:
        super().__init__(interval=MONITOR_INTERVAL, name="SpotifyMonitorWorker")
        self._spotify = spotify
        # Snapshot of active/playing from the previous do_work() iteration,
        # used to detect transitions. Set in setup(), updated in do_work().
        self._prev_active = False
        self._prev_playing = False

    def setup(self) -> None:
        """
        Take the initial flag reading before the poll loop (re)begins, so
        spotify.active/spotify.playing are already valid by the time
        safe_start() returns, and so the first do_work() pass doesn't fire
        spurious transition events for the startup state.
        """
        oradio_log.info("SpotifyConnect: starting flag monitoring.")
        self._spotify.update_flags()
        self._prev_active = self._spotify.active
        self._prev_playing = self._spotify.playing

    def do_work(self) -> None:
        """
        One poll iteration: re-read the flag files and publish events for
        any active/playing transition since the previous iteration.

        Transitions detected:
        - active  0->1: SPOTIFY_CONNECTED_EVENT
        - active  1->0: SPOTIFY_DISCONNECTED_EVENT
        - playing 0->1: SPOTIFY_PLAYING_EVENT
        - playing 1->0: SPOTIFY_PAUSED_EVENT
        """
        self._prev_active = self._spotify.active
        self._prev_playing = self._spotify.playing
        self._spotify.update_flags()

        if self._prev_active != self._spotify.active:
            if self._spotify.active:
                Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_CONNECTED_EVENT))
            else:
                Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_DISCONNECTED_EVENT))

        if self._prev_playing != self._spotify.playing:
            if self._spotify.playing:
                Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_PLAYING_EVENT))
            else:
                Commands.publish(CommandMessage(SPOTIFY_SOURCE, SPOTIFY_PAUSED_EVENT))

    def teardown(self) -> None:
        """Report incident: Oradio never intentionally stops Spotify Monitor."""
        Incidents.publish(IncidentMessage(SPOTIFY_SOURCE, SPOTIFY_STOPPED))

class SpotifyConnect:
    """
    Basic Spotify functionality based on Librespot service.
    - Monitors the Librespot active/playing state via flag files.
    - Supports muting/unmuting the ALSA output channel.
    - Runs the flag monitoring in a background worker; start()/stop() can
      be called repeatedly since the worker (ThreadTemplate) is restartable.
    """

    def __init__(self):
        """
        Initialize SpotifyConnect. Does NOT start monitoring automatically
        -- call start() to begin polling the flag files.
        """
        self.active = False
        self.playing = False

        # Track whether we've already warned about a missing/unreadable file
        self._warned_missing = {
            ACTIVE_FLAG_FILE: False,
            PLAYING_FLAG_FILE: False,
        }

        # Created once; safe_start()/safe_stop() can be called on it
        # repeatedly since ThreadTemplate itself supports restarting.
        self._worker = _SpotifyMonitorWorker(self)

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

    # ---------- Monitor thread control ----------

    def start(self) -> None:
        """
        Start the flag-monitoring worker if not already running.
        Blocks until the worker signals readiness or a timeout occurs.

        Note:
            Safe to call after a previous stop() -- the underlying worker
            is restartable and resumes monitoring; self.active/self.playing
            are re-read fresh in setup() before this returns.
        """
        if self._worker.is_alive():
            oradio_log.debug("SpotifyConnect monitor thread already running")
            return

        if not self._worker.safe_start():
            oradio_log.error("SpotifyConnect monitor thread failed to start")
            Incidents.publish(IncidentMessage(SPOTIFY_SOURCE, SPOTIFY_FAILED))
            return

        if self._worker.crashed:
            oradio_log.error("SpotifyConnect monitor thread crashed during startup: %s", self._worker.exception)
            Incidents.publish(IncidentMessage(SPOTIFY_SOURCE, SPOTIFY_FAILED))
            return

        oradio_log.info("SpotifyConnect monitor thread started")

    def stop(self) -> None:
        """
        Stop the flag-monitoring worker and wait for it to terminate.

        Note:
            The worker can be resumed later via start().
        """
        if not self._worker.is_alive():
            oradio_log.debug("SpotifyConnect monitor thread not running")
            return

        if not self._worker.safe_stop():
            oradio_log.error("SpotifyConnect monitor thread did not stop cleanly")
        else:
            oradio_log.info("SpotifyConnect monitor thread stopped")

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the Spotify Connect service.

        Instantiates SpotifyConnect and loops until the user selects quit (0).
        Options cover starting/stopping the flag monitor and mute/unmute
        (ALSA channel control).
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Start flag monitor\n"
            " 2-Stop flag monitor\n"
            " 3-Unmute (100% volume)\n"
            " 4-Mute (0% volume)\n"
            "Select: "
        )

        spotify = SpotifyConnect()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print("Starting flag monitor...")
                    spotify.start()
                case 2:
                    print("Stopping flag monitor...")
                    spotify.stop()
                case 3:
                    spotify.unmute()
                case 4:
                    spotify.mute()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

        # Make sure the monitor thread isn't left running when the menu exits
        spotify.stop()

    print("\nStarting test program...\n")

    # Subscribe to command and error topics so published messages are printed to console
    command_handler = DebugMessageHandler(Commands.subscribe())
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Commands.unsubscribe(command_handler.get_queue())
    Incidents.unsubscribe(incident_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    command_handler.stop()
    incident_handler.stop()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
