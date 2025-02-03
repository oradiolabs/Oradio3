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
import inspect
from vcgencmd import Vcgencmd
import logging as python_logging
import logging.config as log_config
import concurrent_log_handler   
from concurrent_log_handler.queue import setup_logging_queues

##### oradio modules ####################

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
ORADIO_LOGGER         = 'oradio'
ORADIO_LOGGING_FILE   = ORADIO_LOGGING + '/oradio.log'
ORADIO_LOGGING_LEVEL  = python_logging.DEBUG
ORADIO_LOGGING_CONFIG = 'config/oradio_logging.conf'

# Placeholder: To be replaced by system monitoring?
sys_monitor = None
# Placeholder: To be replaced by remote monitoring!
rms_monitor = None

# Add 'logging.success()'. Level same as for info
DEBUG_SUCCESS_NUM = python_logging.INFO
python_logging.addLevelName(DEBUG_SUCCESS_NUM, "SUCCESS")
def success(self, message, *args, **kws):
    if self.isEnabledFor(DEBUG_SUCCESS_NUM):
        # Yes, logger takes its '*args' as 'args'.
        self._log(DEBUG_SUCCESS_NUM, message, args, **kws) 
python_logging.Logger.success = success

# Create Oradio root logger
oradio_log = python_logging.getLogger(ORADIO_LOGGER)
oradio_log.setLevel(ORADIO_LOGGING_LEVEL)

# Load logger configuration
python_logging.ORADIO_LOGGING_FILE = ORADIO_LOGGING_FILE
log_config.fileConfig(ORADIO_LOGGING_CONFIG)

# convert all configured loggers to use a background thread
setup_logging_queues()

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

def logging(level, log_text):
    """
    logging of log message, but only if rpi is not throttled
    if throttled logging message is lost
    :param level (str) - level of logging [ 'warning' | 'error' | 'info']
    :param log_text (str) - logging message
    """
    # Get stack trace info
    inspect_info = inspect.stack()
    module_info =  inspect.stack()[1]
    module_file_name = inspect_info[1].filename
    mod_name = inspect.getmodule(module_info[0]).__name__
    frame_info = inspect_info[1][0]
    func_name = inspect.getframeinfo(frame_info)[2]

    # check whether rpi is throttled or running normal
    rpi_throttled, throttled_flags = get_throttled_state_rpi()

    # Format message to include stack trace info
    logging_text = f"{mod_name} - {func_name} : {log_text}"

    # Add colors to logging text
    RED_TXT    = "\033[91m"
    GREEN_TXT  = "\033[92m"
    YELLOW_TXT = "\033[93m"
    END_TXT    = "\x1b[0m"

    if level == 'success':
        logging_text = GREEN_TXT + logging_text + END_TXT

    # do not write files in case of a rpi being throttled, could cause a SDram crash
    if not rpi_throttled:
        if level == 'debug':
            oradio_log.debug(logging_text)
#            if sys_monitor != None:
#                sys_monitor.set_warning(logging_text)
        # rpi is not throttled: log info can be writen into log file
        if level == 'success':
            oradio_log.success(GREEN_TXT + logging_text + END_TXT)
#            if sys_monitor != None:
#                sys_monitor.set_warning(logging_text)
        if level == 'warning':
            oradio_log.warning(YELLOW_TXT + logging_text + END_TXT)
#            if sys_monitor != None:
#                sys_monitor.set_warning(logging_text)
        if level == 'error':
            oradio_log.error(RED_TXT + logging_text + END_TXT)
#            if sys_monitor != None:            
#                sys_monitor.set_error(logging_text)
        if level == 'info':
            oradio_log.info(logging_text)
    else:
        if level == 'throttled':
#            sys_monitor.set_warning(logging_text)
            pass

# Entry point for stand-alone operation
if __name__ == '__main__':

    from time import sleep

    print(f"\nSystem logging level: {ORADIO_LOGGING_LEVEL}\n")

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Test log level DEBUG\n"
                       " 2-Test log level INFO\n"
                       " 3-Test log level WARNING\n"
                       " 4-Test log level ERROR\n"
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
                oradio_log.setLevel(python_logging.DEBUG)
                print(f"\nlogging level: {python_logging.DEBUG}: Show debug, info, success, warning and error messages\n")
                logging('debug', 'This is a debug message')
                logging('info', 'This is a info message')
                logging('success', 'This is a success message')
                logging('warning', 'This is a warning message')
                logging('error', 'This is a error message')
            case 2:
                oradio_log.setLevel(python_logging.INFO)
                print(f"\nlogging level: {python_logging.INFO}: Show info, success, warning and error messages\n")
                logging('debug', 'This is a debug message')
                logging('info', 'This is a info message')
                logging('success', 'This is a success message')
                logging('warning', 'This is a warning message')
                logging('error', 'This is a error message')
            case 3:
                oradio_log.setLevel(python_logging.WARNING)
                print(f"\nlogging level: {python_logging.WARNING}: Show warning and error messages\n")
                logging('debug', 'This is a debug message')
                logging('info', 'This is a info message')
                logging('success', 'This is a success message')
                logging('warning', 'This is a warning message')
                logging('error', 'This is a error message')
            case 4:
                oradio_log.setLevel(python_logging.ERROR)
                print(f"\nlogging level: {python_logging.ERROR}: Show error message\n")
                logging('debug', 'This is a debug message')
                logging('info', 'This is a info message')
                logging('success', 'This is a success message')
                logging('warning', 'This is a warning message')
                logging('error', 'This is a error message')
            case _:
                print("\nPlease input a valid number\n")

        # Allow log messages to be printed before showing menu again
        sleep(0.5)

