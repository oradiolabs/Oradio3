#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #    #     #     ####

Created on December 18, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    USB-C Power Delivery (PD) power-supply control for Oradio hardware.

    This module controls a HUSB238 USB-C PD sink controller via I2C.
    It allows requesting specific voltage/current combinations and
    validating that the negotiated PD contract meets minimum requirements.

    Notes on the HUSB238 register interface (per the Hynetek HUSB238
    Register Information datasheet, rev. 1.1):
      - SRC_PDO (0x08) bits [7:4] select the requested voltage; bits [3:0]
        are reserved and must be left 0. The HUSB238 cannot be told to
        request a specific current - the delivered current is whatever
        the source offers for that voltage, and is only known after the
        fact from PD_STATUS0.
      - PD_STATUS1 bits [5:3] (PD_RESPONSE) report the outcome of the last
        PD protocol request (000=no response yet, 001=success, 011=invalid
        command/argument, 100=command not supported, 101=transaction fail).
        This is polled after a request instead of using a fixed delay.
      - SRC_PDO_5V/9V/12V/... (0x02-0x07) are read-only capability
        registers: bit 7 indicates whether the source advertises that
        voltage at all. These are used at startup to detect whether the
        connected supply supports PD negotiation before any voltage is
        requested, so an incompatible (non-PD) supply doesn't generate
        negotiation-failure incidents.
"""
from time import sleep, monotonic

##### Oradio modules ######################################
from log_service import oradio_log
from i2c_service import I2CService
from messaging import (
    Incidents,
    IncidentMessage,
    POWER_SOURCE,
    POWER_NEGOTIATION_FAILED,
)

##### LOCAL constants #####################################

# HUSB238 I2C Address
HUSB238_ADDRESS = 0x08

# HUSB238 Register Addresses
REG_PD_STATUS0  = 0x00  # PD Status register 0 (voltage/current selection)
REG_PD_STATUS1  = 0x01  # PD Status register 1 (attach, CC, response)
REG_SRC_PDO_5V  = 0x02  # Source capability register: 5V
REG_SRC_PDO_9V  = 0x03  # Source capability register: 9V
REG_SRC_PDO_12V = 0x04  # Source capability register: 12V
REG_SRC_PDO     = 0x08  # Requested PDO selection register
REG_GO_COMMAND  = 0x09  # Trigger register

# Oradio-relevant voltages/current profiles:
# - Standby:  5V,  min 3.0A
# - Nominal:  9V,  min 2.0A
# - Max:      12V, min 1.5A

# Voltage selector encoding (datasheet-defined). Written to SRC_PDO bits [7:4].
_VOLTAGE_SEL = {
 5: 0b0001,
 9: 0b0010,
12: 0b0011,
}

# Reverse lookup table for decoding negotiated voltage
_SEL_TO_VOLTAGE_V = {selector: volts for volts, selector in _VOLTAGE_SEL.items()}

# Reverse lookup table for decoding negotiated current (PD_STATUS0 bits [3:0])
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

# GO_COMMAND command codes (bits [4:0])
_CMD_REQUEST_PDO = 0b00001  # Request the PDO set by SRC_PDO.PDO_SELECT
_CMD_GET_SRC_CAP = 0b00100  # Ask the source to (re)send its capabilities

# PD_RESPONSE codes (PD_STATUS1 bits [5:3])
_PD_RESPONSE_NO_RESPONSE      = 0b000  # still negotiating, keep polling
_PD_RESPONSE_SUCCESS          = 0b001
_PD_RESPONSE_INVALID_CMD      = 0b011
_PD_RESPONSE_NOT_SUPPORTED    = 0b100
_PD_RESPONSE_TRANSACTION_FAIL = 0b101

_PD_RESPONSE_MESSAGES = {
    _PD_RESPONSE_INVALID_CMD: "invalid command or argument",
    _PD_RESPONSE_NOT_SUPPORTED: "command not supported",
    _PD_RESPONSE_TRANSACTION_FAIL: "transaction fail (no GoodCRC received)",
}

# Polling parameters for waiting on PD_RESPONSE after a PDO/capability request
_PD_RESPONSE_POLL_INTERVAL_S = 0.02   # 20 ms between polls
_PD_RESPONSE_TIMEOUT_S = 0.5          # give up after 500 ms

class PowerSupplyService:
    """
    Service class for controlling a USB-C PD power supply using a HUSB238.

    The service provides a small, safe public API for requesting predefined power profiles
    and internally handles I2C communication, PD negotiation, and status verification.

    At construction time, the attached power source's PD capabilities are queried once so
    that later requests to a supply that doesn't support PD negotiation (or doesn't
    advertise a given voltage) fail quietly instead of raising negotiation-failure
    incidents for something that was never going to work.

    Public API:
      - set_standby_voltage():   Request standby 5V with minimum 3.0A.
      - set_nom_voltage():       Request nominal 9V with minimum 2.0A.
      - set_max_voltage():       Request maximum 12V with minimum 1.5A.
      - read_status():           Read and decode the current PD status.
      - refresh_capabilities():  Re-detect the attached source's PD capabilities.
    """

    def __init__(self) -> None:
        """
        Initialize the power supply service.

        A shared I2CService instance is obtained for communication with the HUSB238
        controller, and the attached power source's PD capabilities are queried once
        up front so later requests know whether negotiation is even possible.
        """
        self._i2c_service = I2CService()
        self._capabilities = self._detect_capabilities()

##### Helpers #############################################

    def _read_pd_response(self) -> int | None:
        """
        Read the PD_RESPONSE field (bits [5:3]) from PD_STATUS1.

        Returns:
            The 3-bit PD_RESPONSE code, or None if the I2C read failed.
        """
        status1 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)
        if status1 is None:
            return None
        return (status1 >> 3) & 0b111

    def _wait_for_pd_response(self, timeout_s: float = _PD_RESPONSE_TIMEOUT_S) -> bool:
        """
        Poll PD_STATUS1.PD_RESPONSE until the HUSB238 reports a definitive
        response to the last request, or until timeout_s elapses.

        Args:
            timeout_s: Maximum time to wait for a definitive response.

        Returns:
            True if PD_RESPONSE reports Success.
            False on any other definitive response code, on an I2C read failure,
            or on timeout (no response received in time).
        """
        deadline = monotonic() + timeout_s
        while monotonic() < deadline:
            response = self._read_pd_response()

            if response is None:
                oradio_log.error("PD Status register 1 (attach, CC, response) read failed while polling PD_RESPONSE")
                return False

            if response == _PD_RESPONSE_NO_RESPONSE:
                sleep(_PD_RESPONSE_POLL_INTERVAL_S)
                continue

            if response == _PD_RESPONSE_SUCCESS:
                return True

            # Any other code is a definitive failure - no point polling further
            oradio_log.error("PD_RESPONSE=0b%s (%s)", format(response, '03b'),
                _PD_RESPONSE_MESSAGES.get(response, "unknown/reserved"),
            )
            return False

        oradio_log.error("timed out after %.2fs waiting for PD_RESPONSE", timeout_s)
        return False

    def _detect_capabilities(self) -> dict:
        """
        Query the attached power source's advertised PD capabilities.

        Reads PD_STATUS1.ATTACH to see whether anything is connected on CC at all,
        then issues Get_SRC_Cap and reads back the SRC_PDO_5V/9V/12V capability
        registers to see which of the voltages Oradio needs (5V, 9V, 12V) the
        source actually advertises support for.

        A source with no CC attachment, one that never responds to Get_SRC_Cap, or
        one that simply doesn't list any of these voltages (e.g. a legacy 5V-only
        USB charger with no PD support) is not considered a fault - it's just a
        power supply that can't do what Oradio wants, and callers should not raise
        an incident for that.

        Returns:
            {"attached": bool, "pd_capable": bool, "voltages": set[int]}
            "voltages" holds whichever of {5, 9, 12} the source advertises.
        """
        status1 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)
        if status1 is None:
            oradio_log.error("PD Status register 1 (attach, CC, response) read failed during capability check")
            return {"attached": False, "pd_capable": False, "voltages": set()}

        attach = ((status1 >> 6) & 0b1) == 1
        if not attach:
            oradio_log.warning("No USB-C attachment detected during capability check")
            return {"attached": False, "pd_capable": False, "voltages": set()}

        # Ask the source to (re)send its capabilities and wait for a definitive response
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_GO_COMMAND, _CMD_GET_SRC_CAP)
        if not self._wait_for_pd_response():
            oradio_log.warning("Source did not respond to Get_SRC_Cap; treating as a non-PD power supply")
            return {"attached": True, "pd_capable": False, "voltages": set()}

        voltages = set()
        for voltage_v, reg in ((5, REG_SRC_PDO_5V), (9, REG_SRC_PDO_9V), (12, REG_SRC_PDO_12V)):
            reg_value = self._i2c_service.read_byte(HUSB238_ADDRESS, reg)
            if reg_value is not None and (reg_value >> 7) & 0b1:
                voltages.add(voltage_v)

        if not voltages:
            oradio_log.warning("Source attached and PD-capable, but advertises none of the required voltages (5V/9V/12V)")

        oradio_log.info("detected source voltages: %s", sorted(voltages) or "none")

        return {"attached": True, "pd_capable": bool(voltages), "voltages": voltages}

    def _safe_set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        """
        Safe wrapper around _set_voltage().

        Checks the source's known PD capabilities first so that a supply which
        doesn't support PD negotiation, or doesn't advertise the requested
        voltage, fails quietly without raising an incident. Only a supply that
        claimed it could deliver the requested voltage, but then fails to
        actually do so, is treated as a genuine negotiation fault.

        This method guarantees that no exception propagates to the caller.
        Any I2C/negotiation error is logged and reported as a simple False
        return value.

        Args:
            voltage_v: Requested voltage in volts.
            current_a: Requested current in amperes (informational only - see
                _set_voltage for why the HUSB238 cannot request a specific
                current).
            min_current_a: Minimum acceptable negotiated current.

        Returns:
            True if the request succeeded and requirements are met, False on any failure.
        """
        if not self._capabilities["attached"]:
            # Nothing was attached at startup/last refresh - do a cheap live
            # re-check in case the supply was connected afterward, rather than
            # silently failing forever.
            self.refresh_capabilities()

        if not self._capabilities["pd_capable"]:
            oradio_log.warning("Skipping %sV request - connected power supply does not support PD negotiation", voltage_v)
            return False

        if voltage_v not in self._capabilities["voltages"]:
            oradio_log.warning("Skipping %sV request - connected power supply does not advertise this voltage", voltage_v)
            return False

        try:
            success = self._set_voltage(voltage_v=voltage_v, current_a=current_a, min_current_a=min_current_a)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            oradio_log.warning("Request %sV @ %.1fA failed: %s", voltage_v, current_a, exc)
            Incidents.publish(IncidentMessage(POWER_SOURCE, POWER_NEGOTIATION_FAILED))
            return False

        if not success:
            Incidents.publish(IncidentMessage(POWER_SOURCE, POWER_NEGOTIATION_FAILED))
        return success

    def _set_voltage(self, voltage_v: int, current_a: float, min_current_a: float) -> bool:
        """
        Perform a PD voltage request and verify the negotiated result.

        Note: the HUSB238 cannot request a specific current (see module docstring).
        current_a is accepted for logging/documentation of the profile being
        requested; only min_current_a is actually checked, against whatever
        current the source negotiated for the requested voltage.

        Args:
            voltage_v: Requested voltage in volts.
            current_a: Nominal current for this profile (informational only).
            min_current_a: Minimum acceptable negotiated current.

        Returns:
            True if the negotiated voltage matches exactly and the negotiated current
            is greater than or equal to the minimum required current, False on any failure.
        """
        # Validate requested voltage
        if voltage_v not in _VOLTAGE_SEL:
            oradio_log.error("Unsupported voltage request %sV. Supported: %s", voltage_v, sorted(_VOLTAGE_SEL.keys()))
            return False

        # Configure the requested PDO and trigger negotiation
        self._configure_pdo(voltage_v=voltage_v)

        # Wait for the HUSB238 to report a definitive response to the request, instead of guessing how long negotiation takes
        if not self._wait_for_pd_response():
            return False

        # Read back the negotiated PD status
        status = self.read_status()

        # Ensure a USB-C attachment is present
        if status["attach"] is False:
            oradio_log.error("USB-C not attached (PD Status register 1: attach=0)")
            return False

        delivered_v = status["voltage_v"]
        delivered_a = status["current_a"]

        # Validate decoded status fields
        if delivered_v is None or delivered_a is None:
            oradio_log.error("Could not decode PD status (voltage_v=%s, current_a=%s)", delivered_v, delivered_a)
            return False

        # Check whether the negotiated contract meets requirements
        success = (delivered_v == voltage_v) and (delivered_a >= min_current_a)
        if success:
            oradio_log.info("negotiated %sV @ %.1fA", delivered_v, delivered_a)
            return True

        # Negotiation failed or does not meet requirements
        oradio_log.error("Negotiation mismatch. Requested %sV (min %.1fA) but got %sV @ %sA", voltage_v, min_current_a, delivered_v, delivered_a)

        # Log additional PD response information if available
        if status["pd_response"] is not None:
            oradio_log.warning("PD Status register 1: pd_response=%s, cc_dir=%s, attach=%s", status["pd_response"], status["cc_dir"], status["attach"])

        return False

    def _configure_pdo(self, voltage_v: int) -> None:
        """
        Write the requested voltage to the HUSB238 SRC_PDO register and trigger negotiation.

        SRC_PDO bits [7:4] select the voltage; bits [3:0] are reserved and must be
        left 0 (the HUSB238 has no mechanism to request a specific current - see
        module docstring).

        Args:
            voltage_v: Requested voltage in volts.
        """
        pdo_value = _VOLTAGE_SEL[voltage_v] << 4  # bits [3:0] stay 0 (reserved)
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_SRC_PDO, pdo_value)

        # Trigger the GO command to request the PDO just written
        self._i2c_service.write_byte(HUSB238_ADDRESS, REG_GO_COMMAND, _CMD_REQUEST_PDO)

##### Public API ##########################################

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

    def refresh_capabilities(self) -> None:
        """
        Re-run capability detection.

        Call this if the physical USB-C connection is known to have changed
        (e.g. a hot-plug event) since __init__ or the last refresh, so a newly
        connected supply's capabilities are picked up.
        """
        self._capabilities = self._detect_capabilities()

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
            oradio_log.error("PD_STATUS0 (PD Status register 0 - voltage/current selection)) read failed")
        else:
            v_sel = (status0 >> 4) & 0b1111
            c_sel = status0 & 0b1111
            voltage_v = _SEL_TO_VOLTAGE_V.get(v_sel)
            current_a = _SEL_TO_CURRENT_A.get(c_sel)

        # Decode attachment and PD response information from PD_STATUS1
        if status1 is None:
            oradio_log.error("PD Status register 1 (attach, CC, response) read failed")
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

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import YELLOW, NC
    from utilities import input_prompt

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    def print_status(power_service) -> None:
        """Read and print the current PD status."""
        status = power_service.read_status()
        print(
            "\n"
            f"PD status: voltage={status['voltage_v']}V, current={status['current_a']}A, "
            f"attach={status['attach']}, cc_dir={status['cc_dir']}, pd_response={status['pd_response']}"
            "\n"
        )

    def print_capabilities(power_service) -> None:
        """Print the currently known source capabilities."""
        caps = power_service._capabilities  # pylint: disable=protected-access
        print(
            "\n"
            f"Capabilities: attached={caps['attached']}, pd_capable={caps['pd_capable']}, "
            f"voltages={sorted(caps['voltages'])}"
            "\n"
        )

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the Power Supply service.

        Instantiates PowerSupplyService and loops until the user selects quit (0).
        Options cover the full public API: reading status, requesting each
        voltage profile, and inspecting/refreshing detected source capabilities.
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Read PD status\n"
            " 2-set_standby_voltage (5V / >=3.0A)\n"
            " 3-set_nom_voltage (9V / >=2.0A)\n"
            " 4-set_max_voltage (12V / >=1.5A)\n"
            " 5-Show detected capabilities\n"
            " 6-Refresh capabilities\n"
            "Select: "
        )

        power_service = PowerSupplyService()
        print_capabilities(power_service)

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print_status(power_service)
                case 2:
                    result = power_service.set_standby_voltage()
                    print(f"SetStandbyVoltage: {'OK' if result else 'FAIL'}")
                    print_status(power_service)
                case 3:
                    result = power_service.set_nom_voltage()
                    print(f"SetNomVoltage: {'OK' if result else 'FAIL'}")
                    print_status(power_service)
                case 4:
                    result = power_service.set_max_voltage()
                    print(f"SetMaxVoltage: {'OK' if result else 'FAIL'}")
                    print_status(power_service)
                case 5:
                    print_capabilities(power_service)
                case 6:
                    power_service.refresh_capabilities()
                    print_capabilities(power_service)
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
