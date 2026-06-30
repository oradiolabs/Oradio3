#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Miscellaneous Oradio utility functions
    Following services provided:
        * Raspberry Pi serial number lookup
        * systemd service status check
        * Internet connectivity check
        * Generic shell command execution
        * Loading and storing presets.json
        * Console input prompting with type conversion and a default fallback
"""
import json
import socket
import subprocess
from pathlib import Path
from typing import TypeVar, Callable

##### Oradio modules ######################################
from log_service import oradio_log

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    PRESETS_FILE,
    USB_SYSTEM,
)

##### LOCAL constants #####################################
DNS_HOST    = "google.com"
DNS_TIMEOUT = 0.5               # seconds; short on purpose - callers should
                                 # fail fast rather than block on a flaky or
                                 # just-woken Wi-Fi radio.

# Row prefix used by `vcgencmd otp_dump` for the Raspberry Pi serial number.
SERIAL_OTP_ROW = "28:"

T = TypeVar("T")

def get_serial() -> str:
    """Extract serial from Raspberry Pi."""
    cmd = "vcgencmd otp_dump"
    result, response = run_shell_script(cmd)

    if not result:
        oradio_log.error("Error during <%s> to get serial number, error: %s", cmd, response)
        return "Unknown"

    # Parse the output in Python
    for line in response.splitlines():
        if line.startswith(SERIAL_OTP_ROW):
            serial = line[len(SERIAL_OTP_ROW):].strip()
            return serial or "Unknown"

    return "Unknown"

def is_service_active(service_name) -> bool:
    """
    Check if systemd service is running
    Args:
        service_name (str): Name of the service
    Returns:
        bool: True if service is active, False otherwise
    """
    try:
        # Run systemctl is-active command
        result = subprocess.run(
            ["sudo", "systemctl", "is-active", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Error checking %s service, error-status: %s", service_name, ex_err)
        return False

def has_internet():
    """
    Try whether the wifi-connection has internet by using a DNS service to resolve a domain name.
    As domain name is used google.com, which is one of the most reliable and globally available domains.
    This will resolve into a IPv4 address,to test DNS and networking connectivity using UDP Port 53.
    DNS lookups are high-priority traffic and typically wake the Wi-Fi radio from power-saving mode.

    Note:
        socket.gethostbyname() always uses the process-wide default socket
        timeout (set via socket.setdefaulttimeout()); it is not a
        socket-object method, so a per-call timeout cannot be passed
        directly. The previous default timeout is saved and restored
        around the call so this function does not permanently change
        timeout behaviour for other sockets created elsewhere in the
        process.

    Returns:
        bool: True if internet is reachable, False otherwise.
    """
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(DNS_TIMEOUT)
    try:
        _ = socket.gethostbyname(DNS_HOST)
        oradio_log.info("Internet available")
        return True
    except (socket.gaierror, socket.timeout) as ex_err:
        oradio_log.debug("Internet not available: %s", ex_err)
        return False
    finally:
        socket.setdefaulttimeout(previous_timeout)

def run_shell_script(script):
    """
    Simplified shell command execution
    Args:
        script (str) - shell command to execute
    Returns:
        (success, output) tuple
             success=True -> output = stdout (stripped)
             success=False -> output = stderr (stripped)
    """
    oradio_log.debug("Running shell script: %s", script)
    try:
        process = subprocess.run(
            script,
            shell = True,           # Avoid exception, inspect returncode and stdout/stderr
            capture_output = True,
            text = True,
            check = False           # Avoid exception, inspect returncode and stdout/stderr
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Error running shell script <%s>, error: %s", script, ex_err)
        return False, str(ex_err)

    if process.returncode != 0:
        return False, process.stderr.strip()
    return True, process.stdout.strip()

def _normalize_listname(raw_value) -> str:
    """
    Normalize a raw preset value into a clean listname string.

    Args:
        raw_value: Value to normalize, expected to be a str but tolerates
            other/missing types.

    Returns:
        str: The stripped string if raw_value is a non-blank string,
            otherwise an empty string.
    """
    return raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else ""

def load_presets() -> dict[str, str]:
    """
    Retrieve the playlist names associated with the presets from a JSON file.
    Returns:
        dict[str, str]: A dictionary mapping lowercase preset_key -> listname.
                        If a preset value is missing or invalid, listname will be an empty string "".
                        Keys are normalized to lowercase for case-insensitive lookup.
    """
    try:
        with open(PRESETS_FILE, encoding='utf-8') as file:
            presets = json.load(file)
            if not isinstance(presets, dict):
                oradio_log.error("Invalid JSON format in %s: expected dict", PRESETS_FILE)
                return {}
    except FileNotFoundError:
        oradio_log.error("File not found at %s", PRESETS_FILE)
        return {}
    except json.JSONDecodeError:
        oradio_log.error("Failed to JSON decode %s", PRESETS_FILE)
        return {}

    # Ensure all expected keys exist and are normalized
    presets_dict = {}
    for key in ["preset1", "preset2", "preset3"]:
        # Fetch raw value from JSON, default to empty string if missing
        raw_value = presets.get(key, "")
        listname = _normalize_listname(raw_value)
        if not listname:
            oradio_log.warning("Preset '%s' is missing or has an empty listname in %s", key, PRESETS_FILE)

        # Store in dictionary using lowercase key for case-insensitive lookups
        presets_dict[key.lower()] = listname

    oradio_log.debug("Presets loaded (case-insensitive): %s", presets_dict)
    return presets_dict

def store_presets(presets: dict[str, str]) -> None:
    """
    Save the provided presets dictionary to the presets.json file in the USB_SYSTEM folder.

    Args:
        presets (dict): Dictionary containing keys 'preset1', 'preset2', 'preset3' with playlist values.
    """
    # Ensure the USB_SYSTEM directory exists
    try:
        Path(USB_SYSTEM).mkdir(parents=True, exist_ok=True)
    except OSError as ex_err:
        oradio_log.error("Presets cannot be saved. Error: %s", ex_err)
        return

    # Prepare the data to save, ensuring all expected keys exist.
    # Keys are already lowercase literals here, so no case normalization
    # of the key itself is needed (unlike load_presets' lookup from
    # arbitrary JSON input).
    data_to_save = {}
    for key in ["preset1", "preset2", "preset3"]:
        # Fetch raw value from JSON, default to empty string if missing
        raw_value = presets.get(key, "")
        data_to_save[key] = _normalize_listname(raw_value)

    # Write the JSON file
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as file:
            json.dump(data_to_save, file, indent=4)
        oradio_log.debug("Presets '%s' successfully saved to %s", data_to_save, PRESETS_FILE)
    except IOError as ex_err:
        oradio_log.error("Failed to write presets to '%s'. Error: %s", PRESETS_FILE, ex_err)

def input_prompt(prompt: str, converter: Callable[[str], T], default: T) -> T:
    """
    Prompt the user for input and convert it to the requested type.

    Args:
        prompt: Prompt shown to the user.
        converter: Conversion function (e.g. int, float).
        default: Value returned if conversion fails.

    Returns:
        Converted value or the default.
    """
    try:
        return converter(input(prompt))
    except (ValueError, EOFError):
        return default

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu():
        """Show menu with test options"""

        # Show menu with test options
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Show internet connection status\n"
            " 2-Run shell script('ls')\n"
            " 3-Run shell script('xxx')  [intentionally invalid command, exercises the failure path]\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print(f"\nConnected to internet: {has_internet()}\n")
                case 2:
                    result, response = run_shell_script("ls")
                    if result:
                        print(f"\nresult={result}, response={response}")
                    else:
                        print(f"\n{YELLOW}Unexpected result: result={result}, response={response}{NC}")
                case 3:
                    result, response = run_shell_script("xxx")
                    if not result:
                        print(f"\nresult={result}, response={response}")
                    else:
                        print(f"\n{YELLOW}Unexpected result: result={result}, response={response}{NC}")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
