#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 10, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio I2C access module
    - No Packet Error Checking (PEC)
@references:
    https://github.com/kplindegaard/smbus2
"""
from os import listdir
from time import sleep
from threading import Lock
from smbus2 import SMBus

##### Oradio modules ######################################
from log_service import oradio_log
from singleton import singleton
from messaging import (
    Errors,
    ErrorMessage,
    I2C_SOURCE,
    I2C_ERROR_BUS,
)

##### LOCAL constants #####################################
I2C_RETRIES = 3
I2C_BACKOFF = 1     # seconds

ORADIO_DEVICES = {
    0x4D: {"name": "MCP3021 - A/D Converter"},
    0x29: {"name": "TSL2591 - Ambient Light Sensor"},
    0x60: {"name": "MCP4725 - D/A Converter"},
    0x08: {"name": "HUSB238 - USB-C Power Controller"},
}

##### Helpers #############################################

def find_i2c_buses() -> list:
    """
    Discover all available I2C buses on the system by scanning /dev/.

    Returns:
        list: Sorted list of I2C bus numbers (e.g., [0, 1])
    """
    buses = []
    for dev in listdir("/dev"):
        if dev.startswith("i2c-") and dev[4:].isdigit():
            buses.append(int(dev[4:]))
    return sorted(buses)

def test_i2c_bus(bus_number: int) -> dict:
    """
    Test a specific I2C bus for connected devices.

    Args:
        bus_number (int): The I2C bus number to test.

    Returns:
        dict: Dictionary with bus test results:
            - "bus": Bus number
            - "ok": True if bus opened successfully
            - "devices": List of tuples (address, device_name) for expected devices found
            - "missing": List of expected devices not found
            - "unexpected": List of devices found but not in ORADIO_DEVICES
            - "error": Error message if bus could not be accessed
    """
    result = {
        "bus": bus_number,
        "ok": False,
        "devices": [],
        "missing": [],
        "unexpected": [],
        "error": None
    }

    try:
        with SMBus(bus_number) as bus:
            result["ok"] = True
            found_addresses = []

            # Probe all valid I2C addresses (0x03 to 0x77)
            for addr in range(0x03, 0x78):
                try:
                    bus.write_quick(addr)  # Safe probe
                    found_addresses.append(addr)
                except OSError:
                    continue  # No device at this address

            # Check for expected devices
            for expected_addr, device_info in ORADIO_DEVICES.items():
                if expected_addr in found_addresses:
                    result["devices"].append((expected_addr, device_info["name"]))
                else:
                    result["missing"].append((expected_addr, device_info["name"]))

            # Identify unexpected devices
            for addr in found_addresses:
                if addr not in ORADIO_DEVICES:
                    result["unexpected"].append((addr, "Unknown"))

    except FileNotFoundError:
        result["error"] = f"/dev/i2c-{bus_number} not found"
    except PermissionError:
        result["error"] = "Permission denied (try running with sudo)"
    except Exception as ex_err: # pylint: disable=broad-exception-caught
        result["error"] = str(ex_err)

    return result

@singleton
class I2CService:
    """
    Thread-safe class for I2C device communication.
    - Locks all SMBus operations to prevent concurrent access from multiple threads.
    - Provides helpers for bytes, words, and block operations.
    - Logs errors for debugging.
    """
    def __init__(self) -> None:
        """
        Initialize the I2C bus and the thread lock.
        Logs and publishes an error if no buses are found or the bus is not accessible.
        """
        self._lock = Lock()
        self._bus = None

        buses = find_i2c_buses()
        if not buses:
            oradio_log.error("No I2C buses found under /dev/")
            Errors.publish(ErrorMessage(I2C_SOURCE, I2C_ERROR_BUS))
            return

        info = test_i2c_bus(buses[0])
        if not info["ok"]:
            oradio_log.error("I2C bus %d not accessible: %s", buses[0], info['error'])
            Errors.publish(ErrorMessage(I2C_SOURCE, I2C_ERROR_BUS))
            return

        self._bus = SMBus(buses[0])

##### Byte operations #####################################

    def read_byte(self, device: int, register: int) -> int | None:
        """
        Read a single byte from a device register.
        - Thread-safe with a lock.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.

        Returns:
            int | None: Byte value read from the device, or None on error.
        """
        with self._lock:
            try:
                value = self._bus.read_byte_data(device, register)
                return value
            except (OSError, ValueError, TypeError) as ex_err:
                oradio_log.error("I2C read: device=0x%02X, register=0x%02X -> %s", device, register, ex_err)
        return None

    def write_byte(self, device: int, register: int, value: int) -> None:
        """
        Write a single byte to a device register.
        - Thread-safe with a lock.
        - Write with retries and backoff.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            value (int): Byte value to write.
        """
        for attempt in range(1, I2C_RETRIES + 1):
            with self._lock:
                try:
                    self._bus.write_byte_data(device, register, value)
                    return
                except (OSError, ValueError, TypeError) as ex_err:
                    oradio_log.warning("I2C write byte failed (attempt %d/%d): device=0x%02X, register=0x%02X, value=0x%02X -> %s", attempt, I2C_RETRIES, device, register, value, ex_err)
            # Avoid hammering the I2C bus
            sleep(I2C_BACKOFF)
        # All retries exhausted
        oradio_log.error("Failed writing byte to device=0x%02X, register=0x%02X, value=0x%02X after %d attempts", device, register, value, I2C_RETRIES)

##### Block operations ####################################

    def read_block(self, device: int, register: int, length: int) -> list | None:
        """
        Read a block of bytes from a device register.
        - Thread-safe with a lock.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            length (int): Number of bytes to read, max 32.

        Returns:
            list | None: List of byte values read from the device, or None on error.
        """
        if length > 32:
            oradio_log.error("SMBus block read supports a maximum of 32 bytes")
            return None

        with self._lock:
            try:
                data = self._bus.read_i2c_block_data(device, register, length)
                return data
            except (OSError, ValueError, TypeError) as ex_err:
                oradio_log.error("I2C read block ERROR: device=0x%02X, register=0x%02X, length=%d -> %s", device, register, length, ex_err)
        return None

    def write_block(self, device: int, register: int, data: list) -> None:
        """
        Write a block of bytes to a device register.
        - Thread-safe with a lock.
        - Write with retries and backoff.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            data (list): List of byte values to write, max 32.
        """
        if len(data) > 32:
            oradio_log.error("SMBus block write supports a maximum of 32 bytes")
            return

        for attempt in range(1, I2C_RETRIES + 1):
            with self._lock:
                try:
                    self._bus.write_i2c_block_data(device, register, data)
                    return
                except (OSError, ValueError, TypeError) as ex_err:
                    oradio_log.warning("I2C write block failed (attempt %d/%d): device=0x%02X, register=0x%02X, data=%s -> %s", attempt, I2C_RETRIES, device, register, data, ex_err)
            # Avoid hammering the I2C bus
            sleep(I2C_BACKOFF)
        # All retries exhausted
        oradio_log.error("Failed writing block to device=0x%02X, register=0x%02X, data=%s after %d attempts", device, register, data, I2C_RETRIES)

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import GREEN, YELLOW, RED, NC

    def i2c_bus_probe() -> None:
        """
        Probe all available I2C buses and report results.
        - Lists expected devices and reports if missing.
        - Detects unexpected devices.
        - Uses color-coded output for clarity.
        """
        buses = find_i2c_buses()
        if not buses:
            print(f"{RED}No I2C buses found under /dev/{NC}")
            return

        print(f"Found I2C buses: {buses}")

        for bus in buses:
            info = test_i2c_bus(bus)

            if not info["ok"]:
                print(f"{RED} - Bus {bus}: not accessible ({info['error']}){NC}")
                continue

            # Report missing expected devices
            if info["missing"]:
                print(f"{RED} - Bus {bus}: missing expected devices:{NC}")
                for addr, name in info["missing"]:
                    print(f"   - device at 0x{addr:02X} -> {name}")

            # Report found expected devices
            print(f"{GREEN} - Bus {bus}: found expected devices:{NC}")
            for addr, name in info["devices"]:
                print(f"   - device at 0x{addr:02X} -> {name}")

            # Report unexpected devices
            if info["unexpected"]:
                print(f"{YELLOW} - Bus {bus}: unexpected devices detected:{NC}")
                for addr, _ in info["unexpected"]:
                    print(f"   - device at 0x{addr:02X} -> Unknown")

    i2c_bus_probe()
