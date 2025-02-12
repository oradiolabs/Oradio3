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
import os, requests, json
from datetime import datetime

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import check_internet_connection

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

##### GLOBAL constants ####################
from oradio_const import *
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
WARNING   = 'WARNING'
ERROR     = 'ERROR'

##### LOCAL constants ####################
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
SW_LOG_FILE = "/var/log/oradio_sw_version.log"
HW_LOG_FILE = "/var/log/oradio_hw_version.log"

# Get Oradio logger
#oradio_log = getLogger('oradio')

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

class rms_service():
    """
    Manage communication with Oradio Remote Monitoring Service (ORMS):
    - HEARTBEAT messages as sign of life
    - SYS_INFO to identify the ORadio to ORMS
    - WARNING and ERROR log messages, where ERROR messages are accompnied by the log file
    """
    def __init__(self):
        """
        Setup rms service class variables
        """
        self.serial     = get_serial()
        self.send_files = None

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
                self.send_files = [ ORADIO_LOG_FILE ]

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
                print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                rms.send_message(HEARTBEAT)
            case 2:
                print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                rms.send_message(SYS_INFO)
            case 3:
                print("\nSend WARNING test message to Remote Monitoring Service...\n")
                rms.send_message(WARNING, 'test warning message', 'filename:lineno')
            case 4:
                print("\nSend ERROR test message to Remote Monitoring Service...\n")
                rms.send_message(ERROR, 'test error message', 'filename:lineno')
            case _:
                print("\nPlease input a valid number\n")
