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
from platform import python_version
from datetime import datetime
from threading import Timer
import subprocess
import logging
import requests

##### oradio modules ####################
from singleton import singleton
from oradio_utils import has_internet

##### GLOBAL constants ####################
from oradio_const import ORADIO_LOG_DIR

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

# We cannot use from oradio_logging import oradio_log as this creates a circular import
# Solution is to get the logger gives us the same logger-object
oradio_log = logging.getLogger("oradio")

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
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

def _handle_response_command(response_text) -> None:
    """Check for 'command =>' in server response and execute if present"""
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
        oradio_log.debug("Run command '%s' from RMS server", command)
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
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s", command, ex_err.returncode, ex_err.stdout, ex_err.stderr)

@singleton
class Heartbeat(Timer):
    """Process-wide singleton auto-repeating timer."""

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

    def send_message(self, msg_type, message = None, function = None) -> None:
        """
        Format message based on type
        If connected to the internet: send message to Remote Monitoring Service
        """
        # Messages are lost if not connected to internet
        if not has_internet():
            return

        # Initialze message to send
        msg_data = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : self.serial,
            'type'     : msg_type
        }

        # Compile HEARTBEAT message
        if msg_type == HEARTBEAT:
            msg_data['message'] = json.dumps({'temperature': _get_temperature()})
            self.send_files = None

        # Compile SYS_INFO message
        elif msg_type == SYS_INFO:
            msg_data['message'] = json.dumps({
                'sw_version': _get_sw_version(),
                'python'    : python_version(),
                'rpi'       : _get_rpi_version(),
                'rpi-os'    : _get_os_version(),
            })
            self.send_files = None

        # Compile WARNING and ERROR message
        elif msg_type in (WARNING, ERROR):
            msg_data['message'] = json.dumps({'function': function, 'message': message})
            # Send all log files in logging directory
            self.send_files = glob.glob(ORADIO_LOG_DIR + "/*.log")

        # Unexpected message type
        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return

        oradio_log.debug("Sending to ORMS: message=%s, files=%s", msg_data, self.send_files)

        if not self.send_files:
            # Send message
            try:
                response = requests.post(RMS_SERVER_URL, data=msg_data, timeout=REQUEST_TIMEOUT)
            except requests.Timeout:
                # If we use oradio_error() we might get stuck in a loop
                oradio_log.info("\x1b[38;5;196mERROR: Timeout posting message\x1b[0m")
        else:
            # Send message + files
            msg_files = {}
            for file in self.send_files:
                # Open files after sending
                msg_files[file] = (file, open(file, "rb"))  # pylint: disable=consider-using-with
            try:
                response = requests.post(RMS_SERVER_URL, data=msg_data, files=msg_files, timeout=REQUEST_TIMEOUT)
            except requests.Timeout:
                # If we use oradio_error() we might get stuck in a loop
                oradio_log.info("\x1b[38;5;196mERROR: Timeout posting file(s)\x1b[0m")

            # Close files after sending
            for _, (_, fobj) in msg_files.items():
                fobj.close()

        # Check for errors
        if response.status_code != 200:
            # If we use oradio_error() we might get stuck in a loop
            oradio_log.info("\x1b[38;5;196mERROR: Status code=%s, response.headers=%s\x1b[0m", response.status_code, response.headers)

        # Check for command in RMS response and if exists execute command in Linux shell
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
