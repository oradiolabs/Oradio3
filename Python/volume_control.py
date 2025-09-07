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
@summary: Oradio Volume control
"""

import threading
import time

import alsaaudio
import smbus2

from oradio_logging import oradio_log
from oradio_const import VOLUME_MINIMUM, VOLUME_MAXIMUM

# LOCAL constants
MCP3021_ADDRESS = 0x4D
ADC_UPDATE_TOLERANCE = 5
POLLING_MIN_INTERVAL = 0.05
POLLING_MAX_INTERVAL = 0.3
POLLING_STEP = 0.01
ALSA_MIXER_DIGITAL = "Digital"

# pylint: disable=c-extension-no-member


class VolumeControl:
    """Tracks the volume control setting and updates ALSA/state machine on change."""

    def __init__(self, state_machine, i2c_bus: int = 1, mixer_name: str = ALSA_MIXER_DIGITAL) -> None:
        """Initialize mixer, I²C bus and start the monitoring thread."""
        self.state_machine = state_machine
        self.running = True

        # ALSA
        try:
            self.mixer = alsaaudio.Mixer(mixer_name)
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error initializing ALSA mixer '%s': %s", mixer_name, ex_err)
            raise

        # I²C
        self.bus = smbus2.SMBus(i2c_bus)
        self._i2c_read_block = self.bus.read_i2c_block_data
        self._adc_addr = MCP3021_ADDRESS
        self._adc_cmd = 0x00

        # Thread
        self.thread = threading.Thread(target=self.volume_adc, name="VolumeADC", daemon=True)
        self.thread.start()

    # ---------- small helpers (DRY) ----------

    @staticmethod
    def _clamp_raw(value: int) -> int:
        """Clamp a raw mixer value to [VOLUME_MINIMUM..VOLUME_MAXIMUM]."""
        return max(VOLUME_MINIMUM, min(VOLUME_MAXIMUM, int(value)))

    def _get_raw_volume(self) -> int:
        """Read the current ALSA mixer volume in RAW units."""
        return int(self.mixer.getvolume(units=alsaaudio.VOLUME_UNITS_RAW)[0])

    def _set_raw_volume(self, value: int) -> None:
        """Set the ALSA mixer volume in RAW units."""
        self.mixer.setvolume(self._clamp_raw(value), units=alsaaudio.VOLUME_UNITS_RAW)

    # ---------- hot path I²C + scaling ----------

    def read_adc(self) -> int | None:
        """Fast read of 10-bit value from MCP3021. Returns 0..1023 or None."""
        try:
            byte0, byte1 = self._i2c_read_block(self._adc_addr, self._adc_cmd, 2)
        except OSError:
            return None
        return ((byte0 & 0x3F) << 6) | (byte1 >> 2)

    def scale_adc_to_volume(self, adc_value: int) -> int:
        """Scale raw ADC (0..1023) to [VOLUME_MINIMUM..VOLUME_MAXIMUM]."""
        if adc_value < 0:
            adc_value = 0
        elif adc_value > 1023:
            adc_value = 1023
        span = VOLUME_MAXIMUM - VOLUME_MINIMUM
        return int(round(VOLUME_MINIMUM + (adc_value * span) / 1023))

    # ---------- public API ----------

    def set_volume(self, volume_raw: int) -> None:
        """Set volume in RAW mixer units."""
        try:
            self._set_raw_volume(volume_raw)
            oradio_log.debug("Volume set to: %s", self._clamp_raw(volume_raw))
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error setting volume: %s", ex_err)

    def volume_adc(self) -> None:
        """Monitor the ADC and adjust the volume; wake to Play on user change."""
        previous_adc_value = self.read_adc() or 0
        polling_interval = POLLING_MAX_INTERVAL
        first_run = True

        while self.running:
            adc_value = self.read_adc()
            if adc_value is None:
                oradio_log.warning("ADC read failed. Retrying...")
                time.sleep(polling_interval)
                continue

            volume = self.scale_adc_to_volume(adc_value)

            if first_run:
                self.set_volume(volume)
                oradio_log.debug("Initial volume set to: %s", volume)
                first_run = False
            elif abs(adc_value - previous_adc_value) > ADC_UPDATE_TOLERANCE:
                previous_adc_value = adc_value
                self.set_volume(volume)

                if self.state_machine.state in ("StateStop", "StateIdle"):
                    self.state_machine.transition("StatePlay")

                polling_interval = POLLING_MIN_INTERVAL
            else:
                polling_interval = min(polling_interval + POLLING_STEP, POLLING_MAX_INTERVAL)

            time.sleep(polling_interval)

    def stop(self) -> None:
        """Stop thread and close I²C."""
        self.running = False
        self.thread.join()
        try:
            self.bus.close()
        except OSError:
            pass

    def selftest(self) -> bool:
        """One I²C read + ALSA nudge/restore in RAW units."""
        success = True

        # I²C
        adc = self.read_adc()
        if adc is None:
            success = False
            oradio_log.error("VolumeControl selftest: I²C read failed")
        else:
            oradio_log.info("VolumeControl selftest: I²C OK (ADC=%d)", adc)

        # ALSA (RAW)
        try:
            curr = self._get_raw_volume()
            test = self._clamp_raw(curr + 2)
            self._set_raw_volume(test)
            readback = self._get_raw_volume()
            self._set_raw_volume(curr)  # restore

            if abs(readback - test) <= 2:
                oradio_log.info("VolumeControl selftest: ALSA OK (raw %d→%d)", curr, readback)
            else:
                oradio_log.error("VolumeControl selftest: ALSA mismatch (set %d, read %d)", test, readback)
                success = False
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("VolumeControl selftest: ALSA error %s", ex_err)
            success = False

        return success


# Standalone test
if __name__ == "__main__":
    class DummyStateMachine:
        """Minimal stand-in state machine for standalone testing."""

        def __init__(self) -> None:
            """Initialize with Idle as the starting state."""
            self.state = "StateIdle"

        def transition(self, new_state: str) -> None:
            """Record a transition and print it for visibility during tests."""
            print(f"[DummyStateMachine] Transition: {self.state} → {new_state}")
            self.state = new_state

    print("\nStarting VolumeControl test...\n")
    print("Turn the volume knob and observe volume changes.")
    print("Press Ctrl+C to exit.\n")

    sm = DummyStateMachine()
    volume_control = VolumeControl(sm)

    if volume_control.selftest():
        print("✅ VolumeControl selftest passed")
    else:
        print("❌ VolumeControl selftest failed")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping VolumeControl...")
        volume_control.stop()
        print("Test finished.")
        