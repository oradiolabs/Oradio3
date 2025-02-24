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
from subprocess import run
from vcgencmd import Vcgencmd

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

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
