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
import logging
import subprocess
from subprocess import run
from typing import Any, Optional
from pydantic import BaseModel, create_model
import netifaces

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
    JSON_SCHEMAS_FILE,
    MODEL_NAME_FOUND,
    MODEL_NAME_NOT_FOUND,
)

# We cannot use from oradio_logging import oradio_log as this creates a circular import
# Solution is to get the logger gives us the same logger-object
oradio_log = logging.getLogger("oradio")

##### LOCAL constants ####################
INTERFACE = "wlan0"
# ping constants
PING_TIMEOUT = 1
PING_HOST    = "8.8.8.8"  # google.com
# TCP DNS constants
DNS_TIMEOUT = 3
DNS_HOST    = ("8.8.8.8", 53)

def safe_put(queue, item, block=True, timeout=None):
    """
    Safely put an item into a multiprocessing.Queue.

    Args:
        queue (multiprocessing.Queue): The queue.
        item: The object to put.
        block (bool): Whether to block if the queue is full.
        timeout (float|None): Timeout for blocking put.

    Returns:
        bool: True if the item was put successfully, False otherwise.
    """
    try:
        queue.put(item, block=block, timeout=timeout)
        return True

    except queue.Full:
        oradio_log.warning("Queue is full — dropping item: %r", item)
        return False

    except (OSError, EOFError, ValueError) as ex_err:
        # Queue closed or broken
        oradio_log.error("Queue is closed/broken — failed to put item: %r (%s)", item, ex_err)
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

def json_schema_to_pydantic(name: str, schema: dict[str,Any]) -> BaseModel:
    """
    Dynamic Model generation based on a JSON schema
    """
    if "properties" not in schema:  # Skip first entry
        return None
    fields ={}
    required_fields = set(schema.get("required", []))  # Get required fields from schema

    for prop, details in schema["properties"].items():
        field_type = str  # Default type
        if details["type"] == "integer":
            field_type = int
        elif details["type"] == "boolean":
            field_type = bool
        elif details["type"] == "number":
            field_type = float
        elif details["type"] == "array":
            field_type = list
        if "required" in schema and prop in schema["required"]:
            fields[prop] = (field_type, ...)
        else:
            fields[prop] = (field_type, None)

        # Handle optional fields (not in "required")
        if prop not in required_fields:
            fields[prop] = (Optional[field_type], None)  # Mark as Optional
        else:
            fields[prop] = (field_type, ...)  # Required fields

    return create_model(name, **fields)

def create_json_model(model_name):
    """
    Create a object based model derived from the json schema
    :param model_name [str] = name of model in schema
    :return model
    :return status =
    """
    # Load the JSON schema file
    with open(JSON_SCHEMAS_FILE, encoding="utf-8") as file:
        schemas = json.load(file)
    if model_name not in schemas:
        status = MODEL_NAME_NOT_FOUND
        messages = None
    else:
        status = MODEL_NAME_FOUND
        # Dynamically create Pydantic models
        models = {name: json_schema_to_pydantic(name, schema) for name, schema in schemas.items()}
        # create messages model
        messages = models[model_name]
    return(status, messages)

def has_internet():
    """
    Hybrid internet check on wireless interface:
    First check NetworkManager has connection
    Second if connected do ping (fast)
    Third if ping fails do DNS check (robust)
    """
    # First: NetworkManager
    cmd = f"nmcli -t -f DEVICE,STATE device status | grep -q '^{INTERFACE}:connected'"
    result, _ = run_shell_script(cmd)
    if not result:
        oradio_log.debug("No network connection")
        return False

    # Second: ping
    cmd = f"ping -I {INTERFACE} -c 1 -W {PING_TIMEOUT} {PING_HOST}"
    result, _ = run_shell_script(cmd)
    if result:
        # Has internet
        return True
    oradio_log.debug("ping failed, check DNS")

    # Third: DNS
    try:
        # Get IPv4-address
        addrs = netifaces.ifaddresses(INTERFACE)
        src_ip = addrs[netifaces.AF_INET][0]['addr']
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((src_ip, 0))  # Any source port
            sock.settimeout(DNS_TIMEOUT)
            sock.connect(DNS_HOST)
        return True
    except (socket.timeout, socket.error, KeyError):
        oradio_log.debug("DNS failed: no internet")
        return False

def run_shell_script(script):
    """
    Simplified shell command execution
    :param script (str) - shell command to execute
    Returns exit status and output of running the script
    """
    oradio_log.debug("Runnning shell script: %s", script)
    process = run(script, shell = True, capture_output = True, encoding = 'utf-8', check = False)
    if process.returncode != 0:
        return False, process.stderr.strip()
    return True, process.stdout

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
