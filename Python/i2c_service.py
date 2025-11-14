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
from threading import Lock
from smbus2 import SMBus

##### oradio modules ####################
from oradio_logging import oradio_log
from singleton import singleton

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, RED, NC,
)

##### Local constants ####################
ORADIO_DEVICES = {
    0x4D: {"name": "MCP3021 - A/D Converter"},
    0x29: {"name": "TSL2591 - Ambient Light Sensor"},
    0x60: {"name": "MCP4725 - D/A Converter"},
    0x08: {"name": "HUSB238 - USB-C Power Controller"},
}

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
        """
        self._lock = Lock()
        self._bus = SMBus(1)  # Always use bus 1 on Raspberry Pi

# ---------------- Byte operations ----------------

    def write_byte(self, device: int, register: int, value: int) -> None:
        """
        Write a single byte to a device register.
        - Thread-safe with a lock.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            value (int): Byte value to write.

        Returns:
            None
        """
        with self._lock:
            try:
                self._bus.write_byte_data(device, register, value)
            except (OSError, ValueError, TypeError) as ex_err:
                oradio_log.error("I2C write: device=0x%02X, register=0x%02X, value=0x%02X -> %s", device, register, value, ex_err)

    def read_byte(self, device: int, register: int) -> int | None:
        """
        Read a single byte from a device register.
        - Thread-safe with a lock.
        - Logs the operation and any errors.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.

        Returns:
            int: Byte value read from the device.
        """
        with self._lock:
            try:
                value = self._bus.read_byte_data(device, register)
                return value
            except (OSError, ValueError, TypeError) as ex_err:
                oradio_log.error("I2C read: device=0x%02X, register=0x%02X -> %s", device, register, ex_err)
        return None

# ---------------- Block operations ----------------

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
            list: List of byte values read from the device.
        """
        if length > 32:
            oradio_log.error("SMBus block read supports a maximum of 32 bytes")

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
        - Logs the operation and any errors.
        
        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            data (list): List of byte values to write, max 32.
        
        Returns:
            None
        """
        if len(data) > 32:
            oradio_log.error("SMBus block write supports a maximum of 32 bytes")

        with self._lock:
            try:
                self._bus.write_i2c_block_data(device, register, data)
            except (OSError, ValueError, TypeError) as ex_err:
                oradio_log.error("I2C write block ERROR: device=0x%02X, register=0x%02X, data=%s -> %s", device, register, data, ex_err)

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from os import listdir

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

    def test_i2c_bus(bus_number: int, probe: bool = True) -> dict:
        """
        Test a specific I2C bus for connected devices.

        Args:
            bus_number (int): The I2C bus number to test.
            probe (bool): Whether to scan for devices (default True).

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
                if probe:
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
            info = test_i2c_bus(bus, probe=True)

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

    print("\nStarting I2C service test program...\n")

    i2c_bus_probe()

    print("\nExiting I2C service test program...\n")
