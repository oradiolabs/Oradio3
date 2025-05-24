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
import requests

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import check_internet_connection

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
# Software and hardware version info files
SW_LOG_FILE = "/var/log/oradio_sw_version.log"
HW_LOG_FILE = "/var/log/oradio_hw_version.log"
# HEARTBEAT repeat time
HEARTBEAT_REPEAT_TIME = 60 * 60     # 1 hour in seconds
# Timeout for ORMS POST request
REQUEST_TIMEOUT = 30

# Flag to ensure only 1 heartbeat repeat timer is active
HEARTBEAT_REPEAT_TIMER_IS_RUNNING = False

def _get_serial():
    """ Extract serial from hardware """
    stream = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-')
    serial = stream.read().strip()
    return serial

def _get_temperature():
    """ Extract SoC temperature from hardware """
    stream = os.popen('vcgencmd measure_temp | cut -c 6-9')
    temperature = stream.read().strip()
    return temperature

def _get_sw_version():
    """ Read the contents of the SW serial number file """
    try:
        with open(SW_LOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

def _get_hw_version():
    """ Read the contents of the HW serial number file """
    try:
        with open(HW_LOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + " (" + data["hw_detected"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", HW_LOG_FILE)
        return "Invalid HW version"

def _get_rpi_version():
    """ Get the Raspberry Pi version """
    stream = os.popen("cat /proc/cpuinfo | grep Model | cut -d':' -f2")
    rpi_version = stream.read().strip()
    return rpi_version

def _get_os_version():
    """ Get the operating system version """
    stream = os.popen("lsb_release -a | grep 'Description:' | cut -d':' -f2")
    os_version = stream.read().strip()
    return os_version

def _handle_response_command(response_text):
    """ Check repsonse for command: if exist execute in Linux shell """
    # Check for command in response
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
        oradio_log.debug("Run command '%s' from RMS server", command)
        try:
            # executable need to be set, othewise python uses sh. Text converts the result into reable
            result = subprocess.run(command, shell=True, capture_output=True, check=True, executable="/usr/bin/bash", text=True)
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s", command, ex_err.returncode, ex_err.stdout, ex_err.stderr)

class Hearbeat(Timer):
    """ Auto-repeating timer """
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

class RmsService():
    """
    Manage communication with Oradio Remote Monitoring Service (ORMS):
    - HEARTBEAT messages as sign of life
    - SYS_INFO to identify the Oradio to ORMS
    - WARNING and ERROR log messages accompnied by the log file
    """
    def __init__(self):
        """
        Setup rms service class variables
        """
        self.serial = _get_serial()
        self.send_files = None
        self.heartbeat_timer = None

    def heartbeat_start(self):
        """ If not yet active: start the heartbeat repeat timer and mark as active """
        global HEARTBEAT_REPEAT_TIMER_IS_RUNNING    # pylint:  disable=global-statement
        if not HEARTBEAT_REPEAT_TIMER_IS_RUNNING:

            # Send HEARTBEAT when starting
            self.send_message(HEARTBEAT)

            # Send HEARTBEAT every time the timer expires
            self.heartbeat_timer = Hearbeat(HEARTBEAT_REPEAT_TIME, self.send_message, args=(HEARTBEAT,))
            self.heartbeat_timer.start()

            # Mark timer active
            HEARTBEAT_REPEAT_TIMER_IS_RUNNING = True
        else:
            oradio_log.warning("heartbeat repeat timer already active")

    def heartbeat_stop(self):
        """ Stop the heartbeat repeat timer and mark as not active """
        global HEARTBEAT_REPEAT_TIMER_IS_RUNNING    # pylint:  disable=global-statement
        if HEARTBEAT_REPEAT_TIMER_IS_RUNNING:
            self.heartbeat_timer.cancel()
            HEARTBEAT_REPEAT_TIMER_IS_RUNNING = False

    def send_sys_info(self):
        """ Wrapper to simplify oradio control """
        self.send_message(SYS_INFO)

    def send_message(self, msg_type, message = None, function = None):
        """
        Format message based on type
        If connected to the internet: send message to Remote Monitoring Service
        """
        # Messages are lost if not connected to internet
        if not check_internet_connection():
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

        # Compile SYS_INFO message
        elif msg_type == SYS_INFO:
            msg_data['message'] = json.dumps({
                                        'sw_version': _get_sw_version(),
                                        'hw_version': _get_hw_version(),
                                        'python'    : python_version(),
                                        'rpi'       : _get_rpi_version(),
                                        'rpi-os'    : _get_os_version(),
                                    })

        # Compile WARNING and ERROR message
        elif msg_type in (WARNING, ERROR):
            msg_data['message'] = json.dumps({'function': function, 'message': message})
            # Send all log files in logging directory
            self.send_files = glob.glob(ORADIO_LOG_DIR + "/*.log")

        # Unexpected message type
        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return

        oradio_log.debug("Sending to ORMS: %s", msg_data)

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
                msg_files[file] = (file, open(file, "rb"))  # pylint: disable=consider-using-with
            try:
                response = requests.post(RMS_SERVER_URL, data=msg_data, files=msg_files, timeout=REQUEST_TIMEOUT)
            except requests.Timeout:
                # If we use oradio_error() we might get stuck in a loop
                oradio_log.info("\x1b[38;5;196mERROR: Timeout posting file(s)\x1b[0m")

            # Close after sending
            for _, (_, fobj) in msg_files.items():
                fobj.close()

        # Check for errors
        if response.status_code != 200:
            # If we use oradio_error() we might get stuck in a loop
            oradio_log.info("\x1b[38;5;196mERROR: Status code=%s, response.headers=%s\x1b[0m", response.status_code, response.headers)

        # Check for command in RMS response and if exists execute command in Linux shell
        _handle_response_command(response.text)

if __name__ == "__main__":

    # Instantiate RMS service
    rms = RmsService()

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-test heartbeat\n"
                       " 2-test sys_info\n"
                       " 3-test warning\n"
                       " 4-test error\n"
                       " 5-start heartbeat\n"
                       " 6-stop heartbeat\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            FUNCTION_NR = int(input(INPUT_SELECTION))
        except ValueError:
            FUNCTION_NR = -1

        # Execute selected function
        match FUNCTION_NR:
            case 0:
                print("\nExiting test program...\n")
                rms.heartbeat_stop()
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
                print("\nStart heartbeat... Check ORMS for heartbeats\n")
                rms.heartbeat_start()
            case 6:
                print("\nStop heartbeat...\n")
                rms.heartbeat_stop()
            case _:
                print("\nPlease input a valid number\n")
