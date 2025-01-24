import smbus2
import time


class backlighting:
    def __init__(self):
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

    def write_register(self, register, value):
        self.bus.write_byte_data(self.TSL2591_ADDR, self.COMMAND_BIT | register, value)

    def read_register(self, register):
        return self.bus.read_byte_data(self.TSL2591_ADDR, self.COMMAND_BIT | register)

    def read_two_registers(self, register_low, register_high):
        low = self.read_register(register_low)
        high = self.read_register(register_high)
        return (high << 8) | low

    def initialize_sensor(self):
        self.write_register(self.ENABLE_REG, self.ENABLE_POWER_ON | self.ENABLE_ALS)
        time.sleep(0.1)
        self.write_register(self.CONTROL_REG, self.GAIN_MEDIUM | self.INTEGRATION_TIME_300MS)

    def calculate_lux(self, raw_value):
        GAIN_SCALE = 25
        INTEGRATION_TIME_SCALE = 300 / 100
        return raw_value / (GAIN_SCALE * INTEGRATION_TIME_SCALE)

    def read_visible_light(self):
        return self.read_two_registers(self.VISIBLE_LIGHT_LOW, self.VISIBLE_LIGHT_HIGH)

    def write_dac(self, value):
        value = max(0, min(4095, value))
        high_byte = (value >> 8) & 0x0F
        low_byte = value & 0xFF
        self.bus.write_byte_data(self.MCP4725_ADDR, high_byte, low_byte)

    def interpolate_backlight(self, lux):
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
        self.initialize_sensor()
#        time.sleep(3)
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
        """Stop the auto_adjust loop and turn the backlight off."""
        self.running = False
        self.write_dac(4095)  # Set backlight to max (off)

    def maximum(self):
        """Stop the auto_adjust loop and turn the backlight off."""
        self.running = False
        self.write_dac(self.backlight_max)  # Set backlight to max (off)

if __name__ == "__main__":
    lighting = backlighting()
    lighting.auto_adjust()