#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 27, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio Volume Control
    Tracks the analog volume knob via the MCP3021 ADC over I2C, maps its
    position to a percentage, and updates the ALSA master volume control
    whenever the knob moves significantly. Also sets the initial default
    volumes for MPD, Spotify, and system sounds. Publishes a single
    volume-changed message per "turn" of the knob. Notifications are
    automatically disarmed while the knob is moving and re-armed once it
    settles, so other components can react to knob movement without
    polling it themselves and without being flooded during a single turn.
"""
##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from i2c_service import I2CService
from utilities import run_shell_script, ThreadTemplate
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    VOLUME_SOURCE,
    VOLUME_CHANGED,
    VOLUME_START_FAILED,
    VOLUME_SET_FAILED,
    VOLUME_STOPPED,
)

##### LOCAL constants #####################################
# Volume scaling and clamping units
ADC_MIN   = 0
ADC_MAX   = 1023
VOL_MIN   = "50%"       # 104
VOL_MAX   = "100%"      # 207

# ALSA volume controls
VOLUME_CONTROL_MPD       = "VolumeMPD"
VOLUME_CONTROL_SPOTIFY   = "VolumeSpotCon2"
VOLUME_CONTROL_SYS_SOUND = "VolumeSysSound"
VOLUME_CONTROL_MASTER    = "Digital Playback Volume"

# Default source volume levels
DEFAULT_VOLUME_MPD       = "100%"
DEFAULT_VOLUME_SPOTIFY   = "100%"
DEFAULT_VOLUME_SYS_SOUND = "90%"

# MCP3021 - A/D Converter
MCP3021_ADDRESS      = 0x4D
READ_DATA_REGISTER   = 0x00
ADC_UPDATE_TOLERANCE = 5
POLLING_MIN_INTERVAL = 0.05
POLLING_MAX_INTERVAL = 0.3
POLLING_STEP         = 0.01

# Dedicated idle timeout (seconds) with no significant knob movement before
# VOLUME_CHANGED notifications are re-armed. Can be tuned on its own for how
# quickly a user should notice a response after starting to turn the knob again.
REARM_IDLE_SECONDS = 1.0

@singleton
class VolumeControl(ThreadTemplate):
    """
    Singleton tracking ADC volume knob, updating on significant volume changes.

    Built on ThreadTemplate, which provides the restartable
    setup()/do_work()/teardown() background-thread machinery (safe_start(),
    safe_stop(), crash detection, etc.), so this class only needs to implement
    the volume-specific behaviour: setup() establishes the starting knob
    position, do_work() is one polling iteration, and the adaptive polling
    interval (fast while the knob is turning, slow while idle) is implemented
    by mutating self._interval from within do_work().
    """

    def __init__(self) -> None:
        """
        Initialise the ThreadTemplate base, default volume levels and the
        I²C service.

        Construction only sets up internal state; the background polling
        thread is not started until start() is called explicitly, mirroring
        ThreadTemplate's own separation between construction and
        safe_start(). This lets callers control exactly when polling
        begins (and stop()/start() again later) rather than having it
        begin as a side effect of construction.
        """
        # interval is controlled in setup() and do_work(); called first so
        # ThreadTemplate's own state exists before any subsequent hardware
        # I/O below could plausibly fail partway through.
        super().__init__(name="VolumeControl")

        # Set default MPD volume
        self._set_volume(VOLUME_CONTROL_MPD, DEFAULT_VOLUME_MPD)

        # Set default Spotify volume
        self._set_volume(VOLUME_CONTROL_SPOTIFY, DEFAULT_VOLUME_SPOTIFY)

        # Set default system sounds volume
        self._set_volume(VOLUME_CONTROL_SYS_SOUND, DEFAULT_VOLUME_SYS_SOUND)

        # Get I2C r/w methods
        self._i2c_service = I2CService()

        # Arm notification so the first volume change triggers a message.
        # Automatically disarmed after a change and re-armed once the knob
        # settles again (see do_work()).
        self._armed = True

        # Last-seen ADC value, carried across do_work() calls;
        # (re)established in setup() at the start of each run.
        self._previous_adc: int = 0

        # Accumulated idle time (seconds) with no significant knob movement,
        # used to re-arm notifications independently of the polling backoff.
        # (Re)established in setup() at the start of each run.
        self._idle_seconds: float = 0.0

##### Helpers #############################################

    def _read_adc(self) -> int | None:
        """
        Read a 10-bit value from the MCP3021 ADC.

        Returns:
            ADC value in range 0..1023, or None if reading fails.
        """
        # Get ADC value - volume knob position
        data = self._i2c_service.read_block(MCP3021_ADDRESS, READ_DATA_REGISTER, 2)
        if not data:
            return None

        # Combine the 2 bytes into a 10-bit value
        return ((data[0] & 0x3F) << 6) | (data[1] >> 2)

    def _adc2volume(self, adc: int) -> int:
        """
        Map ADC value from range [ADC_MIN, ADC_MAX] to [VOL_MIN, VOL_MAX].
        Result is clamped to 0..100 and rounded to the nearest integer.

        Args:
            adc: ADC reading representing the volume knob position.

        Returns:
            Volume level in the range [VOL_MIN, VOL_MAX] (currently 50..100).
        """
        raw = int(VOL_MIN[:-1]) + (adc - ADC_MIN) * (int(VOL_MAX[:-1]) - int(VOL_MIN[:-1])) / (ADC_MAX - ADC_MIN)
        return max(0, min(100, round(raw)))

    def _set_volume(self, control: str, volume: str) -> None:
        """
        Change volume for the given ALSA control.

        Args:
            control: The ALSA volume control to update.
            volume: The volume to set as a percentage string (e.g. "75%").
                    Must be in the range 0..100; negative values are rejected.
        """
        # Check if volume is given as percentage and in 0..100 range
        if not (isinstance(volume, str) and volume.endswith('%') and volume[:-1].isdigit() and 0 <= int(volume[:-1]) <= 100):
            oradio_log.error("Invalid volume '%s'", volume)
            return

        # Set volume
        cmd = f"amixer -c 0 cset name='{control}' {volume}"
        result, response = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error setting volume: %s", response)
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_SET_FAILED))
        else:
            oradio_log.debug("Volume of '%s' set to: %s", control, volume)

##### ThreadTemplate overrides ############################

    def setup(self) -> None:
        """
        One-time init for this run: read the knob's current position and set
        the master volume to match it before polling begins. Also (re)sets
        the polling interval to its slow/idle starting value and clears the
        dedicated re-arm idle timer.

        Raises:
            RuntimeError: If the initial ADC read fails, since the volume
                manager cannot start without a valid starting position.
                ThreadTemplate.run() catches this, logs it, and stores it
                on .exception rather than letting it escape.
        """
        previous_adc = self._read_adc()
        if previous_adc is None:
            raise RuntimeError("ADC read failed on startup, volume manager cannot start")
        self._previous_adc = previous_adc

        # Set master volume in line with position of the volume knob
        volume = self._adc2volume(previous_adc)
        self._set_volume(VOLUME_CONTROL_MASTER, f"{volume}%")

        # Start with 'slow' polling
        self._interval = POLLING_MAX_INTERVAL

        # Reset the dedicated re-arm idle timer for this run
        self._idle_seconds = 0.0

    def do_work(self) -> None:
        """
        One polling iteration: read the knob, update the master volume on a
        significant change, and adapt the polling interval: fast while the
        knob is turning, easing back down to idle otherwise.

        Only one VOLUME_CHANGED message is published per "turn": the first
        significant change disarms further notifications, and they are
        automatically re-armed once the knob has been idle (no significant
        movement) for REARM_IDLE_SECONDS. This idle timer is dedicated and
        independent of the polling backoff curve, so the re-arm delay can be
        tuned on its own without affecting polling responsiveness. This
        avoids flooding VOLUME_CHANGED messages while the knob is still
        being turned, without needing an external caller to re-arm it.

        The adaptive interval is implemented by mutating self._interval;
        ThreadTemplate's run() loop reads it fresh after each do_work() call
        to decide how long to wait before the next one.
        """
        adc_value = self._read_adc()
        if adc_value is None:
            oradio_log.warning("ADC read failed. Retrying...")
            return

        # Check if knob moved significantly
        if abs(adc_value - self._previous_adc) > ADC_UPDATE_TOLERANCE:
            self._previous_adc = adc_value

            # Convert ADC reading to volume level
            volume = self._adc2volume(adc_value)

            # Set master volume in line with position of the volume knob
            self._set_volume(VOLUME_CONTROL_MASTER, f"{volume}%")

            # Disarmed until the knob settles again, preventing repeated
            # notifications while it's still being turned.
            if self._armed:
                self._armed = False
                oradio_log.debug("Send volume changed message")
                Commands.publish(CommandMessage(VOLUME_SOURCE, VOLUME_CHANGED))

            # Movement detected: reset the idle timer and poll fast again.
            self._idle_seconds = 0.0
            self._interval = POLLING_MIN_INTERVAL     # Fast polling while turning
        else:
            # No significant movement this cycle: accumulate the real time
            # elapsed since the previous poll (i.e. the interval we just
            # waited) toward the dedicated re-arm idle timer.
            self._idle_seconds += self._interval

            # Re-arm once the knob has been idle for the dedicated timeout,
            # independent of where the polling backoff curve currently is.
            if not self._armed and self._idle_seconds >= REARM_IDLE_SECONDS:
                self._armed = True
                oradio_log.debug("Volume knob settled, notifications re-armed")

            self._interval = min(self._interval + POLLING_STEP, POLLING_MAX_INTERVAL)

    def teardown(self) -> None:
        """Report incident: Oradio never intentionally stops volume control."""
        Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_STOPPED))

##### Public API ##########################################

    def start(self) -> None:
        """
        Start the background polling thread.

        Thin wrapper around ThreadTemplate.safe_start() that preserves this
        class's original public API. Idempotent: calling start() when the
        thread is already alive is a no-op.
        """
        if self.is_alive():
            oradio_log.debug("Volume manager thread already running")
            return

        if not self.safe_start():
            oradio_log.error("Volume manager thread failed to start")
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_START_FAILED))
            return

        if self.crashed:
            oradio_log.error("Volume manager thread crashed during startup: %s", self.exception)
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_START_FAILED))
            return

        oradio_log.info("Volume manager thread started")

    def stop(self) -> None:
        """
        Signal the background polling thread to stop and wait for it to exit.

        Thin wrapper around ThreadTemplate.safe_stop() that preserves this
        class's original public API. The stop incident itself is published
        by teardown(), which always runs when the polling loop exits.
        """
        self.safe_stop()

##### Stand-alone entry point #############################

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu():
        """
        Run an interactive self-test menu for the volume control.
        """

        # Show menu with test options
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start volume control\n"
            " 2-Stop volume control\n"
            "Select: "
        )

        # Initialise volume control
        volume_control = VolumeControl()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print("\nStarting volume control...")
                    print("Turn volume knob to observe changes")
                    volume_control.start()
                case 2:
                    print("\nStopping volume control...")
                    volume_control.stop()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

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

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
