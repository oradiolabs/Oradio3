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
    Oradio Power Supply control (USB-C PD) using HUSB238 via I2C.
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
REG_SRC_PDO = 0x08     # Requested PDO selection register
REG_GO_COMMAND = 0x09  # Trigger register

# Oradio-relevant voltages/currents:
# - Standby:  5V,  min 3.0A
# - Nominal:  9V,  min 2.0A
# - Max:      12V, min 1.5A
_VOLTAGE_SEL = {
    5: 0b0001,
    9: 0b0010,
    12: 0b0011,
}

_CURRENT_SEL = {
    1.5: 0b0100,
    2.0: 0b0110,
    3.0: 0b1010,
}

_SEL_TO_VOLTAGE_V = {v: k for k, v in _VOLTAGE_SEL.items()}

# Decode map for *status readback*
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

# PD negotiation can take a short moment
_NEGOTIATION_DELAY_S = 0.15


class PowerSupplyService:
    """
    Service for controlling the USB-C PD sink controller (HUSB238) over I2C.

    Public API:
      - set_standby_voltage(): Request standby 5V with minimum 3.0A.
      - set_nom_voltage():     Request nominal 9V with minimum 2.0A.
      - set_max_voltage():     Request maximum 12V with minimum 1.5A.
    """

    def __init__(self) -> None:
        """Initialize the service and obtain the shared I2C service."""
        self._i2c_service = I2CService()

    def _safe_set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        """Wrapper: never raise; return False on any failure."""
        try:
            return self._set_voltage(voltage_v=voltage_v, current_a=current_a, min_current_a=min_current_a)
        except Exception as exc:  # keep Oradio running
            oradio_log.warning(
                "PowerSupply: request %sV @ %.1fA failed: %s",
                voltage_v, current_a, exc
            )
            return False

    # ---------------- Public API ----------------

    def set_standby_voltage(self) -> bool:
        """
        Request 5V from the PD source, with minimum 3.0A.
        Use only when really needed to get the standby power.
        System get's in throttle mode, Rpi supply voltage is 4.5 V

        Returns:
            bool: True if the negotiated voltage/current meets the requirements.
        """
        return self._safe_set_voltage(voltage_v=5, current_a=3.0, min_current_a=3.0)

    def set_nom_voltage(self) -> bool:
        """
        Request 9V from the PD source, with minimum 2.0A.

        Returns:
            bool: True if the negotiated voltage/current meets the requirements.
        """
        return self._safe_set_voltage(voltage_v=9, current_a=2.0, min_current_a=2.0)

    def set_max_voltage(self) -> bool:
        """
        Request 12V from the PD source, with minimum 1.5A.

        Returns:
            bool: True if the negotiated voltage/current meets the requirements.
        """
        return self._safe_set_voltage(voltage_v=12, current_a=1.5, min_current_a=1.5)

    # ---------------- Internal helpers ----------------

    def _set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        if voltage_v not in _VOLTAGE_SEL:
            oradio_log.error(
                "PowerSupply: unsupported voltage request %sV. Supported: %s",
                voltage_v, sorted(_VOLTAGE_SEL.keys())
            )
            return False
        if current_a not in _CURRENT_SEL:
            oradio_log.error(
                "PowerSupply: unsupported current request %.1fA. Supported: %s",
                current_a, sorted(_CURRENT_SEL.keys())
            )
            return False

        self._configure_pdo(voltage_v=voltage_v, current_a=current_a)
        sleep(_NEGOTIATION_DELAY_S)

        status = self.read_status()
        if status["attach"] is False:
            oradio_log.error("PowerSupply: USB-C not attached (PD_STATUS1 attach=0).")
            return False

        delivered_v = status["voltage_v"]
        delivered_a = status["current_a"]

        if delivered_v is None or delivered_a is None:
            oradio_log.error(
                "PowerSupply: could not decode PD status (voltage_v=%s, current_a=%s).",
                delivered_v, delivered_a
            )
            return False

        success = (delivered_v == voltage_v) and (delivered_a >= min_current_a)
        if success:
            oradio_log.info("PowerSupply: negotiated %sV @ %.1fA.", delivered_v, delivered_a)
            return True

        oradio_log.error(
            "PowerSupply: negotiation mismatch. Requested %sV @ %.1fA (min %.1fA) but got %sV @ %sA.",
            voltage_v, current_a, min_current_a, delivered_v, delivered_a
        )
        if status["pd_response"] is not None:
            oradio_log.warning(
                "PowerSupply: PD_STATUS1 pd_response=%s, cc_dir=%s, attach=%s",
                status["pd_response"], status["cc_dir"], status["attach"]
            )
        return False

    def _configure_pdo(self, voltage_v: int, current_a: float) -> None:
        """Write SRC_PDO and trigger GO_COMMAND."""
        pdo_value = (_VOLTAGE_SEL[voltage_v] << 4) | _CURRENT_SEL[current_a]
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_SRC_PDO, pdo_value)
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_GO_COMMAND, 0x01)

    def read_status(self) -> dict:
        """
        Read and decode PD status from HUSB238.

        Returns:
            dict with keys:
              - voltage_v: int|None
              - current_a: float|None
              - attach: bool|None
              - cc_dir: int|None
              - pd_response: int|None
        """
        status0 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS0)
        status1 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)

        voltage_v = None
        current_a = None
        attach = None
        cc_dir = None
        pd_response = None

        if status0 is None:
            oradio_log.error("PowerSupply: PD_STATUS0 read failed.")
        else:
            v_sel = (status0 >> 4) & 0b1111
            c_sel = status0 & 0b1111
            voltage_v = _SEL_TO_VOLTAGE_V.get(v_sel)
            current_a = _SEL_TO_CURRENT_A.get(c_sel)

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


def main() -> None:
    """Standalone test runner (menu style, similar to volume_control.py)."""
    power_service = PowerSupplyService()

    def _print_status() -> None:
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


if __name__ == "__main__":
    main()
