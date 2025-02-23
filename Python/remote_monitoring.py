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
import glob
import json
import requests
from datetime import datetime
from threading import Timer

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import check_internet_connection

##### GLOBAL constants ####################
from oradio_const import *

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

# Flag to ensure only 1 heartbeat repeat timer is active
heartbeat_repeat_timer_is_running = False

def get_serial():
    """ Extract serial from hardware """
    stream = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-')
    serial = stream.read().strip()
    return serial

def get_temperature():
    """ Extract SoC temperature from hardware """
    stream = os.popen('vcgencmd measure_temp | cut -c 6-9')
    temperature = stream.read().strip()
    return temperature

def get_sw_version():
    """ Read the contents of the SW serial number file """
    if os.path.exists(SW_LOG_FILE):
        with open(SW_LOG_FILE, "r") as f:
            data = json.load(f)
        return data["serial"]
    else:
        return f"SW version file '{SW_LOG_FILE}' does not exist"

def get_hw_version():
    """ Read the contents of the HW serial number file """
    if os.path.exists(HW_LOG_FILE):
        with open(HW_LOG_FILE, "r") as f:
            data = json.load(f)
        return data["serial"] + " (" + data["hw_detected"] + ")"
    else:
        return f"HW version file '{HW_LOG_FILE}' does not exist"

def get_python_version():
    """Get the current python version """
    from platform import python_version
    version = python_version()
    return(version)

def get_rpi_version():
    """ Get the Raspberry Pi version """
    stream = os.popen("cat /proc/cpuinfo | grep Model | cut -d':' -f2")
    rpi_version = stream.read().strip()
    return rpi_version

def get_os_version():
    """ Get the operating system version """
    stream = os.popen("lsb_release -a | grep 'Description:' | cut -d':' -f2")
    os_version = stream.read().strip()
    return os_version

class heartbeat(Timer):
    """ Auto-repeating timer """
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

class rms_service():
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
        self.serial     = get_serial()
        self.send_files = None

    def heartbeat_start(self):
        """ If not yet active: start the heartbeat repeat timer and mark as active """
        global heartbeat_repeat_timer_is_running
        if not heartbeat_repeat_timer_is_running:
            self.heartbeat_timer = heartbeat(HEARTBEAT_REPEAT_TIME, self.send_message, args={HEARTBEAT,})
            self.heartbeat_timer.start()
            heartbeat_repeat_timer_is_running = True
        else:
            oradio_log.warning("heartbeat repeat timer already active")

    def heartbeat_stop(self):
        """ Stop the heartbeat repeat timer and mark as not active """
        global heartbeat_repeat_timer_is_running
        if heartbeat_repeat_timer_is_running:
            self.heartbeat_timer.cancel()
            heartbeat_repeat_timer_is_running = False

    def send_sys_info(self):
        """ Wrapper to simplify oradio control """
        self.send_message(SYS_INFO)
        
    def send_message(self, msg_type, message = None, function = None):
        """
        Format message based on type
        If connected to the internet: send message to Remote Monitoring Service
        """
        # Messages are lost if not connected to internet
        if check_internet_connection():

            # Initialze message to send
            msg_data = {
                'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'serial'   : self.serial,
                'type'     : msg_type
            }

            # Compile HEARTBEAT message
            if msg_type == HEARTBEAT:
                msg_data['message'] = json.dumps({'temperature': get_temperature()})

            # Compile SYS_INFO message
            elif msg_type == SYS_INFO:
                msg_data['message'] = json.dumps({
                                            'sw_version': get_sw_version(),
                                            'hw_version': get_hw_version(),
                                            'python'    : get_python_version(),
                                            'rpi'       : get_rpi_version(),
                                            'rpi-os'    : get_os_version(),
                                        })

            # Compile WARNING and ERROR message
            elif msg_type == WARNING or msg_type == ERROR:
                msg_data['message'] = json.dumps({'function': function, 'message': message})
                # Send all log files in logging directory
                self.send_files = glob.glob(ORADIO_LOG_DIR + "/*.log")

            # Unexpected message type
            else:
                # We cannot log as ERROR as this might cause a loop
                oradio_log.info(f"\x1b[38;5;196mremote_monitoring ERROR: Unsupported message type: {msg_type}\x1b[0m")
                return

            if not self.send_files:
                # Send message
                response = requests.post(RMS_SERVER_URL, data=msg_data)
            else:
                # Send message + files
                msg_files = {}
                for file in self.send_files:
                    msg_files.update({file: open(file, "rb")})
                response = requests.post(RMS_SERVER_URL, data=msg_data, files=msg_files)

            # Check for errors
            if response.status_code != 200:
                # We cannot log as ERROR as this might cause a loop
                oradio_log.info(f"\x1b[38;5;196mremote_monitoring ERROR: Status code={response.status_code}, response.headers={response.headers}\x1b[0m")

if __name__ == "__main__":

    # Instantiate RMS service
    rms = rms_service()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
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
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                rms.heartbeat_stop()
                break
            case 1:
                print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                rms.send_message(HEARTBEAT)
                rms.heartbeat_start()
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
