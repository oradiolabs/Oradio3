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
from threading import Thread
#REVIEW: Is callback nodig?
from typing import Callable
from alsaaudio import Mixer, ALSAAudioError, VOLUME_UNITS_RAW

##### oradio modules ####################
from oradio_logging import oradio_log
from i2c_servicie import I2CService

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
#    MESSAGE_VOLUME_SOURCE,
#    MESSAGE_VOLUME_CHANGED,
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


class VolumeControl:
    """Tracks the volume control setting and updates ALSA; emits a callback on change."""

    # callback type (zero-arg: just "changed")
    OnChange = Callable[[], None]

    def __init__(self, on_change: OnChange | None = None) -> None:
        """
        Initialize I²C bus, callback and mixer.

        Args:
            on_change: optional zero-argument callback that will be invoked
                       when a significant volume change is detected. Keep
                       the callback tiny and non-blocking.
        """
        # Get I2C r/w methods
        self._i2c_service = I2CService()

#REVIEW: Waarom callback gebruiken?
#           - Alle tijd-kritische zaken worden lokaal afgehandeld
#           - We zouden toch messages gebruiken om tussen modules te communiceren?
#           - Wat is performance verschil tussen messages en callbacks?
        self._on_change = on_change
        self.running = True

#REVIEW: Stop ALSA functies in eigen class die, net als in mpd_service, de no-member issues oplost
        # ALSA
        try:
            self.mixer = alsaaudio.Mixer(ALSA_MIXER_DIGITAL)
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error initializing ALSA mixer '%s': %s", ALSA_MIXER_DIGITAL, ex_err)

        # Cache the last raw volume we actually set, to avoid ALSA churn
        self._last_set_raw = None

        # Thread is created dynamically on `start()` to allow restartability
        self._thread = None
        self._running = False

    # ---------- callback wiring ----------

    def _emit_change(self) -> None:
        """Call the change callback (if any); never let errors kill the ADC thread."""
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception:  # pylint: disable=broad-exception-caught
            # We deliberately keep this broad here: the callback is external code
            # provided by the orchestrating module. Any exception raised there
            # should NOT kill the ADC thread (which is critical for UX). We log
            # the traceback and continue, preserving device responsiveness.
            oradio_log.exception("Volume change callback failed")

# -----Helper methods----------------

    def _clamp_raw(self, value: int) -> int:
        """Clamp a raw mixer value to [VOLUME_MINIMUM..VOLUME_MAXIMUM]."""
        return max(VOLUME_MINIMUM, min(VOLUME_MAXIMUM, int(value)))

    def _set_raw_volume(self, value: int) -> None:
        """Set the ALSA mixer volume in RAW units (no-op if unchanged)."""
        clamped = self._clamp_raw(value)
#REVIEW: 3e check op 'voldoende change'
        if self._last_set_raw is not None and clamped == self._last_set_raw:
            return  # avoid ALSA churn on identical values
        try:
            self.mixer.setvolume(clamped, units=VOLUME_UNITS_RAW)
        except ALSAAudioError as ex_err:
            oradio_log.error("Error setting volume: %s", ex_err)
        self._last_set_raw = clamped
        oradio_log.debug("Volume set to: %s", clamped)

    def _read_adc(self) -> int | None:
        """Fast read of 10-bit value from MCP3021. Returns 0..1023 or None."""
        data = self._i2c_service.read_block(MCP3021_ADDRESS, READ_DATA_REGISTER, 2)
        if data:
            return ((data[0] & 0x3F) << 6) | (data[1] >> 2)
        return None

    def _scale_adc_to_volume(self, adc_value: int) -> int:
        """Scale raw ADC (0..1023) to [VOLUME_MINIMUM..VOLUME_MAXIMUM]."""
        if adc_value < 0:
            adc_value = 0
        elif adc_value > 1023:
            adc_value = 1023
        span = VOLUME_MAXIMUM - VOLUME_MINIMUM
        return int(round(VOLUME_MINIMUM + (adc_value * span) / 1023))

# -----Core methods----------------

    def _volume_manager(self) -> None:
        """Monitor the ADC and adjust the volume; emit a change event on user change."""
        oradio_log.debug("Volume manager thread started")

        # Initial state
        self._running = True
        previous_adc_value = self._read_adc() or 0
        polling_interval = POLLING_MAX_INTERVAL
        first_run = True

        while self._running:
            adc_value = self._read_adc()
            if adc_value is None:
                oradio_log.warning("ADC read failed. Retrying...")
                sleep(polling_interval)
                continue

            raw_volume = self._scale_adc_to_volume(adc_value)
            clamped_raw = self._clamp_raw(raw_volume)

#REVIEW: Waarom hier en niet voor de while loop?
            if first_run:
                # Initialize ALSA to the knob's position
                self._set_raw_volume(clamped_raw)
                oradio_log.debug("Initial volume set to: %s", clamped_raw)
                first_run = False
#REVIEW: 1e check op 'voldoende change'
            elif abs(adc_value - previous_adc_value) > ADC_UPDATE_TOLERANCE:
                previous_adc_value = adc_value

                # Only touch ALSA (and emit) if the effective RAW value changes
#REVIEW: 2e check op 'voldoende change'
               before = self._last_set_raw
                self._set_raw_volume(clamped_raw)
                after = self._last_set_raw

                if after is not None and after != before:
                    # SoC: just signal; policy lives in the subscriber
                    self._emit_change()

                polling_interval = POLLING_MIN_INTERVAL
            else:
                polling_interval = min(polling_interval + POLLING_STEP, POLLING_MAX_INTERVAL)

            sleep(polling_interval)

        oradio_log.debug("Volume manager thread stopped")

# -----Public methods----------------

    def start(self) -> None:
        """Start the volume control thread if not already running."""
        if self._thread and self._thread.is_alive():
            oradio_log.debug("Volume manager thread already running")
            return

        # Create and start thread
        self._thread = Thread(target=self._volume_manager, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the volumne control thread and wait for it to terminate."""
        if self._thread and self._thread.is_alive():
            self._running = False
            # Avoid hanging forever if the thread is stuck in I/O
            self._thread.join(timeout=2)
        else:
            oradio_log.debug("Volume manager thread not running")


# Entry point for stand-alone operation
if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _on_volume_changed() -> None:
        # In production, oradio_control wires a callback that checks the SM state.
        # Here we just show that the callback fires.
        print("[Standalone] Volume change detected → (would trigger StatePlay in main app)")

    def interactive_menu():
        """Show menu with test options"""

        # Show menu with test options
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start volume control\n"
            " 2-Stop volume control\n"
            "Select: "
        )

        # Initialise backlighting
        volume_control = VolumeControl(on_change=_on_volume_changed)

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
                    print("\nStopping volume control...\n")
                    volume_control.stop()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting VolumeControl test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting VolumeControl test program...\n")

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
