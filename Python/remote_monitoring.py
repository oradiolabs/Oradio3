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

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

##### GLOBAL constants ####################
from oradio_const import *
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
WARNING   = 'WARNING'
ERROR     = 'ERROR'
SYS_ERROR = 'SYS_ERROR'

##### LOCAL constants ####################
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'

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
    """ Get the Oradio software version """
    return 'TODO: uit logging/oradio_sw_version.log lezen'

def get_hw_version():
    """ Get the Oradio software version """
    return 'TODO: uit logging/oradio_hw_version.log lezen'

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
    """
    def __init__(self):
        self.send_files = [ ORADIO_LOG_FILE ]
        self.serial     = get_serial()


    def send_message(self, msg_type, function, message, files = False):

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

        elif msg_type == WARNING or msg_type == ERROR:
            # Compile WARNING and ERROR message
            msg_data['message'] = json.dumps({'function': function, 'message': message})

        # Compile SYS_INFO message
        elif msg_type == SYS_ERROR:
            msg_data['message'] = json.dumps({
                                        'code'   : 'sys_error_code',
                                        'event'  : 'sys_error_event',
                                        'state'  : 'sys_error_state',
                                        'source' : 'sys_error_source'
                                    })

        else:
            # We cannot log as ERROR as this might cause a loop
            oradio_log.info(f"\x1b[38;5;196mremote_monitoring ERROR: Unsupported RMS type: {msg_type}\x1b[0m")

        if not files:
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
                       " 5-test sys_error\n"
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
                rms.send_message(HEARTBEAT, 'filename:lineno', 'test HEARTBEAT message', False)
            case 2:
                print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                rms.send_message(SYS_INFO, 'filename:lineno', 'test system info message', False)
            case 3:
                print("\nSend WARNING test message to Remote Monitoring Service...\n")
                rms.send_message(WARNING, 'filename:lineno', 'test warning message', False)
            case 4:
                print("\nSend ERROR test message to Remote Monitoring Service...\n")
                rms.send_message(ERROR, 'filename:lineno', 'test error message', True)
            case 5:
                print("\nSend SYS_ERROR test message to Remote Monitoring Service...\n")
                rms.send_message(SYS_ERROR, 'filename:lineno', 'test systen error message', False)
            case _:
                print("\nPlease input a valid number\n")


