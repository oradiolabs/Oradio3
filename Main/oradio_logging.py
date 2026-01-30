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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides a single source of truth for logging across all modules.
    Features:
    - Safe handlers to prevent recursive logging failures
    - QueueHandler:
      - Non-blocking log handling for multi-threaded applications
      - Prevents log writes from slowing down main program
      - Centralizes log records from multiple threads/processes
    - StreamHandler: Logs messages to console
    - ConcurrentRotatingFileHandler: Logs to a file with rotation, safe for multiple threads/processes
    - RemoteMonitoringHandler: Sends warnings/errors to remote monitoring service
@Reference:
    https://docs.python.org/3/howto/logging.html
    https://pypi.org/project/concurrent-log-handler/
"""
import json
import logging
import traceback
import subprocess
import faulthandler
from os import popen
from glob import glob
from sys import stderr
from time import sleep
from pathlib import Path
from queue import Queue, Full
from datetime import datetime
from contextlib import ExitStack
from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL
from concurrent_log_handler import ConcurrentRotatingFileHandler
from requests import post, RequestException, Timeout

##### oradio modules ####################
# NOTE: Do not import oradio modules using oradio_log to avoid circular imports

##### GLOBAL constants ##################
from oradio_const import (
    BLUE, GREY, WHITE, YELLOW, RED, MAGENTA, NC,
    REMOTE_SERVER,
    POST_TIMEOUT,
)

##### LOCAL constants ###################
# Logger identifier and default level
ORADIO_LOGGER    = "oradio"
ORADIO_LOG_LEVEL = DEBUG
# Log file constants
ORADIO_LOG_PATH     = (Path(__file__).parent.parent / "logging").resolve()
ORADIO_LOG_FILE_STR = str(ORADIO_LOG_PATH / 'oradio.log')
ORADIO_LOG_FILESIZE = 512 * 1024   # 512 KB
ORADIO_LOG_BACKUPS  = 1
# Items to queue when busy
ASYNC_QUEUE_SIZE = 10000
# Robust remote access
MAX_RETRIES    = 3
BACKOFF_FACTOR = 2
# TRACE log level between 0 and DEBUG(=10)
TRACE = 5

# Add TRACE log level to logging
def trace(self, message, *args, **kwargs) -> None:
    """Log message with TRACE level if enabled."""
    if self.isEnabledFor(TRACE):
        # self.log() is called by logger
        self.log(TRACE, message, *args, **kwargs)
logging.addLevelName(TRACE, "TRACE")
logging.Logger.trace = trace

# Enable Python faulthandler for crashes
faulthandler.enable()

# ----- Helpers -----

def _get_rpi_serial() -> str:
    """Extract serial from Raspberry Pi."""
    serial = popen('vcgencmd otp_dump | grep "28:" | cut -c 4-').read().strip()
    return serial or "Unsupported platform"

def _has_internet() -> bool:
    """
    Check for internet access using NetworkManager.
    NOTE: ping is NOT reliable because the network interface uses power management.

    Returns:
        bool: True if internet is reachable, False otherwise.
    """
    try:
        result = subprocess.check_output(
            ["nmcli", "-t", "-f", "CONNECTIVITY", "general"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return result == "full"
    except (FileNotFoundError, subprocess.CalledProcessError, UnicodeDecodeError):
        # Treat any exception as equivalent to 'no internet'
        return False

class ColorFormatter(logging.Formatter):
    """Formatter that adds ANSI color to messages depending on log level."""
    def __init__(self) -> None:
        super().__init__()
        self._msg_format = "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
        self._formatters = {
            TRACE:    logging.Formatter(BLUE    + self._msg_format + NC),
            DEBUG:    logging.Formatter(GREY    + self._msg_format + NC),
            INFO:     logging.Formatter(WHITE   + self._msg_format + NC),
            WARNING:  logging.Formatter(YELLOW  + self._msg_format + NC),
            ERROR:    logging.Formatter(RED     + self._msg_format + NC),
            CRITICAL: logging.Formatter(MAGENTA + self._msg_format + NC),
        }

    def format(self, record) -> str:
        """Return the formatted log message in color based on level."""
        return self._formatters.get(record.levelno, self._formatters[INFO]).format(record)

# ----- Safe logger Handlers -----

class SafeHandler(logging.Handler):
    """Base logging handler that prevents logging exceptions from crashing the program."""
    def emit(self, record) -> None:
        """Wrap safe_emit in try/except to avoid logging failures."""
        try:
            self.safe_emit(record)
        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            print(f"[SafeHandler fallback] {record.getMessage()}. Exception: {ex_err}", file=stderr)
            traceback.print_exc(file=stderr)

    def safe_emit(self, record) -> None:
        """To be implemented by subclasses."""
        raise NotImplementedError

class SafeQueueHandler(SafeHandler):
    """Queue-based handler to safely handle asynchronous logging."""
    def __init__(self, queue) -> None:
        super().__init__()
        self.handler = logging.handlers.QueueHandler(queue)

    def safe_emit(self, record) -> None:
        """Emit record to queue, or warn if the queue is full."""
        try:
            self.handler.emit(record)
        except Full:
            print(f"[SafeQueueHandler] Queue is full. Dropping log: {record.getMessage()}", file=stderr)
        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            print(f"[SafeQueueHandler fallback] {record.getMessage()}. Exception: {ex_err}", file=stderr)
            traceback.print_exc(file=stderr)

class StreamSafeHandler(SafeHandler):
    """Safe console logging handler that prints logs to stdout/stderr."""
    def __init__(self) -> None:
        super().__init__()
        self.handler = logging.StreamHandler()

    def setFormatter(self, fmt) -> None:
        """Set the formatter for both this handler and underlying StreamHandler."""
        super().setFormatter(fmt)
        self.handler.setFormatter(fmt)

    def safe_emit(self, record) -> None:
        """Safely emit record to console."""
        try:
            self.handler.emit(record)
        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            print(f"[StreamSafeHandler fallback] {record.getMessage()}. Exception: {ex_err}", file=stderr)
            traceback.print_exc(file=stderr)

class ConcurrentRotatingFileSafeHandler(SafeHandler):
    """Concurrent rotating file handler that safely logs to file."""
    def __init__(self, filename, max_bytes, backup_count) -> None:
        super().__init__()
        self.handler = ConcurrentRotatingFileHandler(filename, maxBytes=max_bytes, backupCount=backup_count)

    def setFormatter(self, fmt) -> None:
        """Set formatter for both wrapper and internal handler."""
        super().setFormatter(fmt)
        self.handler.setFormatter(fmt)

    def safe_emit(self, record) -> None:
        """Safely save record to file."""
        try:
            self.handler.emit(record)
        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            print(f"[ConcurrentRotatingFileSafeHandler fallback] {record.getMessage()}. Exception: {ex_err}", file=stderr)
            traceback.print_exc(file=stderr)

class RemotePostSafeHandler(SafeHandler):
    """Send WARNING+ log messages to a remote HTTP server safely."""
    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self._serial = _get_rpi_serial()

    def safe_emit(self, record) -> None:
        """Send WARNING, ERROR, CRITICAL messages to remote server if connected to internet."""
        if record.levelno < WARNING or not _has_internet():
            return

        # Compile context for POST request
        payload_info = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : self._serial,
            'type'     : record.levelname,
            'message'  : json.dumps({
                            'source': f"{record.filename}:{record.lineno}",
                            'message': record.getMessage()
                        })
        }

        # Compile files in logging directory for POST request
        send_files = ORADIO_LOG_PATH.glob("*.log")

        try:
            # Use ExitStack to safely open multiple files
            with ExitStack() as stack:
                payload_files = {f.name: (f.name, stack.enter_context(f.open("rb"))) for f in send_files}

                # Retry loop
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        # Send POST with files
                        response = post(self._url, data=payload_info, files=payload_files, timeout=POST_TIMEOUT)
                        # Check for any errors
                        response.raise_for_status()
                        # Success, exit retry loop
                        break
                    except (RequestException, Timeout) as ex_err:
                        logging.getLogger().warning("[RemotePostSafeHandler] Attempt %d failed: %s", attempt, ex_err)
                        if attempt == MAX_RETRIES:
                            # Let fallback mechanism take over
                            raise
                        # Exponential backoff
                        sleep(BACKOFF_FACTOR ** (attempt - 1))

        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            # Log failures using root logger to avoid recursion
            logging.getLogger().error("[RemotePostSafeHandler fallback] Failed to POST log: %s", ex_err, exc_info=True)

# ----- Safe logger wrapper -----

class SafeLogger:
    """Wrapper around standard logger providing safe logging to console, file and remote."""
    def __init__(self, name=None, level=DEBUG) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._formatter = ColorFormatter()

        if not self._logger.handlers:
            # Ensure log directory exists
            ORADIO_LOG_PATH.mkdir(parents=True, exist_ok=True)

            # Async logging queue handler
            queue_handler = SafeQueueHandler(Queue(maxsize=ASYNC_QUEUE_SIZE))
            self._logger.addHandler(queue_handler)

            # Add console handler only when running in a real terminal
            if stderr.isatty():
                console_handler = StreamSafeHandler()
                console_handler.setFormatter(self._formatter)
                self._logger.addHandler(console_handler)

            # File handler with rotation
            file_handler = ConcurrentRotatingFileSafeHandler(
                ORADIO_LOG_FILE_STR,
                ORADIO_LOG_FILESIZE,
                ORADIO_LOG_BACKUPS,
            )
            file_handler.setFormatter(self._formatter)
            self._logger.addHandler(file_handler)

            # Remote logging handler
            remote_handler = RemotePostSafeHandler(REMOTE_SERVER)
            self._logger.addHandler(remote_handler)

            # Replace default Uvicorn handlers
            for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                logger = logging.getLogger(logger_name)
                logger.setLevel(ORADIO_LOG_LEVEL)
                # Remove Uvicorn's default handlers
                logger.handlers = []
                # Add safe handlers
                for handler in self._logger.handlers:
                    logger.addHandler(handler)
                logger.propagate = False

# ----- Convenience logging methods -----

    def _safe_log(self, level, msg, *args, **kwargs) -> None:
        """Internal helper to log messages safely."""
        try:
            # Use stacklevel=3 to skip SafeLogger wrapper
            self._logger.log(level, msg, *args, stacklevel=3, **kwargs)
        # Catching ALL exceptions is fallback, makes logger safe
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            print(f"[SafeLogger fallback] {msg}. Exception: {ex_err}", file=stderr)
            traceback.print_exc(file=stderr)

    # Level-specific methods
    def trace(self, msg, *args, **kwargs) -> None:
        """Log a message with TRACE severity level."""
        self._safe_log(TRACE, msg, *args, **kwargs)
    def debug(self, msg, *args, **kwargs) -> None:
        """Log a message with DEBUG severity level."""
        self._safe_log(DEBUG, msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs) -> None:
        """Log a message with INFO severity level."""
        self._safe_log(INFO, msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs) -> None:
        """Log a message with WARNNIG severity level."""
        self._safe_log(WARNING, msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs) -> None:
        """Log a message with ERROR severity level."""
        self._safe_log(ERROR, msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs) -> None:
        """Log a message with CRITICAL severity level."""
        self._safe_log(CRITICAL, msg, *args, **kwargs)
    def exception(self, msg, *args, **kwargs) -> None:
        """Log an ERROR-level message including the active exception traceback."""
        kwargs.setdefault("exc_info", True)
        self._safe_log(ERROR, msg, *args, **kwargs)

    def set_level(self, level) -> None:
        """Set the logging level for this logger."""
        self._logger.setLevel(level)

# ----- Instantiate system logger -----

oradio_log = SafeLogger(ORADIO_LOGGER, ORADIO_LOG_LEVEL)

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from threading import Thread
    from random import choice

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print(f"\nSystem logging level: {ORADIO_LOG_LEVEL}\n")

    def print_log_messages():
        """Log one message for each level to test handlers."""
        oradio_log.trace('This is a trace message')
        oradio_log.debug('This is a debug message')
        oradio_log.info('This is a info message')
        oradio_log.warning('This is a warning message')
        oradio_log.error('This is a error message')
        oradio_log.critical('This is a critical message')

    def threaded_logging_test(thread_count=5, iterations=10):
        """Spawn multiple threads to log messages concurrently with random levels."""
        log_funcs = [
            oradio_log.trace,
            oradio_log.debug,
            oradio_log.info,
            oradio_log.warning,
            oradio_log.error,
            oradio_log.critical,
        ]

        def worker(thread_id):
            for idx in range(iterations):
                log_func = choice(log_funcs)
                log_func(f"[Thread {thread_id}] Iteration {idx}")
                sleep(0.1)  # Slight delay to simulate work

        threads = [
            Thread(target=worker, args=(thread,), daemon=True)
            for thread in range(thread_count)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        oradio_log.info("Completed multi-threaded logging test with %d threads and %d iterations each", thread_count, iterations)

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
            " 6-Test log level CRITICAL\n"
            " 7-Multi-threaded logging test\n"
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
                    oradio_log.set_level(TRACE)
                    print(f"\nlogging level: {TRACE}: Show trace, debug, info, warning, error and critical messages\n")
                    print_log_messages()
                    print()
                case 2:
                    oradio_log.set_level(DEBUG)
                    print(f"\nlogging level: {DEBUG}: Show debug, info, warning, error and critical messages\n")
                    print_log_messages()
                    print()
                case 3:
                    oradio_log.set_level(INFO)
                    print(f"\nlogging level: {INFO}: Show info, warning, error and critical messages\n")
                    print_log_messages()
                    print()
                case 4:
                    oradio_log.set_level(WARNING)
                    print(f"\nlogging level: {WARNING}: Show warning, error and critical messages\n")
                    print_log_messages()
                    print()
                case 5:
                    oradio_log.set_level(ERROR)
                    print(f"\nlogging level: {ERROR}: Show error and critical messages\n")
                    print_log_messages()
                    print()
                case 6:
                    oradio_log.set_level(CRITICAL)
                    print(f"\nlogging level: {CRITICAL}: Show critical message\n")
                    print_log_messages()
                    print()
                case 7:
                    oradio_log.set_level(DEBUG)
                    print("\nStarting multi-threaded logging test (5 threads, 10 iterations each)...\n")
                    threaded_logging_test()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
