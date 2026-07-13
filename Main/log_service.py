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
@version:       4
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
@Reference:
    https://docs.python.org/3/howto/logging.html
    https://pypi.org/project/concurrent-log-handler/
"""
import atexit
import logging
import traceback
import faulthandler
from sys import stderr
from time import sleep
from pathlib import Path
from threading import Thread
from queue import Queue, Full
from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL
from logging.handlers import QueueHandler, QueueListener, SysLogHandler
from concurrent_log_handler import ConcurrentRotatingFileHandler

##### Oradio modules ######################################
# NOTE: Do not import Oradio modules using oradio_log to avoid circular imports

##### GLOBAL constants ####################################
from constants import (
    BLUE, GREY, WHITE, YELLOW, RED, MAGENTA, NC,
)

##### LOCAL constants #####################################
# Logger identifier and default level
ORADIO_LOGGER    = "oradio"
ORADIO_LOG_LEVEL = DEBUG
# Log file constants
ORADIO_LOG_PATH     = (Path(__file__).parent.parent / "logging").resolve()
ORADIO_LOG_FILE_STR = str(ORADIO_LOG_PATH / 'oradio.log')
ORADIO_LOG_FILESIZE = 512 * 1024   # 512 KB
ORADIO_LOG_BACKUPS  = 1
# Items to queue when busy
QUEUE_SIZE = 10000
# How often (in dropped-item counts) to log a "still dropping" reminder
DROP_LOG_INTERVAL = 50
# Fallback delivery for drop/health notices, independent of stdout/stderr
# (which headless deployments may not capture). /dev/log is forwarded to
# the system journal by journald on virtually every modern Linux distro,
# so `journalctl` shows these even if the log directory itself is gone.
SYSLOG_ADDRESS = "/dev/log"
# TRACE log level between 0 and DEBUG(=10)
TRACE = 5

# Add TRACE log level to logging
def trace(self, message, *args, **kwargs) -> None:
    """Log message with TRACE level if enabled."""
    if self.isEnabledFor(TRACE):
        # self.log() is called by logger
        self.log(TRACE, message, *args, **kwargs)
logging.addLevelName(TRACE, "TRACE")
# Mypy doesn't know Logger has .trace
logging.Logger.trace = trace    # type: ignore[attr-defined]

# Enable Python faulthandler for crashes
faulthandler.enable(file=stderr)

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

class _StrictSysLogHandler(SysLogHandler):
    """
    SysLogHandler whose emit() failures are detectable by the caller.

    Stock SysLogHandler (like most stdlib handlers) swallows emit() errors
    internally via handleError(): it prints a traceback to stderr and
    returns normally, rather than raising. That's the right default for a
    handler used in normal log routing (one bad handler shouldn't crash the
    app) -- but it's the wrong behavior for a handler used purely as a
    drop/health-notice fallback sink, where the whole point is to know
    whether delivery actually succeeded so a caller can try the next
    independent sink. This override raises instead, and is only ever used
    for that fallback role (see SafeLogger.__init__), never for normal
    per-record log routing.
    """
    def handleError(self, record) -> None:
        raise OSError("syslog emit failed")

##### Safe logger Handlers ################################

def _emit_fallback(fallback_handlers: list[logging.Handler], level: int, msg: str) -> None:
    """
    Best-effort delivery of a drop/health notice that does NOT depend on
    anyone reading stdout/stderr (headless services often have console
    output discarded or redirected somewhere nobody looks).

    Writes straight to each given handler via emit(), bypassing the queue
    entirely -- deliberately, since the queue being full/unusable is the
    whole reason this is being called. Tries every sink (in practice: the
    on-disk rotating file handler, then syslog/journald) rather than
    stopping at the first success, since each is an independent failure
    domain -- e.g. a full disk takes out the file handler but not syslog,
    while a journald that's misconfigured or absent takes out syslog but
    not the file. ConcurrentRotatingFileHandler and SysLogHandler are both
    safe to call directly like this from any thread. Falls back to stderr
    only if every sink fails, so nothing is lost silently in any case.
    """
    record = logging.LogRecord(
        name=ORADIO_LOGGER, level=level, pathname=__file__, lineno=0,
        msg=msg, args=None, exc_info=None
    )
    delivered = False
    for handler in fallback_handlers:
        try:
            handler.emit(record)
            delivered = True
        except Exception:     # pylint: disable=broad-exception-caught
            continue  # try the next independent sink
    if not delivered:
        # Every fallback sink failed (or none were configured) -- last resort only.
        print(f"[SafeLogger] {msg}", file=stderr)

class _NonBlockingQueueHandler(QueueHandler):
    """
    QueueHandler variant that never blocks the caller and never drops
    messages silently.

    The stdlib QueueHandler already uses put_nowait() by default, so it was
    already non-blocking -- but a dropped record (queue full) was previously
    swallowed via logging's default handleError() path with no clear signal.
    This subclass counts drops and periodically writes a notice to its
    fallback sinks (disk, syslog), so a saturated queue is visible even
    when running headless with no console attached.
    """
    def __init__(self, queue, fallback_handlers: list[logging.Handler] | None = None) -> None:
        super().__init__(queue)
        self._dropped = 0
        self._fallback_handlers = fallback_handlers or []

    def enqueue(self, record) -> None:
        """Put a record on the queue; count and report if the queue is full."""
        try:
            self.queue.put_nowait(record)
        except Full:
            self._dropped += 1
            # Report the first drop immediately, then periodically, so a
            # sustained overload doesn't spam the fallback sinks on every record.
            if self._dropped == 1 or self._dropped % DROP_LOG_INTERVAL == 0:
                _emit_fallback(
                    self._fallback_handlers, WARNING,
                    f"log queue full (maxsize={QUEUE_SIZE}); "
                    f"dropped {self._dropped} record(s) so far"
                )

    @property
    def dropped(self) -> int:
        """Total number of records dropped due to a full queue."""
        return self._dropped

##### Safe logger wrapper #################################

class SafeLogger:
    """
    Logging wrapper using QueueHandler + QueueListener architecture.
    Architecture:
        Logger → QueueHandler → log_queue → QueueListener → real handlers
    """
    def __init__(self, name=None, level=DEBUG) -> None:
        # Get system logger
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)

        # Create shared log queue
        self._log_queue: Queue[logging.LogRecord] = Queue(maxsize=QUEUE_SIZE)

        # Get color formatter
        self._formatter = ColorFormatter()

        # Ensure log directory exists
        ORADIO_LOG_PATH.mkdir(parents=True, exist_ok=True)

        # REAL output handlers (consumers)
        handlers: list[logging.Handler] = []

        # Console handler
        if stderr.isatty():
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(self._formatter)
            handlers.append(console_handler)

        # File handler (rotating, thread-safe). Kept as its own reference
        # (not just inside `handlers`) because it's also one of the
        # disk-backed fallback sinks for drop/health notices below -- those
        # need to survive headless operation, where stdout/stderr may not
        # be captured anywhere.
        file_handler = ConcurrentRotatingFileHandler(
            filename=ORADIO_LOG_FILE_STR,
            maxBytes=ORADIO_LOG_FILESIZE,
            backupCount=ORADIO_LOG_BACKUPS,
        )
        file_handler.setFormatter(self._formatter)
        handlers.append(file_handler)

        # Second, independent fallback sink for drop/health notices only
        # (not part of `handlers` / normal log routing -- adding it there
        # would duplicate every WARNING+ record into syslog too). journald
        # captures /dev/log on virtually every modern Linux distro, so
        # `journalctl` shows these even if the log directory itself is
        # unwritable (disk full, permissions). Not every environment has
        # /dev/log (containers, non-Linux, minimal images), so this is
        # best-effort: skip it rather than fail startup if unavailable.
        syslog_handler: logging.Handler | None = None
        try:
            syslog_handler = _StrictSysLogHandler(address=SYSLOG_ADDRESS)
            syslog_handler.setFormatter(logging.Formatter(
                "oradio[%(process)d]: %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
            ))
            # SysLogHandler's constructor succeeds even when /dev/log is
            # missing -- it defers the failure to first send. Probe with a
            # real emit() now, so we find out (and fall back to file-only)
            # at startup rather than only discovering it silently later.
            probe_record = logging.LogRecord(
                name=ORADIO_LOGGER, level=DEBUG, pathname=__file__, lineno=0,
                msg="SafeLogger syslog fallback initialized", args=None, exc_info=None
            )
            syslog_handler.emit(probe_record)
        except OSError as ex_err:
            if syslog_handler is not None:
                try:
                    syslog_handler.close()
                except Exception:     # pylint: disable=broad-exception-caught
                    pass
            syslog_handler = None
            print(f"[SafeLogger] syslog fallback unavailable ({ex_err}); "
                  f"drop/health notices will only go to the log file", file=stderr)

        # Independent fallback sinks tried in order for drop/health notices
        # -- each is a separate failure domain (disk full doesn't take out
        # syslog, and vice versa), see _emit_fallback.
        self._fallback_handlers: list[logging.Handler] = [
            h for h in (file_handler, syslog_handler) if h is not None
        ]

        # QueueListener (consumer)
        self._listener = QueueListener(self._log_queue, *handlers, respect_handler_level=True)
        self._listener.start()
        atexit.register(self.shutdown)
        # Flush + stop on normal exit

        # QueueHandler (producer) -- non-blocking, and reports (rather than
        # silently swallows) drops if the queue is ever saturated.
        self._queue_handler = _NonBlockingQueueHandler(self._log_queue, fallback_handlers=self._fallback_handlers)
        self._queue_handler.setLevel(level)

        # IMPORTANT: replace all handlers with queue handler
        self._logger.handlers.clear()
        self._logger.addHandler(self._queue_handler)

        # Uvicorn integration
        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uv_logger = logging.getLogger(logger_name)
            uv_logger.setLevel(level)
            if self._queue_handler not in uv_logger.handlers:
                uv_logger.addHandler(self._queue_handler)
            uv_logger.propagate = False

##### Convenience logging methods #########################

    def _safe_log(self, level, msg, *args, **kwargs) -> None:
        """Internal helper to log messages safely."""
        try:
            # Use stacklevel=3 to skip SafeLogger wrapper
            kwargs.setdefault("stacklevel", 3)
            self._logger.log(level, msg, *args, **kwargs)
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
        """Log a message with WARNING severity level."""
        self._safe_log(WARNING, msg, *args, **kwargs)
    def error(self, msg, *args, **kwargs) -> None:
        """Log a message with ERROR severity level."""
        self._safe_log(ERROR, msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs) -> None:
        """Log a message with CRITICAL severity level."""
        self._safe_log(CRITICAL, msg, *args, **kwargs)
    def set_level(self, level) -> None:
        """Set the logging level for the logger and its queue handler."""
        self._logger.setLevel(level)
        self._queue_handler.setLevel(level)

    @property
    def dropped_count(self) -> int:
        """Total number of log records dropped due to the main queue being full."""
        return self._queue_handler.dropped

    @property
    def queue_size(self) -> int:
        """Approximate current number of records waiting in the log queue."""
        return self._log_queue.qsize()

    @property
    def queue_full(self) -> bool:
        """Whether the log queue is currently at capacity (further puts will be dropped)."""
        return self._log_queue.full()

    @property
    def listener_alive(self) -> bool:
        """
        Whether the QueueListener's background dispatch thread is currently
        running. False means the queue will never drain again (e.g. a
        handler's emit() hung, or the thread died) -- a permanent failure
        distinct from ordinary congestion, where queue_full may be True
        but the listener is still actively draining it.

        QueueListener doesn't expose thread liveness itself, so this reads
        its internal _thread attribute directly.
        """
        thread = self._listener._thread     # pylint: disable=protected-access
        return thread is not None and thread.is_alive()

    def shutdown(self):
        """Shutdown logging queue listener and fallback sinks."""
        self._listener.stop()
        for handler in self._fallback_handlers:
            handler.close()

# Instantiate system logger
oradio_log = SafeLogger(ORADIO_LOGGER, ORADIO_LOG_LEVEL)

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
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

        def worker(thread_id) -> None:
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

        while True:
            try:
                test_choice = int(input(input_selection))
            except (ValueError, EOFError):
                test_choice = -1
            match test_choice:
                case 0:
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

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
