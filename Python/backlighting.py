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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class to run the backlighting service. Measure the light level and adapt the backlightingMCP4725
Update, such that it starts always with low backlighting level

"""
import time
import smbus2

class backlighting:
    """
    Controls the Oradio backlight
    The auto_adjust function works independently
    It is intended to be used as a system service
    """
    def __init__(self):
        """
        Initializes Oradio backlight register settings
        """
        self.TSL2591_ADDR = 0x29
        self.ENABLE_REG = 0x00
        self.CONTROL_REG = 0x01
        self.VISIBLE_LIGHT_LOW = 0x14
        self.VISIBLE_LIGHT_HIGH = 0x15
        self.COMMAND_BIT = 0xA0
        self.ENABLE_POWER_ON = 0x01
        self.ENABLE_ALS = 0x02
        self.GAIN_MEDIUM = 0x10
        self.INTEGRATION_TIME_300MS = 0x02
        self.MCP4725_ADDR = 0x60
        self.bus = smbus2.SMBus(1)
        self.lux_min = 0.1
        self.lux_mid = 5
        self.lux_max = 20.0
        self.backlight_min = 3600
        self.backlight_mid = 3300
        self.backlight_max = 3000
        self.lux_threshold = 30.0
        self.prev_raw_visible_light = 0
        self.current_backlight_value = (self.backlight_min + self.backlight_max) // 2
        self.steps_remaining = 0
        self.step_size = 0
        self.running = False  # Flag to control the auto_adjust loop

        # Write 4095 to EEPROM so that default from boot is all the leds are switched off
        self.write_dac_to_eeprom(4095)

    def write_register(self, register, value):
        """ Write value to register """
        self.bus.write_byte_data(self.TSL2591_ADDR, self.COMMAND_BIT | register, value)

    def read_register(self, register):
        """ Read value from register """
        return self.bus.read_byte_data(self.TSL2591_ADDR, self.COMMAND_BIT | register)

    def read_two_registers(self, register_low, register_high):
        """ Read value from register ;ow-high pair """
        low = self.read_register(register_low)
        high = self.read_register(register_high)
        return (high << 8) | low

    def initialize_sensor(self):
        """ Initialize light sensor """
        self.write_register(self.ENABLE_REG, self.ENABLE_POWER_ON | self.ENABLE_ALS)
        time.sleep(0.1)
        self.write_register(self.CONTROL_REG, self.GAIN_MEDIUM | self.INTEGRATION_TIME_300MS)

    def calculate_lux(self, raw_value):
        """ Calculate lux level """
        GAIN_SCALE = 25
        INTEGRATION_TIME_SCALE = 300 / 100
        return raw_value / (GAIN_SCALE * INTEGRATION_TIME_SCALE)

    def read_visible_light(self):
        """ Read light level """
        return self.read_two_registers(self.VISIBLE_LIGHT_LOW, self.VISIBLE_LIGHT_HIGH)

    def write_dac(self, value):
        """Write a 12-bit value to the MCP4725 DAC (without EEPROM storage)."""
        value = max(0, min(4095, value))  # Ensure value is within range

        high_byte = (value >> 4) & 0xFF   # 8 most significant bits
        low_byte = (value << 4) & 0xFF    # 4 least significant bits shifted

        write_command = 0x40  # Fast mode write to DAC (no EEPROM storage)

        self.bus.write_i2c_block_data(self.MCP4725_ADDR, write_command, [high_byte, low_byte])

    def interpolate_backlight(self, lux):
        """ Calculate backlight setting based on light sensor value """
        if lux < self.lux_min:
            return 4095
        elif lux >= self.lux_max:
            return self.backlight_max
        elif self.lux_min <= lux <= self.lux_mid:
            scale = (lux - self.lux_min) / (self.lux_mid - self.lux_min)
            return int(self.backlight_min + scale * (self.backlight_mid - self.backlight_min))
        else:
            scale = (lux - self.lux_mid) / (self.lux_max - self.lux_mid)
            return int(self.backlight_mid + scale * (self.backlight_max - self.backlight_mid))

    def auto_adjust(self):
        """ Autonomous backlight control """
        self.initialize_sensor()
        self.write_dac(self.backlight_min)
        self.running = True  # Set the running flag to True

        while self.running:
            raw_visible_light = self.read_visible_light()

            if abs(raw_visible_light - self.prev_raw_visible_light) / max(self.prev_raw_visible_light, 1) * 100 > self.lux_threshold:
                self.prev_raw_visible_light = raw_visible_light
                lux = self.calculate_lux(raw_visible_light)
                target_backlight_value = self.interpolate_backlight(lux)
                self.steps_remaining = 30
                self.step_size = (target_backlight_value - self.current_backlight_value) / self.steps_remaining

            if self.steps_remaining > 0:
                self.current_backlight_value += self.step_size
                self.write_dac(int(self.current_backlight_value))
                self.steps_remaining -= 1
            time.sleep(0.5)

    def off(self):
        """ Stop the auto_adjust loop and turn the backlight off """
        self.running = False
        self.write_dac(4095)  # Set backlight to max (off)

    def maximum(self):
        """ Stop the auto_adjust loop and turn the backlight on """
        self.running = False
        self.write_dac(self.backlight_max)  # Set backlight to max (off)

    def write_dac_to_eeprom(self, value):
        """ Write DAC value and store in EEPROM (persistent after reboot) """
        value = max(0, min(4095, value))  # Ensure value is within range

        high_byte = (value >> 4) & 0xFF   # 8 most significant bits
        low_byte = (value << 4) & 0xFF    # 4 least significant bits shifted

        write_command = 0x60  # Write to DAC and store in EEPROM

        self.bus.write_i2c_block_data(self.MCP4725_ADDR, write_command, [high_byte, low_byte])

if __name__ == "__main__":
    lighting = backlighting()
    lighting.auto_adjust()
