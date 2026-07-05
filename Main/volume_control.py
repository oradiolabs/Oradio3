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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio Volume control
@references:
"""
from time import sleep
from threading import Thread, Event

##### Oradio modules ######################################
from log_service import oradio_log
from i2c_service import I2CService
from utilities import run_shell_script
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    VOLUME_SOURCE,
    VOLUME_CHANGED,
    VOLUME_INCIDENT_START,
    VOLUME_INCIDENT_STOP,
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
# Timeout for thread to respond (seconds)
THREAD_TIMEOUT = 3

class VolumeControl:
    """
    Tracks an ADC volume knob, updates volume, and triggers a callback on significant changes.
    """

    def __init__(self) -> None:
        """
        Initialize default volume levels, I²C service, and start the volume manager thread.
        """
        # Set default MPD volume
        self._set_volume(VOLUME_CONTROL_MPD, DEFAULT_VOLUME_MPD)

        # Set default Spotify volume
        self._set_volume(VOLUME_CONTROL_SPOTIFY, DEFAULT_VOLUME_SPOTIFY)

        # Set default system sounds volume
        self._set_volume(VOLUME_CONTROL_SYS_SOUND, DEFAULT_VOLUME_SYS_SOUND)

        # Get I2C r/w methods
        self._i2c_service = I2CService()

        # Arm notification so the first volume change triggers a message
        self._armed = True

        # Thread is created dynamically on start() to allow restartability
        self._running = Event()
        self._thread: Thread | None = None

        # Start volume manager thread
        self.start()

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
        else:
            oradio_log.debug("Volume of '%s' set to: %s", control, volume)

##### Core ################################################

    def _volume_manager(self) -> None:
        """
        Thread function: continuously polls ADC and updates volume.
        - Adaptive polling for faster response when the knob is turned and slower idle polling.

        Note: _running serves two purposes: signals thread readiness on set(),
        and stops the loop on clear().
        """
        # Initialize volume to knob's current position
        previous_adc = self._read_adc()
        if previous_adc is None:
            oradio_log.error("ADC read failed on startup, volume manager cannot start")
            return

        # Convert ADC reading to volume level
        volume = self._adc2volume(previous_adc)

        # Set master volume in line with position of the volume knob
        self._set_volume(VOLUME_CONTROL_MASTER, f"{volume}%")

        # Start with 'slow' polling
        polling_interval = POLLING_MAX_INTERVAL

        # Signal that the volume manager thread is ready
        self._running.set()

        # Volume adjustment loop
        while self._running.is_set():

            # Get knob's current position
            adc_value = self._read_adc()
            if adc_value is None:
                oradio_log.warning("ADC read failed. Retrying...")
                sleep(polling_interval)
                continue

            # Check if knob moved significantly
            if abs(adc_value - previous_adc) > ADC_UPDATE_TOLERANCE:
                previous_adc = adc_value

                # Convert ADC reading to volume level
                volume = self._adc2volume(adc_value)

                # Set master volume in line with position of the volume knob
                self._set_volume(VOLUME_CONTROL_MASTER, f"{volume}%")

                # Disarm until set_notify() re-arms, preventing repeated notifications
                if self._armed:
                    self._armed = False
                    oradio_log.debug("Send volume changed message")
                    Commands.publish(CommandMessage(VOLUME_SOURCE, VOLUME_CHANGED))

                polling_interval = POLLING_MIN_INTERVAL     # Fast polling while turning
            else:
                polling_interval = min(polling_interval + POLLING_STEP, POLLING_MAX_INTERVAL)

            sleep(polling_interval)

##### Public API ##########################################

    def start(self) -> None:
        """Start the volume control thread if not already running."""
        if self._thread and self._thread.is_alive():
            oradio_log.debug("Volume manager thread already running")
            return

        # Create and start thread
        self._thread = Thread(target=self._volume_manager, daemon=True)
        try:
            self._thread.start()
        except RuntimeError as ex_err:
            oradio_log.error("Volume manager thread failed to start: %s", ex_err)
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_INCIDENT_START))
            return

        if not self._running.wait(timeout=THREAD_TIMEOUT):
            oradio_log.error("Volume manager thread did not become ready in time")
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_INCIDENT_START))
            return

        oradio_log.info("Volume manager thread started")

    def stop(self) -> None:
        """Stop the volume control thread and wait for it to terminate."""
        if not self._thread or not self._thread.is_alive():
            oradio_log.debug("Volume manager thread not running")
            return

        # Signal the volume manager thread to stop
        self._running.clear()

        # Avoid hanging forever if the thread is stuck in I/O
        self._thread.join(timeout=THREAD_TIMEOUT)

        if self._thread.is_alive():
            oradio_log.error("Join timed out: volume manager thread is still running")
            Incidents.publish(IncidentMessage(VOLUME_SOURCE, VOLUME_INCIDENT_STOP))
        else:
            oradio_log.info("Volume manager thread stopped")

    def set_notify(self) -> None:
        """Re-arm the notification so the next volume change triggers a message."""
        self._armed = True

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
            " 3-Set volume knob notification\n"
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
                case 3:
                    print("\nSet volume knob notification...")
                    volume_control.set_notify()
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
