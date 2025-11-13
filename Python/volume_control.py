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
# The alsaaudio module is a C extension, which pylint often analyze correctly.
# So, pylint thinks the names don’t exist, even though at runtime they do.
from alsaaudio import Mixer, ALSAAudioError, VOLUME_UNITS_RAW   # pylint: disable=no-name-in-module

##### oradio modules ####################
from oradio_logging import oradio_log
from i2c_service import I2CService
from oradio_utils import safe_put

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, NC,
    MESSAGE_VOLUME_SOURCE,
    MESSAGE_VOLUME_CHANGED,
    MESSAGE_NO_ERROR,
)

##### Local constants ####################
# Raw volume units
VOLUME_MINIMUM = 105
VOLUME_MAXIMUM = 215
# MCP3021 - A/D Converter
MCP3021_ADDRESS      = 0x4D
READ_DATA_REGISTER   = 0x00
ADC_UPDATE_TOLERANCE = 5
POLLING_MIN_INTERVAL = 0.05
POLLING_MAX_INTERVAL = 0.3
POLLING_STEP         = 0.01
ALSA_MIXER_DIGITAL   = "Digital"
# Timeout for thread to respond (seconds)
THREAD_TIMEOUT = 3

# ALSA abstraction
class AlsaVolume:
    """
    Wrapper class for ALSA Mixer.
    
    Handles setting volume in raw units safely and avoids unnecessary ALSA calls.
    """
    def __init__(self, mixer_name: str = ALSA_MIXER_DIGITAL) -> None:
        try:
            self.mixer = Mixer(mixer_name)
        except ALSAAudioError as ex_err:
            oradio_log.error("Error initializing ALSA mixer '%s': %s", mixer_name, ex_err)
            self.mixer = None  # ALSA not available
        self._last_set_raw = None  # Cache last set value to prevent redundant ALSA calls

    def set(self, raw_value: int) -> None:
        """
        Set mixer volume in raw units.
        
        Args:
            raw_value: The raw volume value to set.
        """
        if not self.mixer:
            return  # Mixer unavailable

        # Clamp value within allowed min/max range
        clamped = max(VOLUME_MINIMUM, min(VOLUME_MAXIMUM, raw_value))

        # Skip ALSA call if value hasn't changed
        if self._last_set_raw == clamped:
            return

        try:
            self.mixer.setvolume(clamped, units=VOLUME_UNITS_RAW)
        except ALSAAudioError as ex_err:
            oradio_log.error("Error setting ALSA volume: %s", ex_err)
        else:
            self._last_set_raw = clamped
            oradio_log.debug("Volume set to: %s", clamped)

class VolumeControl:
    """
    Tracks an ADC volume knob, updates ALSA, and triggers a callback on significant changes.
    """

    def __init__(self, queue) -> None:
        """
        Initialize I²C bus, callback and mixer.

        Args:
            on_change: optional zero-argument callback that will be invoked
                       when a significant volume change is detected. Keep
                       the callback tiny and non-blocking.
        """
        # Get I2C r/w methods
        self._i2c_service = I2CService()

        # Store queue for sending volume change messages asynchronously
        self._queue = queue

        # Start ready to send notification
        self._armed = True

        # ALSA wrapper
        self._alsa = AlsaVolume()

        # Thread is created dynamically on `start()` to allow restartability
        self._running = Event()
        self._thread = None

        # Start volume manager thread
        self.start()

# -----Helper methods----------------

    def set_notify(self) -> None:
        """Allow notification to happen."""
        self._armed = True

    def _read_adc(self) -> int | None:
        """
        Read a 10-bit value from the MCP3021 ADC.
        
        Returns:
            ADC value 0..1023, or None if reading fails.
        """
        # Get ADC value - volume knob position
        data = self._i2c_service.read_block(MCP3021_ADDRESS, READ_DATA_REGISTER, 2)
        if not data:
            return None

        # Combine the 2 bytes into a 10-bit value
        return ((data[0] & 0x3F) << 6) | (data[1] >> 2)

    def _set_volume(self, adc_value: int) -> None:
        """
        Update ALSA volume based on ADC reading and trigger callback.
        
        Args:
            adc_value: Current ADC reading
        """
        if adc_value is None:
            return

        # Scale ADC (0..1023) to [VOLUME_MINIMUM..VOLUME_MAXIMUM]
        span = VOLUME_MAXIMUM - VOLUME_MINIMUM
        volume = int(round(VOLUME_MINIMUM + (adc_value * span) / 1023))

        # Set ALSA volume
        self._alsa.set(volume)

# -----Core methods----------------

    def _volume_manager(self) -> None:
        """
        Thread function: continuously polls ADC and updates volume.
        - Adaptive polling for faster response when the knob is turned and slower idle polling.
        """
        # Initialize ALSA to knob's current position
        previous_adc = self._read_adc()
        if previous_adc is None:
            oradio_log.error("ADC read failed")
        self._set_volume(previous_adc)  # None is ignored

        # Start with 'slow' polling
        polling_interval = POLLING_MAX_INTERVAL

        # signal: start volume manager thread
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


                # Set volume level
                self._set_volume(adc_value)

                # Notify only once
                if self._armed:
                    self._armed = False

                    # Create message
                    message = {
                        "source": MESSAGE_VOLUME_SOURCE,
                        "state" : MESSAGE_VOLUME_CHANGED,
                        "error" : MESSAGE_NO_ERROR
                    }

                    # Put message in queue
                    oradio_log.debug("Send volume changed message: %s", message)
                    safe_put(self._queue, message)

                polling_interval = POLLING_MIN_INTERVAL     # Fast polling while turning
            else:
                polling_interval = min(polling_interval + POLLING_STEP, POLLING_MAX_INTERVAL)

            sleep(polling_interval)

# -----Public methods----------------

    def start(self) -> None:
        """Start the volume control thread if not already running."""
        if self._thread and self._thread.is_alive():
            oradio_log.debug("Volume manager thread already running")
            return

        # Create and start thread
        self._thread = Thread(target=self._volume_manager, daemon=True)
        self._thread.start()

        # Check if thread started
        if self._running.wait(timeout=THREAD_TIMEOUT):
            oradio_log.info("Volume manager thread started")
        else:
            oradio_log.error("Timed out: Volume manager thread not started")

    def stop(self) -> None:
        """Stop the volumne control thread and wait for it to terminate."""
        if not self._thread or not self._thread.is_alive():
            oradio_log.debug("Volume manager thread not running")
            return

        # signal: stop volume manager thread
        self._running.clear()

        # Avoid hanging forever if the thread is stuck in I/O
        self._thread.join(timeout=THREAD_TIMEOUT)

        if self._thread.is_alive():
            oradio_log.error("Join timed out: volume manager thread is still running")
        else:
            oradio_log.info("Volume manager thread stopped")

# Entry point for stand-alone operation
if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from multiprocessing import Process, Queue

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    def interactive_menu(queue):
        """Show menu with test options"""

        # Show menu with test options
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start volume control\n"
            " 2-Stop volume control\n"
            " 3-Set volume knob notification\n"
            "Select: "
        )

        # Initialise backlighting
        volume_control = VolumeControl(queue)

        # User command loop
        while True:
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
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

    print("\nStarting VolumeControl test program...\n")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Present menu with tests
    interactive_menu(message_queue)

    # Stop listening to messages
    message_listener.terminate()

    print("\nExiting VolumeControl test program...\n")

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
