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
import sys
import queue
import faulthandler
import logging as python_logging
from logging import DEBUG, INFO, WARNING, ERROR
from concurrent_log_handler import ConcurrentRotatingFileHandler
from vcgencmd import Vcgencmd

##### oradio modules ####################
# Functionality needed from other modules is loaded when needed to avoid circular import errors

##### GLOBAL constants ####################
from oradio_const import ORADIO_LOG_DIR

##### LOCAL constants ####################
ORADIO_LOGGER       = 'oradio'
ORADIO_LOG_FILE     = ORADIO_LOG_DIR + '/oradio.log'    # Use absolute path to prevent file rotation trouble
ORADIO_LOG_FILESIZE = 512 * 1024
ORADIO_LOG_BACKUPS  = 1
# Add TRACE log level for uvicorn
TRACE_LOG_NUMBER    = 5
# System wide log level
ORADIO_LOG_LEVEL    = DEBUG

# Capture and print low-level crashes
faulthandler.enable()

def _get_throttled_state_rpi():
    """
    Get the state of the throttled flags available in vcgencmd module
    :return flags = the full throttled state flags of the system in JSON format.
    This is a bit pattern - a bit being set indicates the following meanings:
        Bit Meaning
        0   Under-voltage detected
        1   Arm frequency capped
        2   Currently throttled
        3   Soft temperature limit active
        16  Under-voltage has occurred
        17  Arm frequency capping has occurred
        18  Throttling has occurred
        19  Soft temperature limit has occurred

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
        oradio_log.warning("Oradio is throttled. Flags=%s", flags)
    else:
        throttled_state = False

    return throttled_state

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
        # Get rpi throttled state
        throttled = _get_throttled_state_rpi()
        # Do not log if throttled
        return not throttled

class RemoteMonitoringHandler(python_logging.Handler):
    """ Send error and warning messages to Oradio Remote Monitoring Service """
    def emit(self, record):
        if record.levelno in (WARNING, ERROR):
            # Import here to avoid circular import
            from remote_monitoring import RmsService
            RmsService().send_message(record.levelname, record.message, f"{record.filename}:{record.lineno}")

# Ensure logging directory exists
os.makedirs(ORADIO_LOG_DIR, exist_ok=True)

def trace(self, message, *args, **kwargs):
    """ Registers a new log level TRACE and .trace method for Uvicorn ASGI debugging """
    if self.isEnabledFor(TRACE_LOG_NUMBER):
        self._log(TRACE_LOG_NUMBER, message, args, **kwargs) # pylint: disable=protected-access

# Add TRACE log level
python_logging.addLevelName(TRACE_LOG_NUMBER, "TRACE")
python_logging.Logger.trace = trace

# Configure Oradio logger
oradio_log = python_logging.getLogger('oradio')

# Set default log level
oradio_log.setLevel(ORADIO_LOG_LEVEL)

# Your CLH handler
clh_handler = ConcurrentRotatingFileHandler(ORADIO_LOG_FILE, 'a+', ORADIO_LOG_FILESIZE, ORADIO_LOG_BACKUPS)

# Explicit queue setup
log_queue = queue.Queue(maxsize=10000)

# Queue handler for non-blocking
queue_handler = python_logging.handlers.QueueHandler(log_queue)

# Configure logging
oradio_log.addHandler(queue_handler)

# Rotate log after reaching file size, keep old copies
clh_handler.setFormatter(ColorFormatter())
clh_handler.addFilter(ThrottledFilter())      # Do not write to SD card when RPI is throttled
oradio_log.addHandler(clh_handler)

# Instantiate the Remote Monitoring Service handler
remote_handler = RemoteMonitoringHandler()
oradio_log.addHandler(remote_handler)

# Create console handler only when running in a real terminal
if sys.stderr.isatty():
    console_handler = python_logging.StreamHandler()
    console_handler.setFormatter(ColorFormatter())
    oradio_log.addHandler(console_handler)

# Apply to Uvicorn loggers
for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logger = python_logging.getLogger(name)
    logger.setLevel(ORADIO_LOG_LEVEL)
    logger.handlers = []  # Remove Uvicorn's default handlers
    for handler in oradio_log.handlers:
        logger.addHandler(handler)
    logger.propagate = False

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from threading import Thread
    from multiprocessing import Process

    print(f"\nSystem logging level: {ORADIO_LOG_LEVEL}\n")

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Test log level DEBUG\n"
                       " 2-Test log level INFO\n"
                       " 3-Test log level WARNING\n"
                       " 4-Test log level ERROR\n"
                       " 5-Show throttled status\n"
                       " 6-Test unhandled exceptions in Process and Thread\n"
                       " 7-Test unhandled exception in current thread: will exit\n"
                       " 8-Test segment fault: will exit\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            FunctionNr = int(input(INPUT_SELECTION)) # pylint: disable=invalid-name
        except ValueError:
            FunctionNr = -1 # pylint: disable=invalid-name

        # Execute selected function
        match FunctionNr:
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
            case 5:
                print(f"\nChecking if throttled: {_get_throttled_state_rpi()}\n") # pylint: disable=protected-access
            case 6:
                def _generate_process_exception():
                    print(10 + 'hello: Process')
                print("\nGenerate unhandled exception in Process:\n")
                Process(target=_generate_process_exception).start()
                def _generate_thread_exception():
                    print(10 + 'hello: Thread')
                print("\nGenerate unhandled exception in Thread:\n")
                Thread(target=_generate_thread_exception).start()
            case 7:
                print("\nGenerate unhandled exception in current thread:\n")
                print(10 + 'hello: current thread')

            case 8:
                print("\nGenerate segmentation fault:\n")
                import ctypes
                ctypes.string_at(0)
            case _:
                print("\nPlease input a valid number\n")
