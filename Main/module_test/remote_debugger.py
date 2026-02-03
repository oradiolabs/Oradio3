#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 11, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Defines for oradio scripts

@install: pip install pydevd
@info
    in case remote python debugging is required:
    * run the Python Debug Server in your IDE
    * call module test with argument -rd [no | yes]
        if yes add in -ip your host ip address and -p the portnr
        >python your_program.py -rd yes -ip 102.168.xxx.xxx -p 5678
"""
##### python modules ##################
import argparse
import os
import socket

##### oradio modules ####################
from oradio_const import (YELLOW, GREEN, NC,
                          DEBUGGER_CONNECTED, DEBUGGER_NOT_CONNECTED,
                          DEBUGGER_DISABLED, DEBUGGER_ENABLED
                          )


############## REMOTE_DEBUGGER options ################################
# REMOTE_DEBUGGER options
# DEBUGGER_ENABLED = When remote debugger is required
# DEBUUGER_DISABLED = When no remote debugger is required (default)
#REMOTE_DEBUGGER = DEBUGGER_DISABLED # default
REMOTE_DEBUGGER = DEBUGGER_ENABLED
if REMOTE_DEBUGGER == DEBUGGER_ENABLED:
    import pydevd

if REMOTE_DEBUGGER == DEBUGGER_ENABLED:
    def setup_remote_debugging() -> tuple[str, str]:
        '''
        Remote debugging service for Python with an IDE (eg Eclipse) in case DEBUGGER_ENABLED
        :Returns
            [debugger_status:str, connection_status:str]
            - The debugger_status: the REMOTE_DEBUGGER is enabled
            - The connection_status: in case the REMOTE_DEBUGGER is enabled
                DEBUGGER_CONNECTED : connection established, or No remote debug required
                DEBUGGER_NOT_CONNECTED: Error detected, no connection
        '''

        debugger_status = DEBUGGER_ENABLED
        parser = argparse.ArgumentParser(description='Remote Debug')
        # pylint: disable=invalid-name
        # motivation; MESSAGE_DEBUG it is a constant, not a var, so allowed
        MESSAGE_DEBUG = 'Remote Debug options are:  -rd [no|yes] -ip [host-ip-address] -p [host-portnr]'
        #pylint: enable=invalid-name
        parser.add_argument('-rd', '--rmdebug', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        parser.add_argument('-ip', '--ipaddress', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        parser.add_argument('-p', '--portnr', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        args = parser.parse_args()
        if args.rmdebug == 'yes':
            if not args.ipaddress or not args.portnr:
                raise argparse.ArgumentError(None, "Both -ip and -p are required when -rd is 'yes'")
        # pylint: enable=line-too-long
        remote_debug = args.rmdebug
        allowed_options = [None, "no","yes"]
        if not remote_debug in allowed_options:
            parser.error(MESSAGE_DEBUG)
        print(f"Remote Debug option = {remote_debug}")
        connection_status = DEBUGGER_NOT_CONNECTED
        if remote_debug == 'yes':
            ip_address  = args.ipaddress
            port_nr     = int(args.portnr)
            print("Remote debugging started")
            # PYDEVD_DISABLE_FILE_VALIDATION is an environment variable that can be set
            # to disable file validation warnings in the Python debugger
            # Can be set to 1 to suppress these warnings when using Python debugging tools.
            os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
            try:
                pydevd.settrace(ip_address, port=port_nr)
            except ConnectionRefusedError:
                print(f"{YELLOW} Failed to connect to debugger at {ip_address}:{port_nr}.")
                print(f"Is the IDE pydev running/listening?{NC}")
            except (socket.error, OSError) as err:
                print(f"{YELLOW}Network error while connecting to debugger: {err} {NC}")
            else:
                print(f"{GREEN}Oradio connected to debugger:{NC}")
                connection_status = DEBUGGER_CONNECTED
        else:
            debugger_status = DEBUGGER_DISABLED
        return debugger_status, connection_status
else:
    def setup_remote_debugging() -> tuple[str, str]:
        '''
        Remote debugging service for Python with an IDE (eg Eclipse) in case DEBUGGER_DISABLED
        :Returns
            [debugger_status:str, connection_status:str]
            - The debugger_status: the REMOTE_DEBUGGER is disabled
            - The connection_status:  DEBUGGER_NOT_CONNECTED: Error detected, no connection
        '''
        # pylint: disable=line-too-long
        parser = argparse.ArgumentParser(description='Remote Debug')
        # pylint: disable=invalid-name
        # motivation; MESSAGE_DEBUG it is a constant, not a var, so allowed
        MESSAGE_DEBUG = 'Remote Debug options are:  -rd [no|yes] -ip [host-ip-address] -p [host-portnr]'
        #pylint: enable=invalid-name
        parser.add_argument('-rd', '--rmdebug', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        parser.add_argument('-ip', '--ipaddress', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        parser.add_argument('-p', '--portnr', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
        args = parser.parse_args()
        if args.rmdebug == 'yes':
            print(f"{YELLOW} The remote debugger is disabled, so no debugging possible{NC}.")
        return DEBUGGER_DISABLED, DEBUGGER_NOT_CONNECTED
