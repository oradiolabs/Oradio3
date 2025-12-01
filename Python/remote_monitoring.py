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
import glob
import json
from datetime import datetime
from threading import Timer, Lock
from platform import python_version
from requests.exceptions import RequestException
from contextlib import ExitStack
from requests import post
import subprocess
import logging

##### oradio modules ####################
from singleton import singleton
from oradio_utils import has_internet

##### GLOBAL constants ####################
from oradio_const import (
    ORADIO_LOGGER,
    ORADIO_LOG_DIR,
    ORADIO_LOG_LEVEL,
)

##### LOCAL constants ####################
# Message types
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
WARNING   = 'WARNING'
ERROR     = 'ERROR'
# Remote Monitoring Service URL
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
# Software version info file
SW_LOG_FILE = "/var/log/oradio_sw_version.log"
# HEARTBEAT repeat time
HEARTBEAT_REPEAT_TIME = 60 * 60     # 1 hour in seconds
# Timeout for ORMS POST request
REQUEST_TIMEOUT = 30
# Flag to ensure only 1 heartbeat repeat timer is active
HEARTBEAT_REPEAT_TIMER_IS_RUNNING = False

# Local logger to prevent recurrence
_rms_logger = logging.getLogger("ORADIO_LOGGER")
#_rms_logger.setLevel(ORADIO_LOG_LEVEL)
#_rms_logger.propagate = False

def _get_serial() -> str:
    """Extract serial from Raspberry Pi."""
    return os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-').read().strip()

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
        return "SW version missing or invalid"

#REVIEW Onno: Beter om commando naar oradio_control te sturen die het commando laten uitvoeren. Dan kan de gebruiker ook netjes geinformeerd worden
def _handle_response_command(response_text) -> None:
    """Check for 'command =>' in server response and execute if present"""
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
        _rms_logger.debug("Run command '%s' from RMS server", command)
        try:
            # executable need to be set, othewise python uses sh. Text converts the result into reable
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                check=True,
                executable="/usr/bin/bash",
                text=True
            )
            _rms_logger.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            _rms_logger.error("shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s", command, ex_err.returncode, ex_err.stdout, ex_err.stderr)

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
                _rms_logger.error("Heartbeat execution failed: %s", ex_err)

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
    - WARNING and ERROR log messages accompnied by the log file
    """
    def __init__(self):
        """Setup rms service class variables."""
        self.serial = _get_serial()
        self.send_files = None

        # Start the singleton heartbeat timer
        self.start_heartbeat()

    def start_heartbeat(self):
        """Start the heartbeat timer."""
        Heartbeat.start_heartbeat(
            HEARTBEAT_REPEAT_TIME,
            self.send_message,
            args=(HEARTBEAT,)
        )

    def send_sys_info(self) -> None:
        """ Wrapper to simplify oradio control """
        self.send_message(SYS_INFO)

    def send_message(self, msg_type, message=None, function=None) -> None:
        """
        Format and send message to Remote Monitoring Service.

        Returns:
            bool: True if message sent, False if not.
        """

        # Test if connected
        if not has_internet():
            # Log the message locally since sending to RMS failed
            _rms_logger.error("No internet connection")
            return
        
        # Base message
        msg_data = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial': self.serial,
            'type': msg_type
        }

        # HEARTBEAT message builder
        def build_heartbeat():
            self.send_files = None
            return {'temperature': _get_temperature()}

        # SYS_INFO message builder
        def build_sys_info():
            self.send_files = None
            return {
                'sw_version': _get_sw_version(),
                'python': python_version(),
                'rpi': _get_rpi_version(),
                'rpi-os': _get_os_version(),
            }

        # WARNING / ERROR message builder
        def build_warning_error():
            self.send_files = glob.glob(f"{ORADIO_LOG_DIR}/*.log")
            return {'function': function, 'message': message}

        message_handlers = {
            HEARTBEAT: build_heartbeat,
            SYS_INFO: build_sys_info,
            WARNING: build_warning_error,
            ERROR: build_warning_error,
        }

        # Validate handler exists
        handler = message_handlers.get(msg_type)
        if not handler:
            # Log the message locally since sending to RMS failed
            _rms_logger.error("Unsupported message type: %s", msg_type)
            return

        # Get message to send
        msg_data['message'] = json.dumps(handler())

        # Send message safely
        try:
            if not self.send_files:
                response = post(RMS_SERVER_URL, data=msg_data, timeout=REQUEST_TIMEOUT)
            else:
                with ExitStack() as stack:
                    files = {
                        path: (path, stack.enter_context(open(path, "rb")))
                        for path in self.send_files
                    }
                    response = post(RMS_SERVER_URL, data=msg_data, files=files, timeout=REQUEST_TIMEOUT)

        except RequestException as ex_err:
            # Catch all network-related exceptions (offline, timeout, DNS error)
            # Log the message locally since sending to RMS failed
            _rms_logger.error("Network error while sending message: %s", ex_err)
            return

        # Response handling
        if response.status_code != 200:
            _rms_logger.error("Status code=%s, response.headers=%s", response.status_code, response.headers)
            return

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
            " 3-Test warning\n"
            " 4-Test error\n"
            " 5-Restart heartbeat\n"
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
                    rms.send_message(HEARTBEAT)
                case 2:
                    print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                    rms.send_sys_info()
                case 3:
                    print("\nSend WARNING test message to Remote Monitoring Service...\n")
                    rms.send_message(WARNING, 'test warning message', 'filename:lineno')
                case 4:
                    print("\nSend ERROR test message to Remote Monitoring Service...\n")
                    rms.send_message(ERROR, 'test error message', 'filename:lineno')
                case 5:
                    print("\nRestarting heartbeat... Check ORMS for heartbeats\n")
                    rms.start_heartbeat()
                case _:
                    print("\nPlease input a valid number\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
