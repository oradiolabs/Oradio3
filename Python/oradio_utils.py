#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
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
import urllib.request
import subprocess
from vcgencmd import Vcgencmd
from pydantic import BaseModel, EmailStr, Field, create_model
from typing import Dict, Any, Optional
import json

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
def is_service_active(service_name):
    '''
    Check if service is running
    :param service_name = name of the service
    :return True/False : True when active
    '''
    try:
        # Run systemctl is-active command
        result = subprocess.run(
            ["sudo","systemctl", "is-active", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.stdout.strip() == "active"
    except Exception as err:
        oradio_log.error(f"Error checking {service_name} service, error-status=: {err}")
        return False


def json_schema_to_pydantic(name: str, schema: Dict[str,Any]) -> BaseModel:
    '''
    Dynamic Model generation based on a JSON schema
    '''
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
    '''
    Create a object based model derived from the json schema
    :param model_name [str] = name of model in schema
    :return model 
    :return status = 
    '''
    # Load the JSON schema file
    with open(JSON_SCHEMAS_FILE) as f:
        schemas = json.load(f)
    if model_name not in schemas:
        status = MODEL_NAME_NOT_FOUND
        Messages = None
    else:
        status = MODEL_NAME_FOUND
        # Dynamically create Pydantic models
        models = {name: json_schema_to_pydantic(name, schema) for name, schema in schemas.items()}
        # create Messages model
        Messages = models[model_name]
    return(status, Messages)

def check_internet_connection():
    """
    Check if there is an internet connection ==> True | False
    :return status  - True: connected to the internet
                    - False: not connected to the internet
    """
    try:
        urllib.request.urlopen("http://google.com")
        return True
    except urllib.error.URLError:
        return False

def get_throttled_state_rpi():
    """
    Get the state of the throttled flags available in vcgencmd module
    :return flags = the full throttled state flags of the system in JSON format. 
    This is a bit pattern - a bit being set indicates the following meanings:
        Bit     Meaning
        0     Under-voltage detected
        1     Arm frequency capped
        2     Currently throttled
        3     Soft temperature limit active
        16     Under-voltage has occurred
        17     Arm frequency capping has occurred
        18     Throttling has occurred
        19     Soft temperature limit has occurred

        A value of zero indicates that none of the above conditions is true.
        The last four bits (3..0) are checked and when one of them are set the 
        throttled_state is set to True
    :return if one of bits is set ==> throttled_state = True, else False
    """
    vcgm = Vcgencmd()
    throttled_state = vcgm.get_throttled()
    flags = int( throttled_state.get('binary'),2) # convert binary string to integer
    last_four_bits = flags & 0xF
    if last_four_bits > 0:
        # a new flag was set
        throttled_state = True
    else:
        throttled_state = False

    return throttled_state, flags

def run_shell_script(script):
    """
    Simplified shell command execution
    :param script (str) - shell command to execute
    Returns exit status and output of running the script
    """
    oradio_log.debug("Runnning shell script: %s", script)
    process = run(script, shell = True, capture_output = True, encoding = 'utf-8')
    if process.returncode != 0:
        oradio_log.error("shell script error: %s", process.stderr)
        return False, process.stderr
    return True, process.stdout

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Show internet connection status\n"
                       " 2-Show throttled status\n"
                       " 3-Run shell script('ls')\n"
                       " 4-Run shell script('xxx')\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print(f"\nConnected to internet: {check_internet_connection()}\n")
            case 2:
                print(f"\nthrottled: {get_throttled_state_rpi()}\n")
            case 3:
                result, output = run_shell_script("ls")
                print(f"\nExpect ok: result={result}, output={output}")
            case 4:
                result, error = run_shell_script("xxx")
                print(f"\nExpect fail: result={result}, error={error}")
            case _:
                print("\nPlease input a valid number\n")
