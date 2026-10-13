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
from typing import Any
from os import listdir
from time import sleep
from threading import Lock
from smbus2 import SMBus

##### Oradio modules ######################################
from log_service import oradio_log
from singleton import singleton
from messaging import (
    Incidents,
    IncidentMessage,
    I2C_SOURCE,
    I2C_BUS_FAILED,
    I2C_READ_FAILED,
    I2C_WRITE_FAILED,
)

##### LOCAL constants #####################################
I2C_RETRIES = 3

# Seconds between attempts. Sized for the bus, not for a network: an I2C
# transaction takes tens of microseconds, and a device that NACKed because it
# was busy is ready again within a millisecond or two. Ten gives it room
# without making a failing transfer cost its caller most of a second -- which
# matters because get_power_status() sits on the start-up path and the
# backlight reads the light sensor in a loop.
I2C_BACKOFF = 0.01  # seconds

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
    result: dict[str, Any] = {
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
    - Provides helpers for byte and block operations.
    - Logs errors for debugging.
    - Publishes an incident once per failing device (and once for a missing
      bus), not once per failed call; see _report_failure().
    """
    def __init__(self) -> None:
        """
        Initialize the I2C bus and the thread lock.
        Logs and publishes an error if no buses are found or the bus is not accessible.
        """
        self._lock = Lock()
        self._bus = None

        # Devices whose last read or write failed, as ("read"|"write", address).
        # An incident is published when a device starts failing and not again
        # until it has answered once: the volume ADC is polled up to 20 times a
        # second and the light sensor twice, so a chip that dies would
        # otherwise put tens of incidents a second on the bus -- each one a
        # POST to RMS with log files attached -- and fill the incident queue,
        # which is fatal. Own lock, short-held; may be taken while _lock
        # is held, never the other way round.
        self._failing: set[tuple[str, int]] = set()
        self._failing_lock = Lock()
        self._bus_reported = False

        buses = find_i2c_buses()
        if not buses:
            oradio_log.error("No I2C buses found under /dev/")
            Incidents.publish(IncidentMessage(I2C_SOURCE, I2C_BUS_FAILED))
            self._bus_reported = True
            return

        info = test_i2c_bus(buses[0])
        if not info["ok"]:
            oradio_log.error("I2C bus %d not accessible: %s", buses[0], info['error'])
            Incidents.publish(IncidentMessage(I2C_SOURCE, I2C_BUS_FAILED))
            self._bus_reported = True
            return

        self._bus = SMBus(buses[0])

##### Incident latching ###################################

    def _is_failing(self, kind: str, device: int) -> bool:
        """Whether this device's last read/write (kind) already failed and was reported."""
        with self._failing_lock:
            return (kind, device) in self._failing

    def _attempt_log(self, kind: str, device: int):
        """Per-attempt failures are warnings for a fresh fault, debug for a known one."""
        return oradio_log.debug if self._is_failing(kind, device) else oradio_log.warning

    def _report_failure(self, kind: str, device: int, incident: str, log_msg: str,
                        *log_args, details: str | None = None) -> None:
        """
        Log a failed operation and publish the incident, once per fault.

        Args:
            kind:      "read" or "write".
            device:    I2C address of the device.
            incident:  I2C_READ_FAILED or I2C_WRITE_FAILED.
            log_msg:   Log format string.
            *log_args: Arguments for log_msg.
            details:   Incident details.

        The first failure is logged as an error and published. Later failures
        of the same device and kind are logged at debug only, until
        _report_success() clears the latch. RMS gets one incident per fault
        instead of one per poll.
        """
        key = (kind, device)
        with self._failing_lock:
            first = key not in self._failing
            self._failing.add(key)

        if not first:
            oradio_log.debug(log_msg + " (already reported)", *log_args)
            return

        oradio_log.error(log_msg, *log_args)
        Incidents.publish(IncidentMessage(I2C_SOURCE, incident, details=details))

    def _report_success(self, kind: str, device: int) -> None:
        """Clear the failure latch for this device and kind, and log the recovery."""
        key = (kind, device)
        with self._failing_lock:
            if key not in self._failing:
                return
            self._failing.discard(key)
        oradio_log.info("I2C device 0x%02X answering %ss again", device, kind)

    def _report_bus_missing(self) -> None:
        """Log and publish I2C_BUS_FAILED for an unusable bus, once."""
        with self._failing_lock:
            first = not self._bus_reported
            self._bus_reported = True
        if first:
            oradio_log.error("I2C bus not available")
            Incidents.publish(IncidentMessage(I2C_SOURCE, I2C_BUS_FAILED))
        else:
            oradio_log.debug("I2C bus not available (already reported)")

##### Byte operations #####################################

    def read_byte(self, device: int, register: int) -> int | None:
        """
        Read a single byte from a device register.
        - Thread-safe with a lock.
        - Read with retries and backoff.
        - Logs the operation and any errors.

        Retried unconditionally, with no way to opt out. A register read has no
        side effect, so repeating one cannot make anything worse: the only cost
        of a retry that was not needed is a few microseconds on the bus. That
        is what separates reads from writes here -- see write_byte() for the
        case where repeating a transfer is not free.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.

        Returns:
            int | None: Byte value read from the device, or None when the bus
                is unavailable or every attempt failed.
        """
        if self._bus is None:
            self._report_bus_missing()
            return None

        for attempt in range(1, I2C_RETRIES + 1):
            with self._lock:
                try:
                    value = self._bus.read_byte_data(device, register)
                except (OSError, ValueError, TypeError) as ex_err:
                    self._attempt_log("read", device)(
                        "I2C read byte failed (attempt %d/%d): device=0x%02X, register=0x%02X -> %s",
                        attempt, I2C_RETRIES, device, register, ex_err
                    )
                else:
                    self._report_success("read", device)
                    return value

            # No wait after the last attempt: there is nothing left to wait for.
            if attempt < I2C_RETRIES:
                # Avoid hammering the I2C bus
                sleep(I2C_BACKOFF)

        # All retries exhausted. The incident is published here rather than per
        # attempt, so a single flaky transfer that the next attempt fixes does
        # not reach the incident bus at all -- and once per fault, not per call
        # (see _report_failure()).
        #
        # The address travels with the incident, so RMS shows WHICH chip stopped
        # answering. Without it every i2c failure reads the same, and a dead
        # light sensor is a different fault from a dead volume ADC.
        self._report_failure(
            "read", device, I2C_READ_FAILED,
            "Failed reading byte from device=0x%02X, register=0x%02X after %d attempts",
            device, register, I2C_RETRIES,
            details=f"read byte device=0x{device:02X} register=0x{register:02X}",
        )
        return None

    def write_byte(self, device: int, register: int, value: int, retry: bool = True) -> bool:
        """
        Write a single byte to a device register.
        - Thread-safe with a lock.
        - Write with retries and backoff, unless the caller opts out.
        - Logs the operation and any errors.

        Retries are opt-out because a write, unlike a read, is not always safe
        to repeat. Setting a DAC output or a configuration register twice gives
        the same result; writing a trigger register twice starts two
        transactions. A failed write can mean the device never took it, in
        which case a retry is exactly right, or that it took it and the
        acknowledgement was lost, in which case the retry is a second command.
        Only the caller knows which register it is talking to, so only the
        caller can decide.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            value (int): Byte value to write.
            retry (bool): True (default) for registers where writing the same
                value again is harmless. False for a register whose write is
                an action rather than a value, such as the HUSB238 GO_COMMAND
                trigger; those get one attempt, and repeating the operation
                becomes a decision made where the protocol is understood.

        Returns:
            bool: True once the write is acknowledged. False if the bus is
                unavailable or every attempt failed, so a caller that depends
                on the write landing can report that instead of waiting for an
                effect that will not happen.
        """
        if self._bus is None:
            self._report_bus_missing()
            return False

        attempts = I2C_RETRIES if retry else 1

        for attempt in range(1, attempts + 1):
            with self._lock:
                try:
                    self._bus.write_byte_data(device, register, value)
                except (OSError, ValueError, TypeError) as ex_err:
                    self._attempt_log("write", device)(
                        "I2C write byte failed (attempt %d/%d): device=0x%02X, register=0x%02X, value=0x%02X -> %s",
                        attempt, attempts, device, register, value, ex_err
                    )
                else:
                    self._report_success("write", device)
                    return True

            # No wait after the last attempt: there is nothing left to wait for.
            if attempt < attempts:
                # Avoid hammering the I2C bus
                sleep(I2C_BACKOFF)

        # All attempts exhausted
        self._report_failure(
            "write", device, I2C_WRITE_FAILED,
            "Failed writing byte to device=0x%02X, register=0x%02X, value=0x%02X after %d attempt(s)",
            device, register, value, attempts,
            details=f"write byte device=0x{device:02X} register=0x{register:02X}",
        )
        return False

##### Block operations ####################################

    def read_block(self, device: int, register: int, length: int) -> list | None:
        """
        Read a block of bytes from a device register.
        - Thread-safe with a lock.
        - Read with retries and backoff.
        - Logs the operation and any errors.

        Retried unconditionally, for the same reason as read_byte(): a read has
        no side effect, so a repeat costs only bus time.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            length (int): Number of bytes to read, max 32.

        Returns:
            list | None: List of byte values read from the device, or None when
                the bus is unavailable, the block exceeds 32 bytes, or every
                attempt failed.
        """
        if self._bus is None:
            self._report_bus_missing()
            return None

        if length > 32:
            # A caller bug, not a bus fault: no attempt is made and no retry
            # would change the answer.
            oradio_log.error("SMBus block read supports a maximum of 32 bytes")
            Incidents.publish(IncidentMessage(
                I2C_SOURCE, I2C_READ_FAILED,
                details=f"block read of {length} bytes exceeds the SMBus limit of 32",
            ))
            return None

        for attempt in range(1, I2C_RETRIES + 1):
            with self._lock:
                try:
                    data = self._bus.read_i2c_block_data(device, register, length)
                except (OSError, ValueError, TypeError) as ex_err:
                    self._attempt_log("read", device)(
                        "I2C read block failed (attempt %d/%d): device=0x%02X, register=0x%02X, length=%d -> %s",
                        attempt, I2C_RETRIES, device, register, length, ex_err
                    )
                else:
                    self._report_success("read", device)
                    return data

            # No wait after the last attempt: there is nothing left to wait for.
            if attempt < I2C_RETRIES:
                # Avoid hammering the I2C bus
                sleep(I2C_BACKOFF)

        # All retries exhausted
        self._report_failure(
            "read", device, I2C_READ_FAILED,
            "Failed reading block from device=0x%02X, register=0x%02X, length=%d after %d attempts",
            device, register, length, I2C_RETRIES,
            details=f"read block device=0x{device:02X} register=0x{register:02X} length={length}",
        )
        return None

    def write_block(self, device: int, register: int, data: list) -> bool:
        """
        Write a block of bytes to a device register.
        - Thread-safe with a lock.
        - Write with retries and backoff.
        - Logs the operation and any errors.

        No retry opt-out, unlike write_byte(): the only block write on this
        board sets the MCP4725 DAC output, which is a value and not an action.
        Add one here the day a block write goes to a trigger register.

        Args:
            device (int): I2C device address.
            register (int): Register address on the device.
            data (list): List of byte values to write, max 32.

        Returns:
            bool: True once the write is acknowledged. False if the bus is
                unavailable, the block exceeds 32 bytes, or every attempt
                failed.
        """
        if self._bus is None:
            self._report_bus_missing()
            return False

        if len(data) > 32:
            oradio_log.error("SMBus block write supports a maximum of 32 bytes")
            return False

        for attempt in range(1, I2C_RETRIES + 1):
            with self._lock:
                try:
                    self._bus.write_i2c_block_data(device, register, data)
                except (OSError, ValueError, TypeError) as ex_err:
                    self._attempt_log("write", device)(
                        "I2C write block failed (attempt %d/%d): device=0x%02X, register=0x%02X, data=%s -> %s",
                        attempt, I2C_RETRIES, device, register, data, ex_err
                    )
                else:
                    self._report_success("write", device)
                    return True

            # No wait after the last attempt: there is nothing left to wait for.
            if attempt < I2C_RETRIES:
                # Avoid hammering the I2C bus
                sleep(I2C_BACKOFF)

        # All retries exhausted
        self._report_failure(
            "write", device, I2C_WRITE_FAILED,
            "Failed writing block to device=0x%02X, register=0x%02X, data=%s after %d attempts",
            device, register, data, I2C_RETRIES,
            details=f"write block device=0x{device:02X} register=0x{register:02X}",
        )
        return False

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

    print("\nStarting test program...\n")

    i2c_bus_probe()

    print("\nExiting test program...\n")
