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

    This module reads the state of a HUSB238 USB-C PD sink controller over
    I2C, and provides stand-alone tooling to negotiate PD power profiles with
    the attached source.

    Importable surface:
      - get_power_status(): negotiated voltage and current, from one read of
        PD_STATUS0. Operational reads run on a latency budget, so this
        performs no PD transaction, constructs no objects and publishes no
        incidents.

    Stand-alone surface (diagnosis is not time-critical, so these gather
    everything available at whatever cost):
      - get_diagnostic_status(): every documented PD_STATUS0/PD_STATUS1 field,
        including attach, CC orientation, PD response, 5V contract and the raw
        register bytes.
      - PowerService: requests voltage profiles and validates the resulting
        contract, alongside the registers and timing parameters only it needs.

    To negotiate voltages from an importable consumer, move PowerService and
    its constants to module level.

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
      - PD_STATUS0 (the negotiated voltage/current selection) can update a
        short time after PD_RESPONSE reports Success, not atomically with
        it - reading it once immediately on Success can still return the
        previous contract's values. A short follow-up poll on PD_STATUS0
        closes that gap.
      - SRC_PDO_5V/9V/12V/... (0x02-0x07) are read-only capability
        registers: bit 7 indicates whether the source advertises that
        voltage at all. These are used at startup to detect whether the
        connected supply supports PD negotiation before any voltage is
        requested, so an incompatible (non-PD) supply fails fast instead of
        burning a full negotiation timeout.
      - A GO_COMMAND request can occasionally come back with
        PD_RESPONSE=Transaction Fail (no GoodCRC received) as a transient
        ack-timing condition rather than a genuine rejection, particularly
        when it follows closely behind a prior PD transaction on the same
        source. Requests are retried a bounded number of times to absorb
        this before treating it as a real failure.
"""
from typing import TypedDict

##### Oradio modules ######################################
from log_service import oradio_log
from i2c_service import I2CService

##### LOCAL constants #####################################

# HUSB238 I2C Address
HUSB238_ADDRESS = 0x08

# HUSB238 Register Addresses
REG_PD_STATUS0  = 0x00  # PD Status register 0 (voltage/current selection)
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

class PowerStatus(TypedDict):
    """Negotiated PD contract.

    Attributes:
        voltage_v: Negotiated voltage in volts, or None if unknown.
        current_a: Negotiated current in amperes, or None if unknown.
    """
    voltage_v: int | None
    current_a: float | None

##### Minimal read-only API ###############################

def get_power_status() -> PowerStatus:
    """Read the currently negotiated PD voltage and current.

    Both values live in PD_STATUS0, so this is a single I2C register read. It
    triggers no PD transaction and publishes no incidents, so it cannot
    disturb an active contract or add startup latency.

    For the PD_STATUS1 fields (attach, CC direction, PD response, 5V
    contract), use get_diagnostic_status() in the stand-alone section.

    Returns:
        The negotiated contract. Both fields are None if the register could
        not be read; an individual field is None if its selector falls
        outside the set of values Oradio uses.
    """
    status0 = I2CService().read_byte(HUSB238_ADDRESS, REG_PD_STATUS0)
    if status0 is None:
        oradio_log.error("PD_STATUS0 (PD Status register 0 - voltage/current selection) read failed")
        return {"voltage_v": None, "current_a": None}

    voltage_v = _SEL_TO_VOLTAGE_V.get((status0 >> 4) & 0b1111)
    current_a = _SEL_TO_CURRENT_A.get(status0 & 0b1111)
    if voltage_v is None:
        # Logged at DEBUG, not WARNING: a selector outside the Oradio set
        # (e.g. a source offering 15V/20V) is unusual but not a fault.
        oradio_log.debug("PD_STATUS0=0x%02X holds a voltage selector outside the Oradio set", status0)

    return {"voltage_v": voltage_v, "current_a": current_a}

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from time import sleep, monotonic
    from constants import YELLOW, NC
    from utilities import input_prompt

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    ##### Constants used only by diagnostics ##################

    REG_PD_STATUS1  = 0x01  # PD Status register 1 (attach, CC, response, 5V contract)
    REG_SRC_PDO_5V  = 0x02  # Source capability register: 5V
    REG_SRC_PDO_9V  = 0x03  # Source capability register: 9V
    REG_SRC_PDO_12V = 0x04  # Source capability register: 12V
    REG_SRC_PDO     = 0x08  # Requested PDO selection register
    REG_GO_COMMAND  = 0x09  # Trigger register

    # 5V contract current encoding (PD_STATUS1 bits [1:0]). These are the
    # source Rp broadcast levels that apply when there is no PD contract.
    # 0b00 means "USB default" - a level the source does not state.
    _SEL_TO_5V_CONTRACT_A = {
        0b01: 1.5,
        0b10: 2.4,
        0b11: 3.0,
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

    # Polling parameters for waiting on a definitive negotiation outcome after a
    # PDO request: either PD_RESPONSE reports a failure, or PD_STATUS0 settles on
    # the requested voltage. A single poll loop checks both each iteration.
    _NEGOTIATION_POLL_INTERVAL_S = 0.02   # 20 ms between polls
    _NEGOTIATION_TIMEOUT_S = 0.5          # give up after 500 ms total

    # Retry parameters for Get_SRC_Cap during capability detection. A bounded
    # retry absorbs a transient Transaction Fail (no GoodCRC) response without
    # masking a source that is genuinely not PD-capable.
    _GET_SRC_CAP_MAX_ATTEMPTS = 3
    _GET_SRC_CAP_RETRY_DELAY_S = 0.05     # 50 ms between attempts

    # Retry parameters for a PDO voltage request (set_standby/nom/max_voltage).
    # Only a Transaction Fail (no GoodCRC) response is retried - INVALID_CMD,
    # NOT_SUPPORTED, and a plain timeout are genuine outcomes and are not
    # retried.
    _VOLTAGE_REQUEST_MAX_ATTEMPTS = 3
    _VOLTAGE_REQUEST_RETRY_DELAY_S = 0.05  # 50 ms between attempts

    # Settle delay applied after capability detection's own Get_SRC_Cap
    # transaction (see _detect_capabilities_and_settle()), used by both
    # __init__ and refresh_capabilities(). Without it, a caller that issues a
    # voltage request immediately afterward can land close enough behind
    # Get_SRC_Cap on the same source to race it and get a transient Transaction
    # Fail, even though the request itself is valid.
    _POST_INIT_SETTLE_DELAY_S = 0.2

    ##### Diagnostic read #####################################

    class Capabilities(TypedDict):
        """PD capabilities advertised by the attached source.

        Attributes:
            attached: True when a USB-C attachment is detected on CC.
            pd_capable: True when the source advertises at least one of the
                voltages Oradio uses.
            voltages: Whichever of {5, 9, 12} the source advertises.
        """
        attached: bool
        pd_capable: bool
        voltages: set[int]

    class DiagnosticStatus(TypedDict):
        """Every documented field of PD_STATUS0 and PD_STATUS1.

        Attributes:
            voltage_v: Negotiated voltage in volts, or None if unknown.
            current_a: Negotiated current in amperes, or None if unknown.
            attach: True when CC is attached, None if unknown.
            cc_dir: CC orientation (0=CC1, 1=CC2), or None if unknown. Only
                meaningful while attach is True.
            pd_response: PD response code for the most recent request, or
                None. Reads as 0 (no response) until something is requested.
            contract_5v: True when the 5V contract voltage bit is set.
            contract_5v_current_a: 5V contract current in amperes, or None
                for the unstated "USB default" level.
            raw_status0: Raw PD_STATUS0 byte, or None if the read failed.
            raw_status1: Raw PD_STATUS1 byte, or None if the read failed.
        """
        voltage_v: int | None
        current_a: float | None
        attach: bool | None
        cc_dir: int | None
        pd_response: int | None
        contract_5v: bool | None
        contract_5v_current_a: float | None
        raw_status0: int | None
        raw_status1: int | None

    def get_diagnostic_status() -> DiagnosticStatus:
        """Read and decode every field the two PD status registers expose.

        Reads PD_STATUS0 and PD_STATUS1 once each, so all decoded fields
        describe the same instant. Voltage and current are decoded here
        directly to keep PD_STATUS0 to a single read per call.

        Returns:
            The full decoded status, including the raw register bytes for
            comparison against the datasheet.
        """
        i2c_service = I2CService()
        status0 = i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS0)
        status1 = i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)

        voltage_v = None
        current_a = None
        attach = None
        cc_dir = None
        pd_response = None
        contract_5v = None
        contract_5v_current_a = None

        # Decode voltage and current selection from PD_STATUS0
        if status0 is None:
            oradio_log.error("PD_STATUS0 (PD Status register 0 - voltage/current selection) read failed")
        else:
            voltage_v = _SEL_TO_VOLTAGE_V.get((status0 >> 4) & 0b1111)
            current_a = _SEL_TO_CURRENT_A.get(status0 & 0b1111)

        # Decode attach, CC orientation, PD response and 5V contract from PD_STATUS1
        if status1 is None:
            oradio_log.error("PD Status register 1 (attach, CC, response) read failed")
        else:
            cc_dir = (status1 >> 7) & 0b1
            attach = ((status1 >> 6) & 0b1) == 1
            pd_response = (status1 >> 3) & 0b111
            contract_5v = ((status1 >> 2) & 0b1) == 1
            contract_5v_current_a = _SEL_TO_5V_CONTRACT_A.get(status1 & 0b11)

        return {
            "voltage_v": voltage_v,
            "current_a": current_a,
            "attach": attach,
            "cc_dir": cc_dir,
            "pd_response": pd_response,
            "contract_5v": contract_5v,
            "contract_5v_current_a": contract_5v_current_a,
            "raw_status0": status0,
            "raw_status1": status1,
        }

    ##### Full control API ####################################

    class PowerService:
        """Control a USB-C PD power supply through a HUSB238.

        Requests predefined power profiles and handles the I2C communication,
        PD negotiation and status verification behind them. Construction
        queries the source's PD capabilities once, so a request to a supply
        that cannot negotiate, or that does not advertise the wanted voltage,
        fails immediately.

        Public API:
          - set_standby_voltage():   Request standby 5V with minimum 3.0A.
          - set_nom_voltage():       Request nominal 9V with minimum 2.0A.
          - set_max_voltage():       Request maximum 12V with minimum 1.5A.
          - refresh_capabilities():  Re-detect the attached source's PD capabilities.
        """

        def __init__(self) -> None:
            """Initialize the power supply service.

            Obtains the shared I2CService for HUSB238 communication and
            queries the source's PD capabilities, so later requests know
            whether negotiation is possible.
            """
            self._i2c_service = I2CService()
            self._capabilities = self._detect_capabilities_and_settle()

    ##### Helpers #############################################

        def _read_pd_response(self) -> int | None:
            """Read the PD_RESPONSE field (bits [5:3]) from PD_STATUS1.

            Returns:
                The 3-bit PD_RESPONSE code, or None if the I2C read failed.
            """
            status1 = self._i2c_service.read_byte(HUSB238_ADDRESS, REG_PD_STATUS1)
            if status1 is None:
                return None
            return (status1 >> 3) & 0b111

        def _wait_for_pd_response(self, timeout_s: float = _NEGOTIATION_TIMEOUT_S, is_final_attempt: bool = True) -> bool:
            """Poll PD_STATUS1.PD_RESPONSE until the HUSB238 reports a definitive
            response to the last request, or until timeout_s elapses.

            Used for requests that have no associated PD_STATUS0 value to wait on
            (e.g. Get_SRC_Cap during capability detection). For a PDO voltage
            request, use _wait_for_voltage_negotiation() instead, which checks
            PD_RESPONSE and PD_STATUS0 together in a single poll.

            Args:
                timeout_s: Maximum time to wait for a definitive response.
                is_final_attempt: Whether this is the last attempt in the caller's
                    retry loop. A failure is logged at ERROR when true, or DEBUG
                    when false since the caller is about to retry and a single
                    failed attempt isn't (yet) an actionable problem.

            Returns:
                True if PD_RESPONSE reports Success.
                False on any other definitive response code, on an I2C read failure,
                or on timeout (no response received in time).
            """
            log_failure = oradio_log.error if is_final_attempt else oradio_log.debug
            deadline = monotonic() + timeout_s
            while monotonic() < deadline:
                response = self._read_pd_response()

                if response is None:
                    log_failure("PD Status register 1 (attach, CC, response) read failed while polling PD_RESPONSE")
                    return False

                if response == _PD_RESPONSE_NO_RESPONSE:
                    sleep(_NEGOTIATION_POLL_INTERVAL_S)
                    continue

                if response == _PD_RESPONSE_SUCCESS:
                    return True

                # Any other code is a definitive failure - no point polling further
                log_failure(
                    "PD_RESPONSE=0b%s (%s)", format(response, '03b'),
                    _PD_RESPONSE_MESSAGES.get(response, "unknown/reserved")
                )
                return False

            log_failure("timed out after %.2fs waiting for PD_RESPONSE", timeout_s)
            return False

        def _wait_for_voltage_negotiation(self, voltage_v: int, timeout_s: float = _NEGOTIATION_TIMEOUT_S, is_final_attempt: bool = True) -> tuple[bool, DiagnosticStatus]:
            """Poll after a PDO voltage request until the outcome is known, checking
            both signals in a single pass with one shared timeout budget:

              - PD_RESPONSE reporting a definitive failure code (anything other
                than Success) ends the wait immediately - there is no point
                waiting for PD_STATUS0 to update after a rejected request.
              - PD_STATUS0 reflecting the requested voltage_v means the contract
                has settled and negotiation succeeded.

            Both signals are needed. PD_RESPONSE lets a rejected request fail
            fast with a specific reason instead of burning the full timeout on
            a status value that will never arrive, and it separates "no
            negotiation happened" from "negotiation succeeded but the status
            register has not caught up". PD_STATUS0 alone cannot distinguish
            those: when the requested voltage is already active, a stale match
            looks identical to a fresh success.

            Args:
                voltage_v: The voltage that was just requested.
                timeout_s: Maximum total time to wait for a definitive outcome.
                is_final_attempt: Whether this is the last attempt in the caller's
                    retry loop. A failure is logged at ERROR when true, or DEBUG
                    when false since the caller is about to retry and a single
                    failed attempt isn't (yet) an actionable problem.

            Returns:
                (True, status) once PD_STATUS0 confirms voltage_v.
                (False, status) on a PD_RESPONSE failure code, an I2C read
                failure, or timeout - status holds whatever was last read.
            """
            log_failure = oradio_log.error if is_final_attempt else oradio_log.debug
            deadline = monotonic() + timeout_s
            status = get_diagnostic_status()

            while monotonic() < deadline:
                response = self._read_pd_response()

                if response is None:
                    log_failure(
                        "PD Status register 1 read failed while polling negotiation outcome"
                    )
                    return False, status

                if response not in (_PD_RESPONSE_NO_RESPONSE, _PD_RESPONSE_SUCCESS):
                    log_failure(
                        "PD_RESPONSE=0b%s (%s)", format(response, '03b'),
                        _PD_RESPONSE_MESSAGES.get(response, "unknown/reserved")
                    )
                    # Surface the failing code so the caller (_set_voltage) can
                    # tell a transient Transaction Fail apart from a genuine
                    # rejection. Without this the field holds the value from
                    # the last successful poll.
                    status["pd_response"] = response
                    return False, status

                status = get_diagnostic_status()
                if status["voltage_v"] == voltage_v:
                    return True, status

                sleep(_NEGOTIATION_POLL_INTERVAL_S)

            log_failure(
                "Timed out after %.2fs waiting for negotiation of %sV to settle "
                "(last read: %sV)", timeout_s, voltage_v, status["voltage_v"]
            )
            return False, status

        def _request_src_cap(self, is_final_attempt: bool = True) -> bool:
            """Issue a single Get_SRC_Cap request and wait for a definitive PD_RESPONSE.

            Args:
                is_final_attempt: Forwarded to _wait_for_pd_response() so a
                    failure that the caller is about to retry logs at DEBUG
                    instead of ERROR.

            Returns:
                True if PD_RESPONSE reports Success, False otherwise.
            """
            self._i2c_service.write_byte(HUSB238_ADDRESS, REG_GO_COMMAND, _CMD_GET_SRC_CAP)
            return self._wait_for_pd_response(is_final_attempt=is_final_attempt)

        def _detect_capabilities(self) -> Capabilities:
            """Query the attached power source's advertised PD capabilities.

            Reads PD_STATUS1.ATTACH to see whether anything is connected on CC at all,
            then issues Get_SRC_Cap and reads back the SRC_PDO_5V/9V/12V capability
            registers to see which of the voltages Oradio needs (5V, 9V, 12V) the
            source actually advertises support for.

            Get_SRC_Cap is retried a bounded number of times (see
            _GET_SRC_CAP_MAX_ATTEMPTS) to absorb a transient Transaction Fail
            (no GoodCRC) response before concluding the source is genuinely not
            PD-capable.

            A source with no CC attachment, one that never responds to Get_SRC_Cap
            after retries, or one that simply doesn't list any of these voltages
            (e.g. a legacy 5V-only USB charger with no PD support) is not
            considered a fault - it's just a power supply that can't do what
            Oradio wants, and is reported as a warning rather than an error.

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

            # Ask the source to (re)send its capabilities, retrying a bounded
            # number of times to absorb a transient Transaction Fail (no GoodCRC).
            got_response = False
            for attempt in range(1, _GET_SRC_CAP_MAX_ATTEMPTS + 1):
                got_response = self._request_src_cap(is_final_attempt=attempt == _GET_SRC_CAP_MAX_ATTEMPTS)
                if got_response:
                    break
                if attempt < _GET_SRC_CAP_MAX_ATTEMPTS:
                    oradio_log.debug(
                        "Get_SRC_Cap attempt %d/%d failed, retrying",
                        attempt, _GET_SRC_CAP_MAX_ATTEMPTS
                    )
                    sleep(_GET_SRC_CAP_RETRY_DELAY_S)

            if not got_response:
                oradio_log.warning(
                    "Source did not respond to Get_SRC_Cap after %d attempts; treating as a non-PD power supply",
                    _GET_SRC_CAP_MAX_ATTEMPTS
                )
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

        def _detect_capabilities_and_settle(self) -> Capabilities:
            """Detect capabilities and let the source settle.

            When a bus transaction took place (source attached), waits before
            returning so that a voltage request issued immediately afterwards
            does not race the Get_SRC_Cap that detection just performed.

            Returns:
                Same shape as _detect_capabilities().
            """
            capabilities = self._detect_capabilities()
            if capabilities["attached"]:
                sleep(_POST_INIT_SETTLE_DELAY_S)
            return capabilities

        def _safe_set_voltage(self, voltage_v: int, min_current_a: float) -> bool:
            """Request a voltage, guarding against unsupported requests.

            Checks the source's known PD capabilities first, so a supply that
            cannot negotiate, or does not advertise the requested voltage,
            fails without attempting the transaction. No exception propagates
            to the caller: any I2C or negotiation error is logged and reported
            as a False return value.

            Args:
                voltage_v: Requested voltage in volts.
                min_current_a: Minimum acceptable negotiated current.

            Returns:
                True if the request succeeded and requirements are met, False on any failure.
            """
            if not self._capabilities["attached"]:
                # Nothing attached at the last check: re-check live in case the
                # supply has been connected since.
                self.refresh_capabilities()

            if not self._capabilities["pd_capable"]:
                oradio_log.warning("Skipping %sV request - connected power supply does not support PD negotiation", voltage_v)
                return False

            if voltage_v not in self._capabilities["voltages"]:
                oradio_log.warning("Skipping %sV request - connected power supply does not advertise this voltage", voltage_v)
                return False

            try:
                success = self._set_voltage(voltage_v=voltage_v, min_current_a=min_current_a)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                oradio_log.warning(
                    "Request %sV (min %.1fA) failed: %s", voltage_v, min_current_a, exc
                )
                return False

            return success

        def _set_voltage(self, voltage_v: int, min_current_a: float) -> bool:
            """Perform a PD voltage request and verify the negotiated result.

            Note: the HUSB238 cannot request a specific current (see module docstring).
            min_current_a is checked, against whatever current the source actually negotiated
            for the requested voltage.

            Args:
                voltage_v: Requested voltage in volts.
                min_current_a: Minimum acceptable negotiated current.

            Returns:
                True if the negotiated voltage matches exactly and the negotiated current
                is greater than or equal to the minimum required current, False on any failure.
            """
            # Validate requested voltage
            if voltage_v not in _VOLTAGE_SEL:
                oradio_log.error("Unsupported voltage request %sV. Supported: %s", voltage_v, sorted(_VOLTAGE_SEL.keys()))
                return False

            # Skip negotiation when the active contract already satisfies the
            # request: some sources NAK a redundant request, which reads back
            # as PD_RESPONSE=Transaction Fail (no GoodCRC).
            current_status = get_diagnostic_status()
            if (
                current_status["attach"]
                and current_status["voltage_v"] == voltage_v
                and current_status["current_a"] is not None
                and current_status["current_a"] >= min_current_a
            ):
                oradio_log.info(
                    "Source already negotiated %sV @ %.1fA (matches request); "
                    "skipping renegotiation",
                    current_status["voltage_v"], current_status["current_a"]
                )
                return True

            # Configure the requested PDO and trigger negotiation. Retried a
            # bounded number of times specifically when the source comes back
            # with Transaction Fail (no GoodCRC), in case a genuinely fresh
            # negotiation still hits a short ack-timing blip. Any other failure
            # (invalid command, not supported, or a plain timeout) is a genuine
            # outcome and is not retried.
            settled = False
            status: DiagnosticStatus = get_diagnostic_status()
            for attempt in range(1, _VOLTAGE_REQUEST_MAX_ATTEMPTS + 1):
                self._configure_pdo(voltage_v=voltage_v)

                # Poll for a definitive outcome in one pass: either a PD_RESPONSE
                # failure code (fail fast) or PD_STATUS0 settling on voltage_v
                # (success). Error-level logging is suppressed here
                # (is_final_attempt=False) because whether this is the last
                # attempt depends on the failure reason, known only after the
                # call returns. The definitive log happens below.
                settled, status = self._wait_for_voltage_negotiation(
                    voltage_v, is_final_attempt=False
                )
                if settled:
                    break

                if status.get("pd_response") != _PD_RESPONSE_TRANSACTION_FAIL:
                    break

                if attempt < _VOLTAGE_REQUEST_MAX_ATTEMPTS:
                    oradio_log.debug(
                            "PDO request for %sV attempt %d/%d got Transaction Fail "
                            "(no GoodCRC), retrying",
                        voltage_v, attempt, _VOLTAGE_REQUEST_MAX_ATTEMPTS
                    )
                    sleep(_VOLTAGE_REQUEST_RETRY_DELAY_S)

            if not settled:
                oradio_log.error(
                    "PDO request for %sV did not settle (last pd_response=%s)",
                    voltage_v, status.get("pd_response")
                )
                return False

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
                oradio_log.info("Negotiated %sV @ %.1fA", delivered_v, delivered_a)
            else:
                # Negotiation failed or does not meet requirements
                oradio_log.error(
                    "Negotiation mismatch. Requested %sV (min %.1fA) but got %sV @ %sA",
                    voltage_v, min_current_a, delivered_v, delivered_a
                )
                # Log additional PD response information if available
                if status["pd_response"] is not None:
                    oradio_log.warning(
                        "PD Status register 1: pd_response=%s, cc_dir=%s, attach=%s",
                        status["pd_response"], status["cc_dir"], status["attach"]
                    )

            return success

        def _configure_pdo(self, voltage_v: int) -> None:
            """Write the requested voltage to the HUSB238 SRC_PDO register and trigger negotiation.

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
            """Request standby power: 5 V with a minimum of 3.0 A.

            This mode should only be used when minimal standby power is required.
            The system may enter a throttled state (e.g. Raspberry Pi supply voltage around 4.5 V).

            Returns:
                True if the negotiated PD contract meets the requirements, False otherwise.
            """
            return self._safe_set_voltage(voltage_v=5, min_current_a=3.0)

        def set_nom_voltage(self) -> bool:
            """Request nominal operating power: 9 V with a minimum of 2.0 A.

            Returns:
                True if the negotiated voltage/current meets the requirements.
            """
            return self._safe_set_voltage(voltage_v=9, min_current_a=2.0)

        def set_max_voltage(self) -> bool:
            """Request maximum operating power: 12 V with a minimum of 1.5 A.

            Returns:
                True if the negotiated voltage/current meets the requirements.
            """
            return self._safe_set_voltage(voltage_v=12, min_current_a=1.5)

        def refresh_capabilities(self) -> None:
            """Re-run capability detection.

            Call this if the physical USB-C connection is known to have changed
            (e.g. a hot-plug event) since __init__ or the last refresh, so a newly
            connected supply's capabilities are picked up.
            """
            self._capabilities = self._detect_capabilities_and_settle()

    def print_status() -> None:
        """Read and print every decoded PD status field."""
        status = get_diagnostic_status()

        def raw(value: int | None) -> str:
            """Format a raw register byte for display.

            Args:
                value: Raw register byte, or None when the read failed.

            Returns:
                Hex representation, or a note that the read failed.
            """
            return "read failed" if value is None else f"0x{value:02X}"

        responses = {
            _PD_RESPONSE_NO_RESPONSE: "no response yet - nothing requested since power-up",
            _PD_RESPONSE_SUCCESS: "success",
            **_PD_RESPONSE_MESSAGES,
        }
        pd_response = status["pd_response"]
        response = "unknown" if pd_response is None else responses.get(pd_response, "reserved")

        cc_dir = status["cc_dir"]
        cc_line = "unknown" if cc_dir is None else {0: "CC1", 1: "CC2"}.get(cc_dir, "reserved")

        contract_a = status["contract_5v_current_a"] or "USB default"

        print(
            "\n"
            f"  Contract    : {status['voltage_v']}V @ {status['current_a']}A\n"
            f"  Attach      : {status['attach']}\n"
            f"  CC direction: {cc_line} (only meaningful when attached)\n"
            f"  PD response : {status['pd_response']} - {response}\n"
            f"  5V contract : voltage={status['contract_5v']}, current={contract_a}\n"
            f"  Raw         : PD_STATUS0={raw(status['raw_status0'])}, "
            f"PD_STATUS1={raw(status['raw_status1'])}\n"
        )

    def print_capabilities(power_service: PowerService) -> None:
        """Print the currently known source capabilities.

        Args:
            power_service: Service whose detected capabilities to print.
        """
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

        Instantiates PowerService and loops until the user selects quit (0).
        Options cover the full public API: reading status, requesting each
        voltage profile, and inspecting/refreshing detected source capabilities.
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Read PD status (full diagnostic)\n"
            " 2-set_standby_voltage (5V / >=3.0A)\n"
            " 3-set_nom_voltage (9V / >=2.0A)\n"
            " 4-set_max_voltage (12V / >=1.5A)\n"
            " 5-Show detected capabilities\n"
            " 6-Refresh capabilities\n"
            "Select: "
        )

        power_service = PowerService()
        print_capabilities(power_service)

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print_status()
                case 2:
                    result = power_service.set_standby_voltage()
                    print(f"SetStandbyVoltage: {'OK' if result else 'FAIL'}")
                    print_status()
                case 3:
                    result = power_service.set_nom_voltage()
                    print(f"SetNomVoltage: {'OK' if result else 'FAIL'}")
                    print_status()
                case 4:
                    result = power_service.set_max_voltage()
                    print(f"SetMaxVoltage: {'OK' if result else 'FAIL'}")
                    print_status()
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
