#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on February 8, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Send log messages to remote monitoring service
"""
import os
import re
import json
import subprocess
from time import sleep
from datetime import datetime
from threading import Timer, Lock
from platform import python_version
from requests import post, RequestException, Timeout

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from oradio_utils import get_serial, has_internet

##### GLOBAL constants ####################

##### LOCAL constants ####################
# Message types
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
# Software version info file
SW_LOG_FILE = "/var/log/oradio_sw_version.log"
# HEARTBEAT repeat time
HEARTBEAT_REPEAT_TIME = 60 * 60     # 1 hour in seconds
# Flag to ensure only 1 heartbeat repeat timer is active
HEARTBEAT_REPEAT_TIMER_IS_RUNNING = False
# Remote Monitoring Service
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
MAX_RETRIES    = 3
BACKOFF_FACTOR = 2  # Exponential backoff multiplier
POST_TIMEOUT   = 5  # seconds

def _get_temperature() -> str:
    """Extract SoC temperature from Raspberry Pi."""
    return os.popen('vcgencmd measure_temp | cut -c 6-9').read().strip()

def _get_rpi_version() -> str:
    """Get the Raspberry Pi version."""
    return os.popen("cat /proc/cpuinfo | grep Model | cut -d':' -f2").read().strip()

def _get_os_version() -> str:
    """Get the operating system version."""
    return os.popen("lsb_release -a | grep 'Description:' | cut -d':' -f2").read().strip()

def _get_sw_version() -> str:
    """Read the contents of the SW serial number file."""
    try:
        with open(SW_LOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

#REVIEW Onno: Better send command to oradio_control where user can be informed, progress monitored
def _handle_response_command(response_text) -> None:
    """Check for 'command =>' in server response and execute if present"""
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
        oradio_log.debug("Run command '%s' from RMS server", command)
        try:
            # executable need to be set, othewise python uses sh. Text converts the result into readable string
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                check=True,
                executable="/usr/bin/bash",
                text=True
            )
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s", command, ex_err.returncode, ex_err.stdout, ex_err.stderr)

@singleton
class Heartbeat(Timer):
    """Process-wide singleton auto-repeating timer."""

    # Lock for start/stop operations
    start_lock = Lock()

    def __init__(self, interval, function, args=None, kwargs=None):
        """Initialize Timer"""
        super().__init__(interval, function, args=args, kwargs=kwargs)

    def run(self) -> None:
        """Call function immediately, then repeat at intervals."""
        while not self.finished.is_set():
            try:
                # Call function immediately at first iteration and every interval
                self.function(*self.args, **self.kwargs)
            # We don't know what exception the callback can raise, so we need to catch all exceptions as we don't want to stop
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Heartbeat execution failed: %s", ex_err)

            # Wait for interval before next iteration
            if self.finished.wait(self.interval):
                break

    @classmethod
    def start_heartbeat(cls, interval, function, args=None, kwargs=None):
        """Stop the current timer if running, then start a new timer."""
        # Cancel existing timer if it exists
        with cls.start_lock:
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None

            # Create a new timer
            cls.instance = cls(interval, function, args=args, kwargs=kwargs)
            # makes it exit with the main program
            cls.instance.daemon = True
            # start the timer
            cls.instance.start()

class RMService:
    """
    Manage communication with Oradio Remote Monitoring Service (ORMS):
    - HEARTBEAT messages as sign of life
    - SYS_INFO to identify the Oradio to ORMS
    """
    def __init__(self):
        """Setup rms service class variables."""
        self.serial = get_serial()

        # Start the singleton heartbeat timer
        self.start_heartbeat()

    def start_heartbeat(self):
        """Start the heartbeat timer."""
        Heartbeat.start_heartbeat(HEARTBEAT_REPEAT_TIME, self.send_heartbeat)
        oradio_log.debug("heartbeat timer started")

    def send_heartbeat(self) -> None:
        """ Wrapper to simplify oradio control """
        self.send_message(HEARTBEAT)

    def send_sys_info(self) -> None:
        """ Wrapper to simplify oradio control """
        self.send_message(SYS_INFO)

    def send_message(self, msg_type) -> bool:
        """Format message based on type and if connected to the internet: send message to Remote Monitoring Service."""

        # Messages are lost if not connected to internet
        if not has_internet():
            oradio_log.debug("No internet connection")
            return

        # Initialze message to send
        payload_info = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : get_serial(),
            'type'     : msg_type
        }

        # Compile HEARTBEAT message
        if msg_type == HEARTBEAT:
            payload_info['message'] = json.dumps({'temperature': _get_temperature()})

        # Compile SYS_INFO message
        elif msg_type == SYS_INFO:
            payload_info['message'] = json.dumps({
                'sw_version': _get_sw_version(),
                'python'    : python_version(),
                'rpi'       : _get_rpi_version(),
                'rpi-os'    : _get_os_version(),
            })

        # Unexpected message type
        else:
            oradio_log.error("Unsupported message type: %s", msg_type)

        # Retry loop
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Send POST without files
                response = post(RMS_SERVER_URL, data=payload_info, files=None, timeout=POST_TIMEOUT)
                # Check for any errors
                response.raise_for_status()
                # Success, exit retry loop
                break
            except (RequestException, Timeout) as ex_err:
                oradio_log.warning("Attempt %d failed: %s", attempt, ex_err)
                if attempt == MAX_RETRIES:
                    oradio_log.error("Failed to POST log: %s", ex_err)
                    return
                sleep(BACKOFF_FACTOR ** (attempt - 1))

        # Check for errors
        if response.status_code != 200:
            oradio_log.error("Status code=%s, response.headers=%s", response.status_code, response.headers)

        # Check for command in RMS response
        _handle_response_command(response.text)

if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def interactive_menu():
        """Show menu with test options"""
        # Instantiate RMS service
        rms = RMService()

        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Test heartbeat\n"
            " 2-Test sys_info\n"
            " 3-Restart heartbeat\n"
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
                    print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                    rms.send_heartbeat()
                case 2:
                    print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                    rms.send_sys_info()
                case 3:
                    print("\nRestarting heartbeat... Check ORMS for heartbeats\n")
                    rms.start_heartbeat()
                case _:
                    print("\nPlease input a valid number\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
