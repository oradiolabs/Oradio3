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
import os
import logging as python_logging
from logging import DEBUG, INFO, WARNING, ERROR
import concurrent_log_handler
from concurrent_log_handler import ConcurrentRotatingFileHandler
from concurrent_log_handler.queue import setup_logging_queues

##### oradio modules ####################
# Functionality needed from other modules is loaded when needed to avoid circular import errors

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
ORADIO_LOGGER       = 'oradio'
ORADIO_LOG_LEVEL    = DEBUG
ORADIO_LOG_FILE     = ORADIO_LOG_DIR + '/oradio.log'    # Use absolute path to prevent file rotation trouble
ORADIO_LOG_FILESIZE = 512 * 1024
ORADIO_LOG_BACKUPS  = 2

class ColorFormatter(python_logging.Formatter):
    """ Use colors to differentiate the different log level messages """
    grey   = '\x1b[38;5;248m'
    white  = '\x1b[38;5;255m'
    yellow = '\x1b[38;5;226m'
    red    = '\x1b[38;5;196m'
    reset  = '\x1b[0m'

    # Format the log message: datetime - filename:lineno - level - message
    msg_format = "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"

    FORMATS = {
        DEBUG: grey + msg_format + reset,
        INFO: white + msg_format + reset,
        WARNING: yellow + msg_format + reset,
        ERROR: red + msg_format + reset,
    }

    # Color log messages
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = python_logging.Formatter(log_fmt)
        return formatter.format(record)

class ThrottledFilter(python_logging.Filter):
    """ Writing to SD card while throttled may corrupt the SD card """
    def filter(self, record):
        # Import here to avoid circular import
        from oradio_utils import get_throttled_state_rpi
        # Get rpi throttled state
        throttled, flags = get_throttled_state_rpi()
        # Do not log if throttled
        return not throttled

class RemoteMonitoringHandler(python_logging.Handler):
    """ Send error and warning messages to Oradio Remote Monitoring Service """
    def emit(self, record):
        if record.levelno in (WARNING, ERROR):
            # Import here to avoid circular import
            from remote_monitoring import rms_service
            rms_service().send_message(record.levelname, record.message, f"{record.filename}:{record.lineno}")

# Ensure logging directory exists
os.makedirs(ORADIO_LOG_DIR, exist_ok=True)

# Configure Oradio logger
oradio_log = python_logging.getLogger('oradio')

# Set default log level
oradio_log.setLevel(ORADIO_LOG_LEVEL)

# create console handler with a higher log level
console_handler = python_logging.StreamHandler()
console_handler.setFormatter(ColorFormatter())
oradio_log.addHandler(console_handler)

# Rotate log after reaching file size, keep old copies
file_handler = ConcurrentRotatingFileHandler(ORADIO_LOG_FILE, 'a+', ORADIO_LOG_FILESIZE, ORADIO_LOG_BACKUPS)
file_handler.setFormatter(ColorFormatter())
file_handler.addFilter(ThrottledFilter())      # Do not write to SD card when RPI is throttled
oradio_log.addHandler(file_handler)

# Instantiate the Remote Monitoring Service handler
remote_handler = RemoteMonitoringHandler()
oradio_log.addHandler(remote_handler)

# Convert loggers to use background thread
setup_logging_queues()

# Entry point for stand-alone operation
if __name__ == '__main__':

    print(f"\nSystem logging level: {ORADIO_LOG_LEVEL}\n")

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
                oradio_log.setLevel(DEBUG)
                print(f"\nlogging level: {DEBUG}: Show debug, info, warning and error messages\n")
                oradio_log.debug('This is a debug message')
                oradio_log.info('This is a info message')
                oradio_log.warning('This is a warning message')
                oradio_log.error('This is a error message')
            case 2:
                oradio_log.setLevel(INFO)
                print(f"\nlogging level: {INFO}: Show info, warning and error messages\n")
                oradio_log.debug('This is a debug message')
                oradio_log.info('This is a info message')
                oradio_log.warning('This is a warning message')
                oradio_log.error('This is a error message')
            case 3:
                oradio_log.setLevel(WARNING)
                print(f"\nlogging level: {WARNING}: Show warning and error messages\n")
                oradio_log.debug('This is a debug message')
                oradio_log.info('This is a info message')
                oradio_log.warning('This is a warning message')
                oradio_log.error('This is a error message')
            case 4:
                oradio_log.setLevel(ERROR)
                print(f"\nlogging level: {ERROR}: Show error message\n")
                oradio_log.debug('This is a debug message')
                oradio_log.info('This is a info message')
                oradio_log.warning('This is a warning message')
                oradio_log.error('This is a error message')
            case _:
                print("\nPlease input a valid number\n")
