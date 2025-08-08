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

##### GLOBAL constants ####################
from oradio_const import (
    JSON_SCHEMAS_FILE,
    MODEL_NAME_FOUND,
    MODEL_NAME_NOT_FOUND,
)

# We cannot use from oradio_logging import oradio_log as this creates a circular import
# Solution is to get the logger gives us the same logger-object
oradio_log = logging.getLogger("oradio")

##### LOCAL constants ####################

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
    :return: True if service is active, /False otherwise
    """
    try:
        # Run systemctl is-active command
        result = subprocess.run(
            ["sudo","systemctl", "is-active", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return result.stdout.strip() == "active"
    except Exception as ex_err: # pylint: disable=broad-exception-caught
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

def check_internet_connection():
    """
    Check if the system has internet access checking Google DNS
    :return: True if connected to the internet, False otherwise
    """
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=3):
            return True
    except OSError:
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
        oradio_log.error("shell script error: %s", process.stderr)
        return False, process.stderr.strip()
    return True, process.stdout

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Show internet connection status\n"
                       " 2-Run shell script('ls')\n"
                       " 3-Run shell script('xxx')\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            FunctionNr = int(input(INPUT_SELECTION)) # pylint: disable=invalid-name
        except ValueError:
            FunctionNr = -1 # pylint: disable=invalid-name

        # Execute selected function
        match FunctionNr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print(f"\nConnected to internet: {check_internet_connection()}\n")
            case 2:
                response, output = run_shell_script("ls")
                print(f"\nExpect ok: response={response}, output={output}")
            case 3:
                response, error = run_shell_script("xxx")
                print(f"\nExpect fail: response={response}, error={error}")
            case _:
                print("\nPlease input a valid number\n")
