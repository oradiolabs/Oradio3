#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on January 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Create single source of truth for all modules to get logger
    :Documentation
        https://docs.python.org/3/howto/logging.html
        https://pypi.org/project/concurrent-log-handler/
"""
import os
import sys
import queue
import faulthandler
import logging
from logging import DEBUG, INFO, WARNING, ERROR
from concurrent_log_handler import ConcurrentRotatingFileHandler
from vcgencmd import Vcgencmd

##### GROBAL constants ####################
from oradio_const import YELLOW, NC, ORADIO_LOG_DIR

##### LOCAL constants ####################
ORADIO_LOGGER       = "oradio"  # Logger identifier
ORADIO_LOG_LEVEL    = DEBUG     # System-wide log level
ORADIO_LOG_FILE     = ORADIO_LOG_DIR + '/oradio.log'
ORADIO_LOG_FILESIZE = 512 * 1024
ORADIO_LOG_BACKUPS  = 1
TRACE_LOG_NUMBER    = 5

# Add TRACE level
# 2. Voeg de .trace() methode toe aan logging.Logger
def trace(self, message, *args, **kwargs):
    """Define TRACE as log level using public log(), not private_log() """
    if self.isEnabledFor(TRACE_LOG_NUMBER):
        self.log(TRACE_LOG_NUMBER, message, *args, **kwargs) # Use log(), not _log()
logging.addLevelName(TRACE_LOG_NUMBER, "TRACE")
logging.Logger.trace = trace

# Enable faulthandler for crashes
faulthandler.enable()

# Create the shared logger
oradio_log = logging.getLogger(ORADIO_LOGGER)
oradio_log.setLevel(ORADIO_LOG_LEVEL)

if not oradio_log.hasHandlers():
    # Ensure log directory
    os.makedirs(ORADIO_LOG_DIR, exist_ok=True)

    # Logging queue
    queue_handler = logging.handlers.QueueHandler(queue.Queue(maxsize=10000))
    oradio_log.addHandler(queue_handler)

    # File handler
    clh_handler = ConcurrentRotatingFileHandler(
        filename=ORADIO_LOG_FILE,
        mode='a+',
        maxBytes=ORADIO_LOG_FILESIZE,
        backupCount=ORADIO_LOG_BACKUPS,
    )

    class ColorFormatter(logging.Formatter):
        """ Use colors to differentiate the different log level messages """
        grey   = '\x1b[38;5;248m'
        white  = '\x1b[38;5;255m'
        yellow = '\x1b[38;5;226m'
        red    = '\x1b[38;5;196m'
        reset  = '\x1b[0m'
        msg_format = "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
        FORMATS = {
            DEBUG: grey + msg_format + reset,
            INFO: white + msg_format + reset,
            WARNING: yellow + msg_format + reset,
            ERROR: red + msg_format + reset,
        }

        def format(self, record):
            """ Color log messages """
            log_fmt = self.FORMATS.get(record.levelno)
            formatter = logging.Formatter(log_fmt)
            return formatter.format(record)

    # Color the log output
    clh_handler.setFormatter(ColorFormatter())

    class ThrottledFilter(logging.Filter):
        """ Writing to SD card while throttled may corrupt the SD card """
        def filter(self, record):
            """
            Get the state of the throttled flags available in vcgencmd module
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
            """
            vcgm = Vcgencmd()
            flags = int(vcgm.get_throttled().get("binary", "0"), 2) # convert binary string to integer
            throttled = (flags & 0xF) > 0
            if throttled:
                oradio_log.warning("System is throttled: flags=%s", flags)
            return not throttled

    # Do not write to SD card when RPI is throttled
    clh_handler.addFilter(ThrottledFilter())

    class RemoteMonitoringHandler(logging.Handler):
        """ Send error and warning messages to Oradio Remote Monitoring Service """
        _rms = None
        def emit(self, record):
            if record.levelno in (WARNING, ERROR):
                if not self._rms:
                    # Postponed import avoids circular import
                    from remote_monitoring import RmsService    # pylint: disable=import-outside-toplevel
                    self._rms = RmsService()
                self._rms.send_message(record.levelname, record.message, f"{record.filename}:{record.lineno}")

    # Send WARNING and ERROR messages to Remote Monitoring Service
    remote_handler = RemoteMonitoringHandler()

    # Configure logging with handlers
    for handler in (queue_handler, clh_handler, remote_handler):
        oradio_log.addHandler(handler)

    # Create console handler only when running in a real terminal
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColorFormatter())
        oradio_log.addHandler(console_handler)

    # Apply to Uvicorn loggers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
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

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print(f"\nSystem logging level: {ORADIO_LOG_LEVEL}\n")

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Test log level DEBUG\n"
                       " 2-Test log level INFO\n"
                       " 3-Test log level WARNING\n"
                       " 4-Test log level ERROR\n"
                       " 5-Test unhandled exceptions in Process and Thread\n"
                       " 6-Test unhandled exception in current thread: will exit\n"
                       " 7-Test segment fault: will exit\n"
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
                def _generate_process_exception():
                    print(10 + 'hello: Process')
                print("\nGenerate unhandled exception in Process:\n")
                Process(target=_generate_process_exception).start()
                def _generate_thread_exception():
                    print(10 + 'hello: Thread')
                print("\nGenerate unhandled exception in Thread:\n")
                Thread(target=_generate_thread_exception).start()
            case 6:
                print("\nGenerate unhandled exception in current thread:\n")
                print(10 + 'hello: current thread')
            case 7:
                print("\nGenerate segmentation fault:\n")
                import ctypes
                ctypes.string_at(0)
            case _:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")
