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
import logging
from logging import DEBUG, INFO, WARNING, ERROR
from concurrent_log_handler import ConcurrentRotatingFileHandler
from vcgencmd import Vcgencmd
import faulthandler

##### oradio modules ####################
from remote_monitoring import RMService

##### GLOBAL constants ####################
from oradio_const import (
    BLUE, GREY, WHITE, YELLOW, RED, NC,
    ORADIO_LOGGER,
    ORADIO_LOG_DIR,
    ORADIO_LOG_LEVEL,
)

##### LOCAL constants ####################
LOGGING_QUEUE_SIZE  = 10000     # Items
ORADIO_LOG_FILE     = ORADIO_LOG_DIR + '/oradio.log'
ORADIO_LOG_FILESIZE = 512 * 1024
ORADIO_LOG_BACKUPS  = 1
TRACE               = 5         # Uvicorn TRACE log level number

# Add TRACE level for Uvicorn
def trace(self, message, *args, **kwargs):
    """Define TRACE as log level using public log(), not private_log() """
    if self.isEnabledFor(TRACE):
        self.log(TRACE, message, *args, **kwargs) # Use log(), not _log()
logging.addLevelName(TRACE, "TRACE")
logging.Logger.trace = trace

# Enable faulthandler for crashes
faulthandler.enable()

# Instantiate RMS service
#REVIEW Onno: RMService instantieren triggert ook de heartbeat-timer
rms = RMService()

# Create the shared logger
oradio_log = logging.getLogger(ORADIO_LOGGER)

# Initialize shared logger
if not oradio_log.hasHandlers():

    # Default log level
    oradio_log.setLevel(ORADIO_LOG_LEVEL)

    # Ensure log directory
    os.makedirs(ORADIO_LOG_DIR, exist_ok=True)

    # Queue handler for async logging
    queue_handler = logging.handlers.QueueHandler(queue.Queue(maxsize=LOGGING_QUEUE_SIZE))
    oradio_log.addHandler(queue_handler)

    # File handler with throttling filter
    class ThrottledFilter(logging.Filter):
        """Blocks writing to SD card when throttled; notifies remote monitoring service."""

        def filter(self, record):
            """
            Get the state of the throttled flags available in vcgencmd module.
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

            The least four bits (3..0) are checked; if any of them are set, `throttled` is True.
            """
            vcgm = Vcgencmd()
            flags = int(vcgm.get_throttled().get("binary", "0"), 2) # Convert binary string to integer
            throttled = (flags & 0xF) > 0   # Check last 4 bits
            if throttled:
                # Notify remote monitoring service
                rms.send_message("WARNING", f"System throttled (flags=0x{flags:X})")
                # Throttled: Skip writing to SD card
                return False
            # Not throttled: Ok to write to SD card
            return True

    clh_handler = ConcurrentRotatingFileHandler(
        filename=ORADIO_LOG_FILE,
        mode='a+',
        maxBytes=ORADIO_LOG_FILESIZE,
        backupCount=ORADIO_LOG_BACKUPS,
    )
    clh_handler.addFilter(ThrottledFilter())

    # Color formatter for file and console
    class ColorFormatter(logging.Formatter):
        """ Use colors to differentiate the different log level messages """
        msg_format = "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
        FORMATS = {
            TRACE:   BLUE   + msg_format + NC,
            DEBUG:   GREY   + msg_format + NC,
            INFO:    WHITE  + msg_format + NC,
            WARNING: YELLOW + msg_format + NC,
            ERROR:   RED    + msg_format + NC,
        }

        def format(self, record):
            """ Color log messages """
            log_fmt = self.FORMATS.get(record.levelno)
            formatter = logging.Formatter(log_fmt)
            return formatter.format(record)

    # Color the log output
    clh_handler.setFormatter(ColorFormatter())

    # Custom handler sending messages to Remote Monitoring Service
    class RemoteMonitoringHandler(logging.Handler):
        """Send WARNING/ERROR logs to RMS service"""
        def emit(self, record):
            if record.levelno in (WARNING, ERROR):
                # Notify remote monitoring service
                rms.send_message(record.levelname, record.getMessage(), f"{record.filename}:{record.lineno}")

    # Send WARNING and ERROR messages to Remote Monitoring Service
    remote_handler = RemoteMonitoringHandler()

    # Console output if terminal
    handlers = [queue_handler, clh_handler, remote_handler]
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColorFormatter())
        handlers.append(console_handler)

    # Configure logging with handlers
    for handler in handlers:
        oradio_log.addHandler(handler)

    # Apply to Uvicorn loggers if needed
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
    import ctypes
    from threading import Thread
    from multiprocessing import Process

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print(f"\nSystem logging level: {ORADIO_LOG_LEVEL}\n")

    def interactive_menu():
        """Show menu with test options"""

        # Show menu with test options
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Test log level TRACE\n"
            " 2-Test log level DEBUG\n"
            " 3-Test log level INFO\n"
            " 4-Test log level WARNING\n"
            " 5-Test log level ERROR\n"
            " 6-Test unhandled exceptions in Process and Thread\n"
            " 7-Test unhandled exception in current thread: will exit\n"
            " 8-Test segment fault: will exit\n"
            "Select: "
        )

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    oradio_log.setLevel(TRACE)
                    print(f"\nlogging level: {TRACE}: Show trace, debug, info, warning and error messages\n")
                    oradio_log.trace('This is a trace message')
                    oradio_log.debug('This is a debug message')
                    oradio_log.info('This is a info message')
                    oradio_log.warning('This is a warning message')
                    oradio_log.error('This is a error message')
                case 2:
                    oradio_log.setLevel(DEBUG)
                    print(f"\nlogging level: {DEBUG}: Show debug, info, warning and error messages\n")
                    oradio_log.trace('This is a trace message')
                    oradio_log.debug('This is a debug message')
                    oradio_log.info('This is a info message')
                    oradio_log.warning('This is a warning message')
                    oradio_log.error('This is a error message')
                case 3:
                    oradio_log.setLevel(INFO)
                    print(f"\nlogging level: {INFO}: Show info, warning and error messages\n")
                    oradio_log.trace('This is a trace message')
                    oradio_log.debug('This is a debug message')
                    oradio_log.info('This is a info message')
                    oradio_log.warning('This is a warning message')
                    oradio_log.error('This is a error message')
                case 4:
                    oradio_log.setLevel(WARNING)
                    print(f"\nlogging level: {WARNING}: Show warning and error messages\n")
                    oradio_log.trace('This is a trace message')
                    oradio_log.debug('This is a debug message')
                    oradio_log.info('This is a info message')
                    oradio_log.warning('This is a warning message')
                    oradio_log.error('This is a error message')
                case 5:
                    oradio_log.setLevel(ERROR)
                    print(f"\nlogging level: {ERROR}: Show error message\n")
                    oradio_log.trace('This is a trace message')
                    oradio_log.debug('This is a debug message')
                    oradio_log.info('This is a info message')
                    oradio_log.warning('This is a warning message')
                    oradio_log.error('This is a error message')
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
                    ctypes.string_at(0)
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
