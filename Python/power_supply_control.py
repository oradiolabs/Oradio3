#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on December 18, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    USB-C Power Delivery (PD) power-supply control for Oradio hardware.

    This module controls a HUSB238 USB-C PD sink controller via I2C.
    It allows requesting specific voltage/current combinations and
    validating that the negotiated PD contract meets minimum requirements.
"""
from time import sleep

##### oradio modules ####################
from oradio_logging import oradio_log
from i2c_service import I2CService

##### Local constants ####################

# HUSB238 I2C Address
HUSB238_ADDRESS = 0x08

# HUSB238 Register Addresses
REG_PD_STATUS0 = 0x00  # PD Status register 0 (voltage/current selection)
REG_PD_STATUS1 = 0x01  # PD Status register 1 (attach, CC, response)
REG_SRC_PDO    = 0x08  # Requested PDO selection register
REG_GO_COMMAND = 0x09  # Trigger register

# Oradio-relevant voltages/current profiles:
# - Standby:  5V,  min 3.0A
# - Nominal:  9V,  min 2.0A
# - Max:      12V, min 1.5A

# Voltage selector encoding (datasheet-defined)
_VOLTAGE_SEL = {
 5: 0b0001,
 9: 0b0010,
12: 0b0011,
}

# Current selector encoding (datasheet-defined, for requests)
_CURRENT_SEL = {
1.5: 0b0100,
2.0: 0b0110,
3.0: 0b1010,
}

# Reverse lookup table for decoding negotiated voltage
_SEL_TO_VOLTAGE_V = {selector: volts for volts, selector in _VOLTAGE_SEL.items()}


# Reverse lookup table for decoding negotiated current (status read-back uses a wider encoding range)
_SEL_TO_CURRENT_A = {
    0b0000: 0.5,
    0b0001: 0.7,
    0b0010: 1.0,
    0b0011: 1.25,
    0b0100: 1.5,
    0b0101: 1.75,
    0b0110: 2.0,
    0b0111: 2.25,
    0b1000: 2.5,
    0b1001: 2.75,
    0b1010: 3.0,
    0b1011: 3.25,
    0b1100: 3.5,
    0b1101: 4.0,
    0b1110: 4.5,
    0b1111: 5.0,
}

# Typical delay required for PD negotiation to complete (seconds)
_NEGOTIATION_DELAY_S = 0.15

class PowerSupplyService:
    """
    Service class for controlling a USB-C PD power supply using a HUSB238.

    The service provides a small, safe public API for requesting predefined power profiles
    and internally handles I2C communication, PD negotiation, and status verification.

    Public API:
      - set_standby_voltage(): Request standby 5V with minimum 3.0A.
      - set_nom_voltage():     Request nominal 9V with minimum 2.0A.
      - set_max_voltage():     Request maximum 12V with minimum 1.5A.
    """

    def __init__(self) -> None:
        """
        Initialize the power supply service.

        A shared I2CService instance is obtained for communication with the HUSB238 controller.
        """
        self._i2c_service = I2CService()

# ---------------- Public API ----------------

    def set_standby_voltage(self) -> bool:
        """
        Request standby power: 5 V with a minimum of 3.0 A.

        This mode should only be used when minimal standby power is required.
        The system may enter a throttled state (e.g. Raspberry Pi supply voltage around 4.5 V).

        Returns:
            True if the negotiated PD contract meets the requirements, False otherwise.
        """
        return self._safe_set_voltage(voltage_v=5, current_a=3.0, min_current_a=3.0)

    def set_nom_voltage(self) -> bool:
        """
        Request nominal operating power: 9 V with a minimum of 2.0 A.

        Returns:
            True if the negotiated voltage/current meets the requirements.
        """
        return self._safe_set_voltage(voltage_v=9, current_a=2.0, min_current_a=2.0)

    def set_max_voltage(self) -> bool:
        """
        Request maximum operating power: 12 V with a minimum of 1.5 A.

        Returns:
            True if the negotiated voltage/current meets the requirements.
        """
        return self._safe_set_voltage(voltage_v=12, current_a=1.5, min_current_a=1.5)

# ---------------- Internal helpers ----------------

    def _safe_set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        """
        Safe wrapper around _set_voltage().

        This method guarantees that no exception propagates to the caller.
        Any error is logged and reported as a simple False return value.

        Args:
            voltage_v: Requested voltage in volts.
            current_a: Requested current in amperes.
            min_current_a: Minimum acceptable negotiated current.

        Returns:
            True if the request succeeded and requirements are met, False on any failure.
        """
        try:
            return self._set_voltage(voltage_v=voltage_v, current_a=current_a, min_current_a=min_current_a)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            oradio_log.warning(
                "PowerSupply: request %sV @ %.1fA failed: %s",
                voltage_v, current_a, exc
            )
            return False

    def _set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        """
        Perform a PD voltage/current request and verify the negotiated result.

        Args:
            voltage_v: Requested voltage in volts.
            current_a: Requested current in amperes.
            min_current_a: Minimum acceptable negotiated current.

        Returns:
            True if the negotiated voltage matches exactly and the negotiated current
            is greater than or equal to the minimum required current, False on any failure.
        """
        # Validate requested voltage
        if voltage_v not in _VOLTAGE_SEL:
            oradio_log.error(
                "PowerSupply: unsupported voltage request %sV. Supported: %s",
                voltage_v, sorted(_VOLTAGE_SEL.keys())
            )
            return False

        # Validate requested current
        if current_a not in _CURRENT_SEL:
            oradio_log.error(
                "PowerSupply: unsupported current request %.1fA. Supported: %s",
                current_a, sorted(_CURRENT_SEL.keys())
            )
            return False

        # Configure the requested PDO and trigger negotiation
        self._configure_pdo(voltage_v=voltage_v, current_a=current_a)

        # Allow the PD controller some time to complete negotiation
        sleep(_NEGOTIATION_DELAY_S)

        # Read back the negotiated PD status
        status = self.read_status()

        # Ensure a USB-C attachment is present
        if status["attach"] is False:
            oradio_log.error("PowerSupply: USB-C not attached (PD_STATUS1 attach=0).")
            return False

        delivered_v = status["voltage_v"]
        delivered_a = status["current_a"]

        # Validate decoded status fields
        if delivered_v is None or delivered_a is None:
            oradio_log.error(
                "PowerSupply: could not decode PD status (voltage_v=%s, current_a=%s).",
                delivered_v, delivered_a
            )
            return False

        # Check whether the negotiated contract meets requirements
        success = (delivered_v == voltage_v) and (delivered_a >= min_current_a)
        if success:
            oradio_log.info("PowerSupply: negotiated %sV @ %.1fA.", delivered_v, delivered_a)
            return True

        # Negotiation failed or does not meet requirements
        oradio_log.error(
            "PowerSupply: negotiation mismatch. Requested %sV @ %.1fA (min %.1fA) but got %sV @ %sA.",
            voltage_v, current_a, min_current_a, delivered_v, delivered_a
        )

        # Log additional PD response information if available
        if status["pd_response"] is not None:
            oradio_log.warning(
                "PowerSupply: PD_STATUS1 pd_response=%s, cc_dir=%s, attach=%s",
                status["pd_response"], status["cc_dir"], status["attach"]
            )

        return False

    def _configure_pdo(self, voltage_v: int, current_a: float) -> None:
        """
        Write the requested PDO to the HUSB238 and trigger negotiation.

        Args:
            voltage_v: Requested voltage in volts.
            current_a: Requested current in amperes.
        """
        # Compose the PDO value: voltage selector in the upper nibble, current selector in the lower nibble
        pdo_value = (_VOLTAGE_SEL[voltage_v] << 4) | _CURRENT_SEL[current_a]
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_SRC_PDO, pdo_value)

        # Write PDO selection and trigger the GO command
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_GO_COMMAND, 0x01)

    def read_status(self) -> dict:
        """
        Read and decode the current PD status from the HUSB238.

        Returns:
            A dictionary with the following keys:
            - voltage_v: Negotiated voltage in volts, or None if unknown
            - current_a: Negotiated current in amperes, or None if unknown
            - attach: True if attached, False if not, None if unknown
            - cc_dir: CC direction bit, or None if unknown
            - pd_response: PD response code, or None if unknown
        """
        status0 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS0)
        status1 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)

        voltage_v = None
        current_a = None
        attach = None
        cc_dir = None
        pd_response = None

        # Decode voltage and current selection from PD_STATUS0
        if status0 is None:
            oradio_log.error("PowerSupply: PD_STATUS0 read failed.")
        else:
            v_sel = (status0 >> 4) & 0b1111
            c_sel = status0 & 0b1111
            voltage_v = _SEL_TO_VOLTAGE_V.get(v_sel)
            current_a = _SEL_TO_CURRENT_A.get(c_sel)

        # Decode attachment and PD response information from PD_STATUS1
        if status1 is None:
            oradio_log.error("PowerSupply: PD_STATUS1 read failed.")
        else:
            cc_dir = (status1 >> 7) & 0b1
            attach = ((status1 >> 6) & 0b1) == 1
            pd_response = (status1 >> 3) & 0b111

        return {
            "voltage_v": voltage_v,
            "current_a": current_a,
            "attach": attach,
            "cc_dir": cc_dir,
            "pd_response": pd_response,
        }

# Entry point for stand-alone operation
if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """Show menu with test options"""
        power_service = PowerSupplyService()

        def _print_status() -> None:
            """Read and print the current PD status."""
            status = power_service.read_status()
            print(
                f"PD status: voltage={status['voltage_v']}V, current={status['current_a']}A, "
                f"attach={status['attach']}, cc_dir={status['cc_dir']}, pd_response={status['pd_response']}"
            )

        while True:
            print("\nPowerSupplyService test menu")
            print("  1) Read PD status")
            print("  2) set_standby_voltage (5V / >=3.0A)")
            print("  3) set_nom_voltage (9V / >=2.0A)")
            print("  4) set_max_voltage(12V / >=1.5A)")
            print("  q) Quit")
            choice = input("Select: ").strip().lower()

            if choice == "1":
                _print_status()
            elif choice == "2":
                result = power_service.set_standby_voltage()
                print(f"SetStandbyVoltage: {'OK' if result else 'FAIL'}")
                _print_status()
            elif choice == "3":
                result = power_service.set_nom_voltage()
                print(f"SetNomVoltage: {'OK' if result else 'FAIL'}")
                _print_status()
            elif choice == "4":
                result = power_service.set_max_voltage()
                print(f"SetMaxVoltage: {'OK' if result else 'FAIL'}")
                _print_status()
            elif choice in ("q", "quit", "exit"):
                break
            else:
                print("Unknown choice.")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
