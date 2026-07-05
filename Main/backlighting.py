#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Class to run the backlighting service.
    - Measure the light level and adapt the backlighting MCP4725
    - Ensures the backlight always starts at a low level on boot
"""
from time import sleep

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from i2c_service import I2CService
from utilities import ThreadTemplate
from messaging import (
    Errors,
    ErrorMessage,
    BACKLIGHT_SOURCE,
    BACKLIGHT_ERROR_START,
    BACKLIGHT_ERROR_STOP,
)

##### LOCAL constants #####################################
# TSL2591 - Ambient Light Sensor
TSL2591_ADDRESS    = 0x29
ENABLE_REGISTER    = 0x00
CONTROL_REGISTER   = 0x01
VISIBLE_LIGHT_LOW  = 0x14
VISIBLE_LIGHT_HIGH = 0x15
COMMAND_BIT        = 0xA0
ENABLE_POWER_ON    = 0x01
ENABLE_ALS         = 0x02
GAIN_MEDIUM        = 0x10
INTEGRATION_TIME   = 0x02   # 300ms
# MCP4725 - D/A Converter
MCP4725_ADDRESS = 0x60
SAVE_PERSISTENT = 0x60
SAVE_VOLATILE   = 0x40
ALS_MIN         = 52.5  # LUX_MIN( 0.7) * GAIN_SCALE(25) * TIME_SCALE(300/100)
ALS_MID         = 375   # LUX_MID( 5.0) * GAIN_SCALE(25) * TIME_SCALE(300/100)
ALS_MAX         = 1500  # LUX_MAX(20.0) * GAIN_SCALE(25) * TIME_SCALE(300/100)
# NOTE: DAC output is inverted — higher value = less light (active-low LED driver)
# BACKLIGHT_OFF (4095) = LEDs off; BACKLIGHT_MAX (3000) = brightest
DAC_MAX         = 4095  # Maximum 12-bit DAC value
BACKLIGHT_OFF   = 4095
BACKLIGHT_MIN   = 3800
BACKLIGHT_MID   = 3300
BACKLIGHT_MAX   = 3000
# Constants controlling transition smoothness
# Backlight level is adjusted in TRANSITION_TIME / ADJUST_INTERVAL steps
# Make sure these steps small enough to not be noticable to the user
TRANSITION_TIME  = 10.0     # seconds for full transition
ADJUST_INTERVAL  = 0.5      # seconds between updates
CHANGE_THRESHOLD = 5        # minimum DAC change to write

class _BacklightWorker(ThreadTemplate):
    """
    Background worker that reads the ambient light sensor and smoothly
    adjusts the backlight DAC value once per ADJUST_INTERVAL.

    One instance is created per Backlighting object (see Backlighting.__init__)
    and reused across repeated start()/stop() cycles: ThreadTemplate itself is
    restartable, so a single _BacklightWorker instance can be safe_start()ed
    and safe_stop()ped any number of times.

    Transition state (_current_backlight_value, _prev_dac_value) is set once
    in __init__ and deliberately NOT reset in setup(), so that stopping and
    later restarting the worker resumes the brightness fade from wherever it
    left off, rather than snapping back to BACKLIGHT_MIN -- matching the fact
    that the LEDs' actual brightness does not change while the worker is
    stopped.
    """
    def __init__(self, backlighting: "Backlighting") -> None:
        super().__init__(interval=ADJUST_INTERVAL, name="BacklightWorker")
        self._backlighting = backlighting
        # Transition state; float for smooth updates, persists across restarts.
        self._current_backlight_value = float(BACKLIGHT_MIN)
        self._prev_dac_value = int(round(self._current_backlight_value))

    def setup(self) -> None:
        """Apply current brightness before the adjust loop (re)begins."""
        self._backlighting._write_dac(self._prev_dac_value)   # pylint: disable=protected-access

    def do_work(self) -> None:
        """One adjust-loop iteration: read, interpolate, write if change exceeds threshold."""
        raw_visible_light = self._backlighting._read_visible_light()   # pylint: disable=protected-access
        if raw_visible_light is None:
            return

        target_backlight_value = self._backlighting._interpolate_backlight(raw_visible_light)   # pylint: disable=protected-access

        delta = target_backlight_value - self._current_backlight_value
        self._current_backlight_value += delta * (ADJUST_INTERVAL / TRANSITION_TIME)

        dac_value = int(round(self._current_backlight_value))
        if abs(dac_value - self._prev_dac_value) >= CHANGE_THRESHOLD:
            self._prev_dac_value = dac_value
            self._backlighting._write_dac(dac_value)   # pylint: disable=protected-access

@singleton
class Backlighting:
    """
    Controls the Oradio backlight using a TSL2591 light sensor and MCP4725 DAC.
    - Automatically adjusts backlighting based on ambient light.
    - Supports turning off, setting maximum, and reading sensor values.
    - Runs the auto-adjust in a background worker; start()/stop() can be
      called repeatedly on the same worker instance since ThreadTemplate
      itself is restartable.

    Decorated as a singleton: only one Backlighting instance (and therefore
    only one worker thread and one set of hardware writes) can ever exist
    per process, regardless of how many times Backlighting() is called.
    """
    def __init__(self) -> None:
        """
        Initializes backlighting settings. Runs exactly once, on first
        construction, courtesy of @singleton:
        - Writes OFF value to DAC EEPROM (persistent), ensuring LEDs are off at boot.
        - Initializes the light sensor hardware.
        - Creates and starts the single, reusable background worker.
        """
        # Get I2C r/w methods
        self._i2c_service = I2CService()

        # Ensure all LEDs are off at boot, stored persistently in DAC EEPROM
        self._write_dac(BACKLIGHT_OFF, eeprom=True)

        # Initialize light sensor hardware
        self._initialize_sensor()

        # Created once; safe_start()/safe_stop() can be called on it
        # repeatedly since ThreadTemplate itself supports restarting.
        self._thread = _BacklightWorker(self)

        # Start backlight manager thread
        self.start()

##### Helpers #############################################

    def _write_dac(self, value: int, eeprom: bool = False) -> None:
        """
        Write a 12-bit value to the MCP4725 DAC.

        Args:
            value (int): DAC output value (0–4095)
            eeprom (bool): If True, store value in EEPROM (persistent after reboot)
                           If False, fast write without EEPROM storage
        """
        # Convert a 12-bit DAC value into high and low bytes for I2C transmission.
        value = max(0, min(DAC_MAX, value))        # Clamp value to valid range
        high_byte = (value >> 4) & 0xFF            # 8 most significant bits
        low_byte = (value << 4) & 0xFF             # 4 least significant bits shifted

        command = SAVE_PERSISTENT if eeprom else SAVE_VOLATILE
        self._i2c_service.write_block(MCP4725_ADDRESS, command, [high_byte, low_byte])

    def _initialize_sensor(self) -> None:
        """Initialize TSL2591 light sensor registers for ALS (ambient light sensing)."""
        # Power on and enable ALS
        self._i2c_service.write_byte(TSL2591_ADDRESS, COMMAND_BIT | ENABLE_REGISTER, ENABLE_POWER_ON | ENABLE_ALS)
        sleep(0.1)
        # Set gain and integration time
        self._i2c_service.write_byte(TSL2591_ADDRESS, COMMAND_BIT | CONTROL_REGISTER, GAIN_MEDIUM | INTEGRATION_TIME)

    def _read_visible_light(self) -> int | None:
        """
        Read visible light level as a 16-bit word from sensor.

        Returns:
            int | None: Raw visible light value, or None on I2C read failure.
        """
        low = self._i2c_service.read_byte(TSL2591_ADDRESS, COMMAND_BIT | VISIBLE_LIGHT_LOW)
        high = self._i2c_service.read_byte(TSL2591_ADDRESS, COMMAND_BIT | VISIBLE_LIGHT_HIGH)
        if low is None or high is None:
            return None
        return (high << 8) | low

    def _interpolate_backlight(self, als_value) -> int:
        """
        Map als_value to appropriate backlight DAC value.

        The mapping uses two linear segments:
          - Below ALS_MIN  : backlight off
          - ALS_MIN to MID : interpolate between BACKLIGHT_MIN and BACKLIGHT_MID
          - ALS_MID to MAX : interpolate between BACKLIGHT_MID and BACKLIGHT_MAX
          - Above ALS_MAX  : maximum brightness

        Note: ALS_MIN is an inclusive boundary; values exactly equal to ALS_MIN
        fall into the MIN-to-MID segment and return BACKLIGHT_MIN.

        Args:
            als_value (int): Current ambient light sensor level.

        Returns:
            int: DAC value for backlight brightness.
        """
        if als_value < ALS_MIN:
            return BACKLIGHT_OFF
        if als_value >= ALS_MAX:
            return BACKLIGHT_MAX
        if als_value <= ALS_MID:
            # Linear interpolation between MIN and MID
            return int(BACKLIGHT_MIN + (als_value - ALS_MIN) * (BACKLIGHT_MID - BACKLIGHT_MIN) / (ALS_MID - ALS_MIN))
        # Linear interpolation between MID and MAX
        return int(BACKLIGHT_MID + (als_value - ALS_MID) * (BACKLIGHT_MAX - BACKLIGHT_MID) / (ALS_MAX - ALS_MID))

##### Public API ##########################################

    def start(self) -> None:
        """
        Start the backlight auto-adjust worker if not already running.
        Blocks until the worker signals readiness or a timeout occurs.

        Note:
            Safe to call after a previous stop() -- the underlying worker
            is restartable and resumes the brightness fade from where it
            left off (see _BacklightWorker docstring).
        """
        if self._thread.is_alive():
            oradio_log.debug("Backlight manager thread already running")
            return

        if not self._thread.safe_start():
            oradio_log.error("Backlight manager thread failed to start")
            Errors.publish(ErrorMessage(BACKLIGHT_SOURCE, BACKLIGHT_ERROR_START))
            return

        if self._thread.crashed:
            oradio_log.error("Backlight manager thread crashed during startup: %s", self._thread.exception)
            Errors.publish(ErrorMessage(BACKLIGHT_SOURCE, BACKLIGHT_ERROR_START))
            return

        oradio_log.info("Backlight manager thread started")

    def stop(self) -> None:
        """
        Stop the backlighting auto-adjust worker and wait for it to terminate.

        Note:
            The worker can be resumed later via start(); see its docstring
            for how transition state is preserved across the stop/start
            cycle.
        """
        if not self._thread.is_alive():
            oradio_log.debug("Backlight manager thread not running")
            return

        if not self._thread.safe_stop():
            oradio_log.error("Backlight manager thread did not stop cleanly")
            Errors.publish(ErrorMessage(BACKLIGHT_SOURCE, BACKLIGHT_ERROR_STOP))
        else:
            oradio_log.info("Backlight manager thread stopped")

    def off(self) -> None:
        """Stop auto-adjust thread if any, and turn off backlight."""
        self.stop()
        self._write_dac(BACKLIGHT_OFF)

    def maximum(self) -> None:
        """Stop auto-adjust thread if any, and set backlight to maximum."""
        self.stop()
        self._write_dac(BACKLIGHT_MAX)

    def read_sensor(self) -> tuple:
        """
        Return the current sensor readings and the corresponding DAC value.

        Convenience method for stand-alone testing; reads sensor and returns derived values.

        Returns:
            tuple[int, float, int] | tuple[None, None, None]:
                (raw_visible_light, lux, interpolated DAC value),
                or (None, None, None) on I2C read failure.
        """
        raw = self._read_visible_light()
        if raw is None:
            return None, None, None
        lux = raw / 75  # GAIN_SCALE(25) * TIME_SCALE(300/100)
        dac = self._interpolate_backlight(raw)
        return raw, lux, dac

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the backlight.
        """

        # Show menu with test options
        input_selection = (
            "\nSelect a function, input the number.\n"
            " 0-Quit\n"
            " 1-Start Auto Adjust\n"
            " 2-Stop Auto Adjust\n"
            " 3-Turn OFF backlight\n"
            " 4-Turn ON backlight\n"
            " 5-Test sensor mode\n"
            "Select: "
        )

        # Initialise backlighting
        backlighting = Backlighting()

        # User command loop
        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print("Starting backlighting auto-adjust...")
                    backlighting.start()
                case 2:
                    print("Stopping backlighting auto-adjust...")
                    backlighting.stop()
                case 3:
                    print("Turning OFF backlight...")
                    backlighting.off()
                case 4:
                    print("Turning ON backlight...")
                    backlighting.maximum()
                case 5:
                    print("Testing sensor mode... Press Ctrl+C to return to the main menu")
                    try:
                        # Print raw visible light, calculated lux, and DAC value every 2 seconds
                        while True:
                            raw, lux, dac = backlighting.read_sensor()
                            if raw is None:
                                print("Sensor read failed")
                            else:
                                print(f"Raw Visible Light: {raw}, Lux: {lux:.2f}, DAC Value: {dac}")
                            sleep(2)
                    except KeyboardInterrupt:
                        print("\nReturning to main menu...\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topics so published messages are printed to console
    err_handler = DebugMessageHandler(Errors.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Errors.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    err_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
