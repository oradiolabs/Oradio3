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
    - Update, such that it starts always with low backlighting level
@references:
    
"""
from time import sleep
from threading import Thread, Event

##### oradio modules ####################
from oradio_logging import oradio_log
from i2c_service import I2CService

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
)

##### Local constants ####################
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
BACKLIGHT_OFF   = 4095
BACKLIGHT_MIN   = 3800
BACKLIGHT_MID   = 3300
BACKLIGHT_MAX   = 3000
# Constants controlling transition smoothness
TRANSITION_TIME  = 10.0     # seconds for full transition
ADJUST_INTERVAL  = 0.5      # seconds between updates
CHANGE_THRESHOLD = 5        # minimum DAC change to write

class Backlighting:
    """
    Controls the Oradio backlight using a TSL2591 light sensor and MCP4725 DAC.
    - Automatically adjusts backlighting based on ambient light.
    - Supports turning off, setting maximum, and reading sensor values.
    - Runs the auto-adjust in a background thread for continuous operation.
    """
    def __init__(self) -> None:
        """
        Initializes backlighting settings:
        - Default backlight value is set between min and max.
        - Writes default OFF value to DAC EEPROM (persistent).
        - Prepares the background thread for auto-adjust.
        - Initializes the light sensor.
        """
        # Get I2C r/w methods
        self._i2c_service = I2CService()

#REVIEW: Eenemalig. Naar oradio_install.sh om slijtage eeprom te voorkomen
        # Ensure all LEDs are off at boot, stored persistently in DAC EEPROM
        self._write_dac(BACKLIGHT_OFF, eeprom=True)

        # Initialize light sensor hardware
        self._initialize_sensor()

        # Thread is created dynamically on `start()` to allow restartability
        self._thread = None
        self._running = Event()

# -----Helper methods----------------

    def _write_dac(self, value: int, eeprom: bool = False) -> None:
        """
        Write a 12-bit value to the MCP4725 DAC.
        
        Args:
            value (int): DAC output value (0–4095)
            eeprom (bool): If True, store value in EEPROM (persistent after reboot)
                           If False, fast write without EEPROM storage
        """
        # Convert a 12-bit DAC value into high and low bytes for I2C transmission.
        value = max(0, min(BACKLIGHT_OFF, value))  # Clamp value to valid range
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
            int: Raw visible light value.
        """
        low = self._i2c_service.read_byte(TSL2591_ADDRESS, COMMAND_BIT | VISIBLE_LIGHT_LOW)
        high = self._i2c_service.read_byte(TSL2591_ADDRESS, COMMAND_BIT | VISIBLE_LIGHT_HIGH)
        if low is None or high is None:
            return None
        return (high << 8) | low

    def _interpolate_backlight(self, als_value) -> int:
        """
        Map als_value to appropriate backlight DAC value.

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

# -----Core methods----------------

    def _backlight_manager(self) -> None:
        """
        Background thread that adjusts the backlight smoothly.
        Adjusts if the change exceeds the threshold.
        """
        # Initial state
        current_backlight_value = float(BACKLIGHT_MIN)  # use float for smooth updates
        prev_dac_value = int(round(current_backlight_value))

        # Apply starting brightness
        self._write_dac(prev_dac_value)

        # Backlight adjustment loop
        while self._running.is_set():

            # Read ambient light
            raw_visible_light = self._read_visible_light()
            if raw_visible_light is None:
                sleep(ADJUST_INTERVAL)
                continue

            # Convert to desired backlight
            target_backlight_value = self._interpolate_backlight(raw_visible_light)

            # Smooth transition toward target
            delta = target_backlight_value - current_backlight_value
            current_backlight_value += delta * (ADJUST_INTERVAL / TRANSITION_TIME)

            # Convert to DAC int and write only if change exceeds threshold
            dac_value = int(round(current_backlight_value))
            if abs(dac_value - prev_dac_value) >= CHANGE_THRESHOLD:
                prev_dac_value = dac_value

                # Set backlighting level
                self._write_dac(dac_value)

            sleep(ADJUST_INTERVAL)

# -----Public methods----------------

    def start(self) -> None:
        """Start the backlighting auto-adjust thread if not already running."""
        if self._thread and self._thread.is_alive():
            oradio_log.debug("Volume manager thread already running")
            return

        # signal: start volume manager thread
        self._running.set()

        # Create and start thread
        self._thread = Thread(target=self._backlight_manager, daemon=True)
        self._thread.start()

        oradio_log.debug("Backlight manager thread started")

    def stop(self) -> None:
        """Stop the volumne control thread and wait for it to terminate."""
        if not self._thread or not self._thread.is_alive():
            oradio_log.debug("Backlight manager thread not running")
            return

        # signal: stop volume manager thread
        self._running.clear()

        # Avoid hanging forever if the thread is stuck in I/O
        self._thread.join(timeout=2)

        oradio_log.debug("Backlight manager thread stopped")

    def off(self):
        """Stop auto-adjust thread if any, and turn off backlight."""
        self.stop()
        self._write_dac(BACKLIGHT_OFF)

    def maximum(self):
        """Stop auto-adjust thread if any, and set backlight to maximum."""
        self.stop()
        self._write_dac(BACKLIGHT_MAX)

    def read_sensor(self) -> tuple:
        """
        Return the current sensor readings and corresponding DAC value.
        - Wrapper accessing internal methods while running stand-alone

        Returns:
            tuple[int, float, int]: (raw_visible_light, lux, interpolated DAC value)
        """
        raw = self._read_visible_light()
        lux = raw / 75  # GAIN_SCALE(25) * TIME_SCALE(300/100)
        dac = self._interpolate_backlight(raw)
        return raw, lux, dac

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def interactive_menu():
        """Show menu with test options"""

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
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
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
                            print(f"Raw Visible Light: {raw}, Lux: {lux:.2f}, DAC Value: {dac}")
                            sleep(2)
                    except KeyboardInterrupt:
                        print("\nReturning to main menu...\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting Backlighting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting Backlighting test program...\n")

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
