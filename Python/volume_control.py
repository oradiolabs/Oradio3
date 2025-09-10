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
from typing import Callable

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
    """Tracks the volume control setting and updates ALSA; emits a callback on change."""

    # ---- callback type (zero-arg: just "changed") ----
    OnChange = Callable[[], None]

    def __init__(self, on_change: OnChange | None = None) -> None:
        """
        Initialize mixer, I²C bus and start the monitoring thread.

        Args:
            on_change: optional zero-argument callback that will be invoked
                       when a significant volume change is detected. Keep
                       the callback tiny and non-blocking.
        """
        self._on_change: VolumeControl.OnChange | None = on_change
        self.running = True

        # ALSA
        try:
            self.mixer = alsaaudio.Mixer(ALSA_MIXER_DIGITAL)
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error initializing ALSA mixer '%s': %s", ALSA_MIXER_DIGITAL, ex_err)
            raise

        # Cache the last raw volume we actually set, to avoid ALSA churn
        self._last_set_raw: int | None = None

        # I²C
        self.bus = smbus2.SMBus(1)  # always bus 1
        self._i2c_read_block = self.bus.read_i2c_block_data
        self._adc_addr = MCP3021_ADDRESS
        self._adc_cmd = 0x00

        # Thread
        self.thread = threading.Thread(target=self.volume_adc, name="VolumeADC", daemon=True)
        self.thread.start()

    # ---------- callback wiring ----------

    def set_on_change(self, callback: OnChange | None) -> None:
        """Register or clear the change callback."""
        self._on_change = callback

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

    # ---------- small helpers  ----------

    @staticmethod
    def _clamp_raw(value: int) -> int:
        """Clamp a raw mixer value to [VOLUME_MINIMUM..VOLUME_MAXIMUM]."""
        return max(VOLUME_MINIMUM, min(VOLUME_MAXIMUM, int(value)))

    def _get_raw_volume(self) -> int:
        """Read the current ALSA mixer volume in RAW units."""
        return int(self.mixer.getvolume(units=alsaaudio.VOLUME_UNITS_RAW)[0])

    def _set_raw_volume(self, value: int) -> None:
        """Set the ALSA mixer volume in RAW units (no-op if unchanged)."""
        clamped = self._clamp_raw(value)
        if self._last_set_raw is not None and clamped == self._last_set_raw:
            return  # avoid ALSA churn on identical values
        self.mixer.setvolume(clamped, units=alsaaudio.VOLUME_UNITS_RAW)
        self._last_set_raw = clamped
        oradio_log.debug("Volume set to: %s", clamped)

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
        """Set volume in RAW mixer units (with churn avoidance)."""
        try:
            self._set_raw_volume(volume_raw)
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error setting volume: %s", ex_err)

    def volume_adc(self) -> None:
        """Monitor the ADC and adjust the volume; emit a change event on user change."""
        previous_adc_value = self.read_adc() or 0
        polling_interval = POLLING_MAX_INTERVAL
        first_run = True

        while self.running:
            adc_value = self.read_adc()
            if adc_value is None:
                oradio_log.warning("ADC read failed. Retrying...")
                time.sleep(polling_interval)
                continue

            raw_volume = self.scale_adc_to_volume(adc_value)
            clamped_raw = self._clamp_raw(raw_volume)

            if first_run:
                # Initialize ALSA to the knob's position
                self.set_volume(clamped_raw)
                oradio_log.debug("Initial volume set to: %s", clamped_raw)
                first_run = False
            elif abs(adc_value - previous_adc_value) > ADC_UPDATE_TOLERANCE:
                previous_adc_value = adc_value

                # Only touch ALSA (and emit) if the effective RAW value changes
                before = self._last_set_raw
                self.set_volume(clamped_raw)
                after = self._last_set_raw

                if after is not None and after != before:
                    # SoC: just signal; policy lives in the subscriber
                    self._emit_change()

                polling_interval = POLLING_MIN_INTERVAL
            else:
                polling_interval = min(polling_interval + POLLING_STEP, POLLING_MAX_INTERVAL)

            time.sleep(polling_interval)

    def stop(self) -> None:
        """Stop thread and close I²C."""
        if not self.running:
            return
        self.running = False
        # Avoid hanging forever if the thread is stuck in I/O
        self.thread.join(timeout=1.5)
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

        # ALSA (RAW) — do a nudge and verify; restore original value
        try:
            curr = self._get_raw_volume()
            test = self._clamp_raw(curr + 2)

            # Bypass churn avoidance: we *want* to set even if our cache says equal
            self.mixer.setvolume(test, units=alsaaudio.VOLUME_UNITS_RAW)
            readback = self._get_raw_volume()
            self.mixer.setvolume(curr, units=alsaaudio.VOLUME_UNITS_RAW)
            # Keep cache consistent with the restored value
            self._last_set_raw = self._clamp_raw(curr)

            if abs(readback - test) <= 2:
                oradio_log.info("VolumeControl selftest: ALSA OK (raw %d→%d)", curr, readback)
            else:
                oradio_log.error("VolumeControl selftest: ALSA mismatch (set %d, read %d)", test, readback)
                success = False
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("VolumeControl selftest: ALSA error %s", ex_err)
            success = False

        return success


# Standalone test (no state machine)

if __name__ == "__main__":
    print("\nStarting VolumeControl standalone test...\n")
    print("Turn the volume knob and observe changes below.")
    print("Press Ctrl+C to exit.\n")

    def _on_volume_changed() -> None:
        # In production, oradio_control wires a callback that checks the SM state.
        # Here we just show that the callback fires.
        print("[Standalone] Volume change detected → (would trigger StatePlay in main app)")

    volume_control = VolumeControl(on_change=_on_volume_changed)

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
        print("Standalone test finished.")
