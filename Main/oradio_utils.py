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
@summary: Class for USB detect, insert, and remove services
    :Note
    :Install
    :Documentation
        https://docs.python.org/3/howto/logging.html
        https://pypi.org/project/concurrent-log-handler/
"""
import json
import socket
import subprocess
from subprocess import run
from typing import Any, Optional, List, Union, Dict
from pathlib import Path
from pydantic import BaseModel, ValidationError
import netifaces

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
    PRESETS_FILE,
    USB_SYSTEM,
)

##### LOCAL constants ####################
JSON_SCHEMAS_PATH = Path(__file__).parent.resolve()
JSON_SCHEMAS_FILE = JSON_SCHEMAS_PATH / "schemas.json"
class OradioMessage(BaseModel):
    '''
    The basemodel for the OradioMessage to standardize the message when
    used in the shared-queue of Oradio.
    '''
    source: str
    state: str
    error: str
    data: Optional[List[Any]] = None

INTERFACE   = "wlan0"           # Raspberry Pi wireless interface
DNS_TIMEOUT = 0.5               # seconds
DNS_HOST    = ("8.8.8.8", 53)   # google.com

def get_serial() -> str:
    """Extract serial from Raspberry Pi."""
    cmd = "vcgencmd otp_dump"
    result, response = run_shell_script(cmd)

    if not result:
        oradio_log.error("Error during <%s> to get serial number, error: %s", cmd, response)
        return "Unknown"

    # Parse the output in Python
    for line in response.splitlines():
        if line.startswith("28:"):
            serial = line[3:].strip()
            return serial or "Unknown"

    return "Unknown"

def safe_put(queue, msg, block=True, timeout=None):
    """
    Safely put a message into a multiprocessing.Queue.

    Args:
        queue (multiprocessing.Queue): The queue.
        msg (list): The object to put.
        block (bool): Whether to block if the queue is full.
        timeout (float|None): Timeout for blocking put.

    Returns:
        bool: True if the message was put successfully, False otherwise.
    """
    try:
        queue.put(msg, block=block, timeout=timeout)
        return True

    except queue.Full:
        oradio_log.warning("Queue is full — dropping message: %r", msg)
        return False

    except (OSError, EOFError, ValueError) as ex_err:
        # Queue closed or broken
        oradio_log.error("Queue is closed/broken — failed to put message: %r (%s)", msg, ex_err)
        return False

    except AssertionError as ex_err:
        # Rare internal queue corruption
        oradio_log.critical("Queue internal error: %s", ex_err, exc_info=True)
        return False

def is_service_active(service_name):
    """
    Check if systemd service is running
    :param service_name: Name of the service
    :return: True if service is active, False otherwise
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
        oradio_log.error("Error checking %s service, error-status=: %s", service_name, ex_err)
        return False

def validate_oradio_message(message: Union[OradioMessage, Dict[str, Any]]) -> Optional[OradioMessage]:
    """
    Validates a message to ensure it matches the OradioMessage schema.
    If the message is already an OradioMessage, it is returned as-is.
    :argument
        message : message formatted as a dictionary or as OradioMessage
    :return
        validated_message = when message is correct
        validated_messsage = None, when not according OradioMessage structure
    """
    if isinstance(message, OradioMessage):
        # Message is already validated; return it directly
        return message

    try:
        # Message is a dictionary; validate it
        validated_message = OradioMessage(**message)
        oradio_log.debug("Message is valid: %s",validated_message)
        return validated_message
    except ValidationError as err:
        oradio_log.error("Message does not match OradioMessage schema %s:",err)
        return None

# handle the error
def has_internet() -> bool:
    """
    Quickly check if the given interface has internet access.
    Uses a TCP connection to a known DNS server bound to the interface's IP.
    NOTE: ping is NOT reliable because the network interface uses power management.

    Returns:
        bool: True if internet is reachable from this interface, False otherwise.
    """
    # NOTE: Do not log to avoid getting stuck in an infinite loop in logging handler
    try:
        # Get IPv4 address of the interface
        addrs = netifaces.ifaddresses(INTERFACE)
        src_ip = addrs[netifaces.AF_INET][0]['addr']
        # Create a TCP socket and bind it to the interface's IP
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # Use any free source port
            sock.bind((src_ip, 0))
            # Set timeout for trying to connect
            sock.settimeout(DNS_TIMEOUT)
            # Attempt connection to DNS server
            sock.connect(DNS_HOST)
        return True
    except (socket.timeout, socket.error, KeyError):
        # KeyError if interface has no IPv4
        # socket.timeout / socket.error if connection fails
        return False

def run_shell_script(script):
    """
    Simplified shell command execution
    :param script (str) - shell command to execute
    :return: (success, output) tuple
             success=True -> output = stdout (stripped)
             success=False -> output = stderr (stripped)
    """
    oradio_log.debug("Running shell script: %s", script)
    process = run(
        script,
        shell = True,           # Avoid exception, inspect returncode and stdout/stderr
        capture_output = True,
        text = True,
        check = False           # Avoid exception, inspect returncode and stdout/stderr
    )
    if process.returncode != 0:
        return False, process.stderr.strip()
    return True, process.stdout.strip()

def load_presets() -> dict[str, str]:
    """
    Retrieve the playlist names associated with the presets from a JSON file.

    Returns:
        dict[str, str]: A dictionary mapping lowercase preset_key -> listname.
                        If a preset value is missing or invalid, listname will be an empty string "".
                        Keys are normalized to lowercase for case-insensitive lookup.
    """
    try:
        with open(PRESETS_FILE, 'r', encoding='utf-8') as file:
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

        # Normalize the listname: strip whitespace if string, else empty string
        listname = raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else ""
        if not listname:
            oradio_log.warning("Preset '%s' is missing or has an empty listname in %s", key, PRESETS_FILE)

        # Store in dictionary using lowercase key for case-insensitive lookups
        presets_dict[key.lower()] = listname

    oradio_log.debug("Presets loaded (case-insensitive): %s", presets_dict)
    return presets_dict

def store_presets(presets: dict[str, str]):
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

    # Prepare the data to save, ensuring all expected keys exist
    data_to_save = {}
    for key in ["preset1", "preset2", "preset3"]:
        # Fetch raw value from JSON, default to empty string if missing
        raw_value = presets.get(key, "")

        # Normalize the listname: strip whitespace if string, else empty string
        listname = raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else ""

        # Store in dictionary using lowercase key for case-insensitive lookups
        data_to_save[key] = listname

    # Write the JSON file
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as file:
            json.dump(data_to_save, file, indent=4)
        oradio_log.debug("Presets '%s' successfully saved to %s", data_to_save, PRESETS_FILE)
    except IOError as ex_err:
        oradio_log.error("Failed to write presets to '%s'. Error: %s", PRESETS_FILE, ex_err)

def input_prompt_int(prompt: str, default=-1 ) -> int:
    """
    Prompt for an user input and return int value of number typed
    :Args 
        prompt : prompt text for user
        default: default value to return in case of an error
    :Returns
        the integer value type in by user | default value in case of an error
    """
    try:
        return int(input(prompt))
    except ValueError:
        return default

def input_prompt_float(prompt: str, default: float | None = None) -> float | None:
    """
    Prompt for an user input and return float value of number typed
    :Args
        prompt : prompt text for user
        default: default value to return in case of an error
    :Returns
        the ifloat value type in by user | default value in case of an error
    """   
    try:
        return float(input(prompt))
    except ValueError:
        return default

# Entry point for stand-alone operation
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
            " 3-Run shell script('xxx')\n"
            "Select: "
        )

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
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
                    print("\nPlease input a valid number\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
