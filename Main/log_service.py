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
    - StreamHandler: Logs messages to console, with ANSI color per level
    - FileHandler: Logs to a file, with the same ANSI color per level as the console and without
      rotation of its own. The color is deliberate: it makes the log readable with cat. Anything
      that parses the file has to strip the escapes first, since they precede the timestamp.
    Rotation is owned entirely by logrotate (/etc/logrotate.d/oradio), not by this module. Two rotation
    owners on one file is a race, and logrotate's threshold would win regardless: it uses copytruncate,
    which resets the file size before any in-process byte counter could ever reach its own limit. The
    file handler opens in append mode (O_APPEND) precisely so that copytruncate is safe -- every write
    seeks to end-of-file atomically, so writing resumes at offset 0 after a truncate instead of leaving
    a sparse file padded with NULs. That append behavior is also what makes it safe for several Oradio
    processes to share the log: each record reaches the file in a single write() because emit() flushes
    per record.

    Levels. The scheme below is written around INFO as the level to run at; the default is DEBUG for
    now, while the fleet is being watched (see ORADIO_LOG_LEVEL). Each level has a job of its own:
    - CRITICAL says the Oradio cannot go on. fatal_exit() logs at this level on its way out, so a
            CRITICAL line is normally the last one before the process ends and systemd takes over.
            One use does not fit: system_sounds logs a missing sounds directory here, and the
            Oradio carries on without announcements. That is an ERROR by this scheme.
    - ERROR says something the Oradio needed did not happen, and it has not recovered on its own: a
            command that failed after its retries, a device that did not answer, a file that could
            not be read. Most are followed by an incident, which is how the fault reaches RMS.
    - WARNING says something unexpected happened and was handled: a fallback taken, a value that
            could not be read and a default used instead, a retry that is about to be made. The
            Oradio still does what was asked. incident_service also logs every incident it
            receives at this level -- deliberately below the ERROR the source usually wrote for
            the same fault, because the source's line says what failed and this one only says
            that it was received.
    - INFO  says what the Oradio does. Anything that changes its behaviour, and anything that explains
            why it did not change, belongs here: a state transition, an accepted button press, a
            playlist that starts, wifi that comes or goes, a shell script that runs, a command from
            RMS. Read on their own, the INFO lines are the path the code took, which is what the
            warnings and errors between them have to be read against. One line per event, written
            where a module decides something rather than where it carries the decision out.
    - DEBUG is the detail inside such a step: what was tried, skipped, retried or chosen, and the
            values behind it. Turned on while the Oradio runs, for as long as it takes to find a
            fault, and turned off again afterwards.
    - TRACE is per-sample and per-item output: every position of the volume knob, every keep-alive
            ping, every entry of a list being filtered. It is kept out of DEBUG so that DEBUG stays
            readable at the moment someone is actually reading it.
    A line that fires on a timer, per item of a collection or per sample of a sensor is TRACE however
    interesting it is on its own. The test for INFO is not whether a line is useful but whether its
    absence would leave a hole in the story: if the next INFO line still follows from the previous
    one without it, it is not INFO.

    Incidents are deliberately not part of this scheme. They travel over the incident bus rather than
    through the log, so no level can suppress one, and incident_service logs them at WARNING.
@Reference:
    https://docs.python.org/3/howto/logging.html
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

##### Oradio modules ######################################
# NOTE: Do not import Oradio modules using oradio_log to avoid circular imports

##### GLOBAL constants ####################################
from constants import (
    BLUE, GREY, WHITE, YELLOW, RED, MAGENTA, NC,
)

##### LOCAL constants #####################################
# Logger identifier and default level
#
# DEBUG on purpose, and temporarily. The level scheme in the module docstring is
# written around INFO: INFO is meant to carry the path the code took, with DEBUG
# raised only for the length of a support session. Running at DEBUG for a while
# first is what checks that claim -- a DEBUG log contains its own INFO log, so
#
#   grep ' - INFO - ' oradio.log
#
# shows exactly what the fleet would have reported at INFO, with the DEBUG lines
# still beside it to say what a gap in that story would have cost. Anything that
# turns out to be missing is a line that belongs at INFO, not a reason to keep
# the default here.
#
# Switch to INFO once the fleet has gone a few weeks without a fault that the
# INFO lines alone could not place. That is one edit, on the line below.
ORADIO_LOGGER    = "oradio"
ORADIO_LOG_LEVEL = DEBUG

# Uvicorn's own loggers, routed into this logger's queue handler.
#
# One tuple for both the wiring in __init__ and set_level(), so a level change
# during a support session reaches the web server too. Two lists would be two
# chances to add a logger to one and forget the other, and the symptom of that
# is a logger stuck at the level it had at start-up: silent, and only noticed
# when the portal logs you need turn out to be missing.
#
# "uvicorn.asgi" is included even though it would reach the handler by
# propagation anyway: uvicorn.Config.configure_logging() sets its level too, so
# it must be in the set that gets moved, or it stays behind.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")

# Log file constants
ORADIO_LOG_PATH     = (Path(__file__).parent.parent / "logging").resolve()
ORADIO_LOG_FILE_STR = str(ORADIO_LOG_PATH / 'oradio.log')

# Shared record body. Each sink prepends its own context: file/console add a
# timestamp, syslog adds the tag journald parses into SYSLOG_IDENTIFIER.
#
# The thread name comes first, before the source location, because the Oradio is
# one process running a dozen or so threads at once -- the wifi listener, the MPD
# monitor, the volume manager, the web listener, the RMS sender -- and their lines
# interleave. Reading the INFO log as the path the code took means telling those
# lanes apart, and filename:lineno does not: two threads can sit in the same
# function. Worth the width on every line, because the alternative is reasoning
# about which lane each line belongs to while reading.
LOG_BODY          = "%(threadName)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
LOG_FORMAT        = "%(asctime)s - " + LOG_BODY
SYSLOG_LOG_FORMAT = "oradio[%(process)d]: " + LOG_BODY

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
        self._formatters = {
            TRACE:    logging.Formatter(BLUE    + LOG_FORMAT + NC),
            DEBUG:    logging.Formatter(GREY    + LOG_FORMAT + NC),
            INFO:     logging.Formatter(WHITE   + LOG_FORMAT + NC),
            WARNING:  logging.Formatter(YELLOW  + LOG_FORMAT + NC),
            ERROR:    logging.Formatter(RED     + LOG_FORMAT + NC),
            CRITICAL: logging.Formatter(MAGENTA + LOG_FORMAT + NC),
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
    on-disk file handler, then syslog/journald) rather than stopping at
    the first success, since each is an independent failure domain; e.g. a 
    full disk takes out the file handler but not syslog, while a journald
    that's misconfigured or absent takes out syslog but not the file.
    FileHandler and SysLogHandler are both safe to call directly like this
    from any thread. Falls back to stderr only if every sink fails, so
    nothing is lost silently in any case.
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

    The stdlib QueueHandler already uses put_nowait() by default, so it is
    non-blocking on its own -- but it swallows a dropped record (queue full)
    through logging's default handleError() path, with no clear signal.
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

        # Console handler. Only attached when stderr is a terminal
        if stderr.isatty():
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(self._formatter)
            handlers.append(console_handler)

        # File handler. No rotation of its own -- logrotate owns that (see module docstring)
        # and mode="a" is load-bearing rather than incidental: O_APPEND is what makes logrotate's
        # copytruncate safe against a file this process holds open.
        #
        # Kept as its own reference (not just inside `handlers`) because it's also one of the
        # disk-backed fallback sinks for drop/health notices below -- those need to survive
        # headless operation, where stdout/stderr may not be captured anywhere.
        file_handler = logging.FileHandler(ORADIO_LOG_FILE_STR, mode="a", encoding="utf-8")
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
            syslog_handler.setFormatter(logging.Formatter(SYSLOG_LOG_FORMAT))
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
        for logger_name in UVICORN_LOGGERS:
            uv_logger = logging.getLogger(logger_name)
            uv_logger.setLevel(level)
            if self._queue_handler not in uv_logger.handlers:
                uv_logger.addHandler(self._queue_handler)
            # propagate=False also keeps the child loggers from reaching the
            # handler twice: each one carries it directly now.
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
        """
        Set the logging level for the Oradio logger, its queue handler and the
        Uvicorn loggers routed into it.

        The Uvicorn loggers are moved along because they are the web server's
        only voice: left behind, they would keep the level they were given at
        start-up, and raising the level to DEBUG during a support session would
        light up everything except the captive portal -- the part most support
        calls are about.
        """
        self._logger.setLevel(level)
        self._queue_handler.setLevel(level)
        for logger_name in UVICORN_LOGGERS:
            logging.getLogger(logger_name).setLevel(level)

    @property
    def level(self) -> int:
        """
        Current level of the Oradio logger.

        Read by anything that has to hand the level to a library rather than
        log through this wrapper (Uvicorn's Config). Reading the module
        constant instead would give the start-up level, not the level in force
        now.
        """
        return self._logger.level

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

    def health_notice(self, msg: str, level: int = ERROR) -> None:
        """
        Write a notice straight to the fallback sinks, bypassing the queue.

        Args:
            msg:   What to record.
            level: Severity for the fallback handlers.

        For the one thing ordinary logging cannot report: that logging itself
        has failed. A record about a dead listener goes into the queue nothing
        is draining, so it is dropped -- the explanation disappears into the
        problem it describes.

        The queue handler's own drop notice is not a substitute. It reports a
        count, not what the dropped records said, so "gave up restarting the
        listener" would leave no trace at all.
        """
        _emit_fallback(self._fallback_handlers, level, f"[SafeLogger] {msg}")

    def restart_listener(self) -> bool:
        """
        Start the queue listener again after its dispatch thread has died.

        Returns:
            True when a listener thread is running afterwards.

        The queue keeps accepting records whatever happens to the listener, so a
        dead one is not noisy -- it is silent. Records go in, nothing takes them
        out, and once the queue is full every later record is dropped. Nothing
        recovers from that on its own: QueueListener has no supervision of its
        own thread.

        Deliberately NOT stop() before start(). QueueListener.stop() enqueues a
        sentinel with put_nowait(), and the queue this is called about is full
        precisely because nothing has been draining it -- so stop() raises
        queue.Full and the listener is never restarted. That is not an edge
        case: a full queue is the normal state of a dead listener.

        start() on its own replaces _thread with a live one and leaves the dead
        object unreferenced, which is exactly right -- there is nothing to join
        and nothing to signal. The first thing the new thread does is drain the
        backlog, which is also what makes queue_full go away and LOG_QUEUE_
        RECOVERED arrive on its own.

        The handlers are not rebuilt. If one of them is what killed the thread
        -- an emit() that blocked forever -- the new thread will die the same
        way, which is why the caller is expected to keep count and stop asking.
        """
        try:
            self._listener.start()
        # Broad catch: this runs to repair logging, and an exception escaping
        # here would be reported through the very thing that is broken.
        except Exception as ex_err:      # pylint: disable=broad-exception-caught
            self.health_notice(f"restart_listener failed: {ex_err}")
            return False

        return self.listener_alive

    def shutdown(self):
        """
        Shutdown logging queue listener and fallback sinks.

        The stop is tolerated because it cannot be relied on: stop() enqueues a
        sentinel with put_nowait(), so on a queue that is full -- which is the
        state a dead listener leaves behind -- it raises queue.Full. Registered
        with atexit, that exception is printed and then swallowed, and the
        handler close below never runs.
        """
        try:
            self._listener.stop()
        # Broad catch: nothing here is worth failing an exit over, and the
        # sinks below still have to be closed.
        except Exception as ex_err:      # pylint: disable=broad-exception-caught
            self.health_notice(f"listener did not stop cleanly: {ex_err}", WARNING)

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
