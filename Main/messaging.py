#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on May 28, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides publish-subscribe messaging for inter-module communication.

    Messages are validated before publication and invalid messages are
    treated as fatal errors. The implementation supports communication
    between threads and, on Linux systems using the fork start method,
    between processes.
"""
import os
import sys
import uuid
from enum import Enum
from queue import Full
from threading import Thread
from typing import Any, NoReturn
from dataclasses import dataclass
from multiprocessing import Lock, Queue

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log

##### GLOBAL constants ####################################
from constants import (
    RED, YELLOW, GREEN, NC,
    BUTTON_SHORT_PRESS,
    BUTTON_LONG_PRESS,
    BUTTON_PLAY,
    BUTTON_STOP,
    BUTTON_PRESET1,
    BUTTON_PRESET2,
    BUTTON_PRESET3,
    JOIN_TIMEOUT,
)

##### LOCAL constants #####################################
# Bound queue size to detect runaway producers early.
_MAX_QUEUE_SIZE = 1000

##### Messaging constants #################################
# Throttling
THROTTLING_SOURCE          = "Throttling message"
THROTTLING_ERROR_THROTTLED = "RPi throttled"
# USB
USB_SOURCE        = "USB message"
USB_ABSENT        = "USB drive absent"
USB_PRESENT       = "USB drive present"
USB_ERROR_FILE    = "USB file error"
USB_ERROR_SERVICE = "USB service error"
# wifi
WIFI_SOURCE           = "Wifi message"
WIFI_CONNECTED        = "Wifi connected"
WIFI_DISCONNECTED     = "Wifi disconnected"
WIFI_ACCESS_POINT     = "Wifi configured as access point"
WIFI_ERROR_DBUS       = "D-Bus event handler failed"
WIFI_ERROR_NMCLI      = "NetworkManager wrapper failed"
WIFI_ERROR_CONNECT    = "Wifi failed to connect"
WIFI_ERROR_DISCONNECT = "Wifi failed to disconnect"
# Web interface
WEB_SOURCE        = "Web message"
WEB_IDLE          = "Web service is idle"
WEB_ACTIVE        = "Web service is running"
WEB_PL1_PLAYLIST  = "PL1 changed to playlist"
WEB_PL2_PLAYLIST  = "PL2 changed to playlist"
WEB_PL3_PLAYLIST  = "PL3 changed to playlist"
WEB_PL1_WEBRADIO  = "PL1 changed to webradio"
WEB_PL2_WEBRADIO  = "PL2 changed to webradio"
WEB_PL3_WEBRADIO  = "PL3 changed to webradio"
WEB_PLAYING_SONG  = "Web service plays a song"
WEB_ERROR_START   = "Web service failed to start"
WEB_ERROR_STOP    = "Web service failed to stop"
WEB_ERROR_SERVICE = "Web service error"
# Remote Monitoring
RMS_SOURCE        = "RMS message"
RMS_ERROR_SERVICE = "RMS service error"
# GPIO
GPIO_SOURCE        = "GPIO message"
GPIO_ERROR_SERVICE = "GPIO service error"
GPIO_ERROR_BUTTONS = "GPIO buttons error"
# Backlight
BACKLIGHT_SOURCE      = "Backlight message"
BACKLIGHT_ERROR_START = "Backlight failed to start"
BACKLIGHT_ERROR_STOP  = "Backlight failed to stop"
# I2C
I2C_SOURCE    = "I2C service message"
I2C_ERROR_BUS = "I2C bus error"
# Volume
VOLUME_SOURCE      = "Volume message"
VOLUME_CHANGED     = "Volume changed"
VOLUME_ERROR_START = "Volume failed to start"
VOLUME_ERROR_STOP  = "Volume failed to stop"
# Buttons
BUTTON_SOURCE              = "Button message"
BUTTON_SHORT_PRESS_PLAY    = BUTTON_SHORT_PRESS + BUTTON_PLAY
BUTTON_SHORT_PRESS_STOP    = BUTTON_SHORT_PRESS + BUTTON_STOP
BUTTON_SHORT_PRESS_PRESET1 = BUTTON_SHORT_PRESS + BUTTON_PRESET1
BUTTON_SHORT_PRESS_PRESET2 = BUTTON_SHORT_PRESS + BUTTON_PRESET2
BUTTON_SHORT_PRESS_PRESET3 = BUTTON_SHORT_PRESS + BUTTON_PRESET3
BUTTON_LONG_PRESS_PLAY     = BUTTON_LONG_PRESS + BUTTON_PLAY
# MPD
MPD_SOURCE        = "MPD message"
MPD_ERROR_CONNECT = "MPD connect error"
MPD_ERROR_EXECUTE = "MPD execute error"
MPD_ERROR_MONITOR = "MPD monitor error"
# Spotify
SPOTIFY_SOURCE             = "Spotify message"
SPOTIFY_CONNECTED_EVENT    = "Spotify connected event"
SPOTIFY_DISCONNECTED_EVENT = "Spotify disconnected event"
SPOTIFY_PLAYING_EVENT      = "Spotify playing event"
SPOTIFY_PAUSED_EVENT       = "Spotify paused event"
SPOTIFY_ERROR_MONITOR      = "Spotify monitor error"

class Topic(str, Enum):
    """
    Enumeration of supported pub-sub topics.

    Inheriting from str allows enum members to behave like ordinary strings,
    making them convenient for logging, JSON serialization, and dictionary keys.
    """
    COMMAND = "COMMAND"
    ERROR = "ERROR"

@dataclass(frozen=True) # Immutable after creation
class CommandMessage:
    """
    Message sent through the command queue.

    Attributes:
        source:  Name of the process, service, or component sending the message.
        message: Command payload or instruction string.
        data:    Optional arbitrary payload attached to the command.
    """
    source: str
    message: str
    data: Any = None

    def is_valid(self) -> bool:
        """
        Return whether the message contains valid source and message strings.

        Optional payload data is not validated.
        """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and bool(self.source.strip())
            and bool(self.message.strip())
        )

@dataclass(frozen=True) # Immutable after creation
class ErrorMessage:
    """
    Message sent through the error queue.

    Attributes:
        source:  Name of the process, service, or component sending the message.
        message: Error description or diagnostic information.
    """
    source: str
    message: str

    def is_valid(self) -> bool:
        """
        Return whether the message contains valid source and message strings.
        """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and bool(self.source.strip())
            and bool(self.message.strip())
        )

##### Helpers #############################################

def _fatal_exit(message: str, stacklevel: int = 6, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush all buffers, and terminate the process.

    Intended for unrecoverable infrastructure failures such as queue
    corruption, invalid internal state, or IPC failure.

    Uses os._exit instead of sys.exit to terminate immediately from any thread.
    This includes daemon threads, where sys.exit() would only terminate the calling thread.

    Args:
        message:    Human-readable description of the fatal error.
        stacklevel: Logging stacklevel passed to oradio_log.critical().
                    The default value reports the original caller.
        exc:        Optional exception associated with the failure; when provided,
                    the full traceback is included in the log entry.
        code:       Process exit status code (default: 1).
    """
    # exc_info=True causes the logging framework to capture the current
    # exception context; passing the exception object directly also works
    # in Python 3.5+ but the bool form is more conventional.
    oradio_log.critical(message, stacklevel=stacklevel, exc_info=exc is not None)

    # Flush the logging framework before exiting so no records are lost.
    oradio_log.shutdown()

    # Flush console buffers before terminating.
    sys.stderr.flush()
    sys.stdout.flush()

    # Bypass Python's normal shutdown sequence so the exit is immediate
    # from any thread, including daemon threads.
    os._exit(code)

##### Pub-Sub Infrastructure ##############################

@singleton
class PubSubManager:
    """
    Singleton responsible for managing subscriptions and message
    delivery for all pub-sub topics.

    Maintains subscriber queues and provides thread-safe subscribe,
    unsubscribe, and publish operations. The implementation uses
    multiprocessing primitives to support communication between
    threads and Linux forked child processes.
    """
    def __init__(self):
        """
        Initialise subscriber registries and message caches.
        """
        # Each subscriber entry is a (queue, source_filter) pair.
        # source_filter is a frozenset of allowed source names, or None to
        # receive messages from all sources.
        # Built from the Topic enum so adding a new topic member
        # automatically gets registries here too.
        self._subscribers: dict[Topic, list[tuple[Queue, frozenset[str] | None]]] = {
            topic: [] for topic in Topic
        }
 
        # Cache of the most recent message per source, per topic.
        # New subscribers receive all cached messages on subscribe so they
        # immediately have a consistent view of the last known state.
        self._last_messages: dict[Topic, dict[str, CommandMessage | ErrorMessage]] = {
            topic: {} for topic in Topic
        }
 
        # multiprocessing.Lock (not threading.Lock) is required here: this
        # lock must also be held safely across forked child processes, not
        # just across threads within one process.
        self._lock = Lock()

    def subscribe(self, topic: Topic, sources: tuple[str, ...] | None = None) -> Queue:
        """
        Register a new subscriber for a topic, optionally filtering messages by source.

        New subscribers receive the most recent cached message from each
        source so they immediately observe the current state.

        Args:
            topic: Topic to subscribe to.
            sources: Optional source filter.

        Returns:
            Subscriber queue.
        """
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        if sources is not None:
            if not isinstance(sources, tuple) or not all(isinstance(s, str) for s in sources):
                _fatal_exit(f"sources must be a tuple of strings or None, got: {sources!r}")
            if not sources:
                _fatal_exit("sources must not be empty; pass None to receive all messages")

        # Convert to frozenset once for O(1) membership tests at publish time.
        source_filter: frozenset[str] | None = frozenset(sources) if sources is not None else None

        with self._lock:
            queue = Queue(_MAX_QUEUE_SIZE)
            self._subscribers[topic].append((queue, source_filter))

            # Replay happens inside the lock so a concurrent publish cannot
            # slip between the cache replay and the queue registration,
            # which would cause the new subscriber to miss a message.
            # Apply the source filter here too so the queue is not pre-filled
            # with messages that would be filtered out at publish time anyway.
            for cached_message in self._last_messages[topic].values():
                if source_filter is not None and cached_message.source not in source_filter:
                    continue
                # safe_put calls _fatal_exit on queue failure; the lock is
                # still held but os._exit is immediate so no deadlock can occur.
                safe_put(queue, cached_message)

        return queue

    def unsubscribe(self, topic: Topic, queue: Queue) -> None:
        """
        Remove a subscriber queue from a topic.

        If queue is not registered for topic, a warning is logged and the
        call is a no-op, making repeated unsubscribe calls safe.

        Args:
            topic: The topic the subscriber was registered on.
            queue: The Queue returned by the matching subscribe() call.
        """
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        with self._lock:
            # Identity comparison (is), not equality: we want the exact Queue
            # object returned by subscribe(), not one that merely compares
            # equal to it. Do not change this to '=='.
            entry = next((e for e in self._subscribers[topic] if e[0] is queue), None)
            if entry is None:
                oradio_log.warning("unsubscribe called for a queue not registered on topic %r — ignored", topic)
                return

            self._subscribers[topic].remove(entry)

        oradio_log.debug("Unsubscribed from topic %r", topic)

    def publish(self, topic: Topic, message: CommandMessage | ErrorMessage) -> None:
        """
        Publish a validated message to all subscribers.

        The latest message per source is cached so new subscribers
        receive current state immediately after subscribing.

        Args:
            topic: Destination topic.
            message: Message to publish.
        """
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        with self._lock:
            # Update the cache inside the lock so it stays consistent with
            # what has been delivered to subscribers.
            self._last_messages[topic][message.source] = message

            for queue, source_filter in self._subscribers[topic]:
                # Apply the source filter before touching the queue so
                # filtered-out messages never consume queue space.
                if source_filter is not None and message.source not in source_filter:
                    continue
                # safe_put calls _fatal_exit on queue failure; the lock is
                # still held but os._exit is immediate so no deadlock can occur.
                safe_put(queue, message)

# Global PubSub manager (singleton — only one instance per process).
_pubsub = PubSubManager()

##### Public API ##########################################

class Commands:
    """
    Static namespace for COMMAND topic operations.
    """

    @staticmethod
    def subscribe(sources: tuple[str, ...] | None = None) -> Queue:
        """
        Subscribe to command messages.

        Args:
            sources: Optional source filter.

        Returns:
            Subscriber queue.
        """
        return _pubsub.subscribe(Topic.COMMAND, sources)

    @staticmethod
    def unsubscribe(queue: Queue) -> None:
        """
        Remove a queue from the COMMAND topic.

        Safe to call more than once for the same queue; repeated calls are
        logged as warnings and ignored.

        Args:
            queue: The Queue returned by the matching Commands.subscribe() call.
        """
        _pubsub.unsubscribe(Topic.COMMAND, queue)

    @staticmethod
    def publish(message: CommandMessage) -> None:
        """
        Validate and publish a command message.

        Invalid messages are treated as fatal errors.

        Args:
            message: Message to publish.
        """
        if not isinstance(message, CommandMessage):
            _fatal_exit(f"Wrong message type for Commands.publish: {message!r}", stacklevel=5)

        if not message.is_valid():
            _fatal_exit(f"Invalid CommandMessage rejected: {message!r}", stacklevel=5)

        _pubsub.publish(Topic.COMMAND, message)

class Errors:
    """
    Static namespace for ERROR topic operations.
    """

    @staticmethod
    def subscribe(sources: tuple[str, ...] | None = None) -> Queue:
        """
        Subscribe to error messages.

        Args:
            sources: Optional source filter.

        Returns:
            Subscriber queue.
        """
        return _pubsub.subscribe(Topic.ERROR, sources)

    @staticmethod
    def unsubscribe(queue: Queue) -> None:
        """
        Remove a queue from the ERROR topic.

        Safe to call more than once for the same queue; repeated calls are
        logged as warnings and ignored.

        Args:
            queue: The Queue returned by the matching Errors.subscribe() call.
        """
        _pubsub.unsubscribe(Topic.ERROR, queue)

    @staticmethod
    def publish(message: ErrorMessage) -> None:
        """
        Validate and publish an error message.

        Invalid messages are treated as fatal errors.

        Args:
            message: Message to publish.
        """
        if not isinstance(message, ErrorMessage):
            _fatal_exit(f"Wrong message type for Errors.publish: {message!r}", stacklevel=5)

        if not message.is_valid():
            _fatal_exit(f"Invalid ErrorMessage rejected: {message!r}", stacklevel=5)

        _pubsub.publish(Topic.ERROR, message)

def safe_get(queue: Queue) -> Any:
    """
    Return the next message from a queue.

    The concrete type of the returned object depends on the queue's producer:
    messaging bus queues deliver CommandMessage or ErrorMessage instances;
    other queues (e.g. the WebService request queue) may deliver plain dicts
    or stop-sentinel strings.

    Terminates the process if the queue becomes unusable.

    Args:
        queue: Queue to read from.

    Returns:
        The next object retrieved from the queue.
    """
    if not hasattr(queue, "get"):
        _fatal_exit(f"Object has no get() method: {type(queue).__name__!r}")

    try:
        return queue.get()

    except (OSError, EOFError, BrokenPipeError) as ex_err:
        # Queue is closed, corrupted, or the underlying pipe is gone.
        _fatal_exit("Queue is closed/broken — failed to get message", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure.
        _fatal_exit("Queue internal error on get", exc=ex_err)

def safe_put(queue: Queue, message: object) -> None:
    """
    Safely put a message into a queue.

    Terminates the process if the queue cannot be written to.

    Args:
        queue:   The queue to put the message into.
        message: The object to put.
    """
    try:
        queue.put_nowait(message)

    except Full:
        # A full queue indicates a runaway producer or stalled consumer —
        # treat it as a critical infrastructure failure.
        _fatal_exit(f"Queue overflow while publishing message: {message}")

    except (OSError, EOFError, ValueError) as ex_err:
        # Queue is closed, corrupted, or the underlying pipe is gone.
        _fatal_exit(f"Queue is closed/broken - failed to put message: {message}", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure.
        _fatal_exit(f"Queue internal error on put message: {message}", exc=ex_err)

##### Template ############################################

class MessageHandlerBase:
    """
    Base class for background message handlers.

    Provides a framework for processing queue messages in a background thread.
    Subclasses must implement the _handle_message method to define how individual
    messages are processed.

    Key Features:
        - Starts a daemon thread to consume messages from a queue.
        - Supports graceful shutdown via a unique stop sentinel per instance.
        - Thread-safe operations using a Lock for the _stopped flag.
    """
    def __init__(self, queue: Queue):
        """
        Initialize the message handler and start the worker thread.

        Terminates the process if the thread fails to start.

        Args:
            queue: The queue to handle messages from.
        """
        self._lock = Lock()
        self._queue = queue
        self._stopped = False
        self._stop_sentinel = f"STOP_{uuid.uuid4().hex}"  # Unique per instance

        # Start background thread that processes incoming messages
        self._thread = Thread(target=self._message_loop, daemon=True)
        try:
            self._thread.start()
            oradio_log.debug("Message handler thread started")
        # Broad catch is safe only because _fatal_exit always terminates the process;
        # if _fatal_exit's behavior ever changes (e.g. to support testing without
        # exiting), this must be narrowed to known thread-start failure types instead
        # of relying on that side effect.
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            _fatal_exit("Failed to start message handler thread", exc=ex_err)

    def stop(self) -> None:
        """
        Stop the message handler gracefully.

        Sends the instance's unique stop sentinel via safe_put to unblock the
        worker thread, then waits for the thread to terminate. If the thread
        does not stop within JOIN_TIMEOUT seconds, a warning is logged.

        Note:
            This method is idempotent. Calling it multiple times has no additional effect.
            The stop sentinel is unique per instance, so multiple handlers on the same
            queue will not interfere with each other.
        """
        with self._lock:
            if self._stopped:
                return
            self._stopped = True

        # Signal the worker thread to exit its loop via safe_put for
        # consistent queue-error handling across the codebase.
        safe_put(self._queue, self._stop_sentinel)

        # Wait for thread termination with timeout
        self._thread.join(timeout=JOIN_TIMEOUT)

        # Log warning if thread did not stop cleanly
        if self._thread.is_alive():
            oradio_log.warning("Message processing thread did not stop within %s seconds", JOIN_TIMEOUT)

    def _message_loop(self) -> None:
        """
        Internal worker loop running in a background thread.

        Continuously retrieves messages from the queue using safe_get (a blocking call
        without a timeout) and dispatches them to _handle_message. The loop exits when
        the instance's unique stop sentinel is received.

        Note:
            Exceptions raised by _handle_message are caught and logged, but do not
            terminate the loop. The loop only exits when the stop sentinel is received.
            The equality check below (rather than identity) is intentional: messages
            arriving from other processes are reconstructed objects, not the original
            instances, so only value equality can match the sentinel string across
            process boundaries. Dataclass and other non-string messages safely compare
            unequal to the sentinel string rather than raising.
        """
        while True:
            # Blocking-safe retrieval of next message
            message = safe_get(self._queue)

            # Exit when the instance's stop sentinel is received.
            if message == self._stop_sentinel:
                return

            # Dispatch message to subclass implementation
            try:
                self._handle_message(message)
            # We don't know what code is executed, thus not what exceptions are possible
            except Exception as ex_err:     # pylint: disable=broad-exception-caught
                oradio_log.error("Error handling message: %s", ex_err)

    def _handle_message(self, message) -> None:
        """
        Handle a single message from the queue.

        Must be implemented by subclasses to define custom message processing logic.

        Args:
            message: The message received from the queue.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement _handle_message()")

##### Debug ###############################################

class DebugMessageHandler(MessageHandlerBase):
    """
    Message handler used for debugging and testing that logs every received message.

    This implementation is intended for debugging purposes and optionally
    includes an index to distinguish multiple handlers subscribed to the
    same topic.
    """
    def __init__(self, queue: Queue, index: int | None = None):
        """
        Initialize the debug message handler.

        Args:
            queue: The subscription queue.
            index: Optional identifier to distinguish multiple
                   handlers subscribed to the same topic.
        """
        # Optional identifier for distinguishing multiple handlers
        self._index = index

        # Initialize base class (subscribes + starts worker thread)
        super().__init__(queue)

    def _handle_message(self, message) -> None:
        """
        Handle an incoming message from the queue by logging it.

        Args:
            message: The received message from the queue.
        """
        tag = "" if self._index is None else f"[{self._index}]"
        oradio_log.debug("DebugMessageHandler%s received: %s", tag, message)

    def get_queue(self) -> Queue:
        """
        Return the underlying subscription queue.

        Returns:
            The Queue associated with this handler.
        """
        return self._queue

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from multiprocessing import Process     # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu() -> None:     # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the messaging module.

        Allows subscribing and unsubscribing multiple handlers, publishing
        command and error messages from both threads and the main process, and
        deliberately triggering the invalid-message fatal-exit path.

        DebugMessageHandler objects are stored in cmd_handlers / err_handlers,
        keyed by handler index, so individual handlers can be targeted by the stop
        options (12 and 13).

        Note: options 8 and 9 publish from a forked child process. On Linux
        (fork start method) these messages are received by the parent's
        subscribers. On Windows and macOS (spawn start method) they are not.
        """

        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Subscribe n COMMAND message handlers\n"
            " 2-Subscribe n ERROR message handlers\n"
            " 3-Publish COMMAND message\n"
            " 4-Publish ERROR message\n"
            " 5-Publish COMMAND message with extra data\n"
            " 6-Publish COMMAND message from THREAD\n"
            " 7-Publish ERROR message from THREAD\n"
            " 8-Publish COMMAND message from PROCESS\n"
            " 9-Publish ERROR message from PROCESS\n"
            "10-Publish invalid COMMAND message (exits python application)\n"
            "11-Publish invalid ERROR message (exits python application)\n"
            "12-Unsubscribe a COMMAND handler by index\n"
            "13-Unsubscribe an ERROR handler by index\n"
            "select: "
        )

        cmd_index = 1   # Next index to assign to a new COMMAND handler
        err_index = 1   # Next index to assign to a new ERROR handler

        # Handlers indexed by index so specific subscriptions
        # can be targeted by unsubscribe options (12 and 13).
        cmd_handlers: dict[int, DebugMessageHandler] = {}
        err_handlers: dict[int, DebugMessageHandler] = {}

        while True:

            try:
                function_nr = int(input(input_selection))
            except ValueError:
                # Non-integer input; fall through to the default case.
                function_nr = -1

            match function_nr:
                case 0:
                    break
                case 1:
                    n = int(input("Enter number of COMMAND handlers to subscribe [1]: ").strip() or "1")
                    for _ in range(n):
                        print(f"Subscribe COMMAND handler {cmd_index}...")
                        cmd_handlers[cmd_index] = DebugMessageHandler(Commands.subscribe(), cmd_index)
                        cmd_index += 1
                case 2:
                    n = int(input("Enter number of ERROR handlers to subscribe [1]: ").strip() or "1")
                    for _ in range(n):
                        print(f"Subscribe ERROR handler {err_index}...")
                        err_handlers[err_index] = DebugMessageHandler(Errors.subscribe(), err_index)
                        err_index += 1
                case 3:
                    if not cmd_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message...")
                    Commands.publish(CommandMessage("worker", "command message"))
                    print(f"{GREEN}Success publishing COMMAND message{NC}\n")
                case 4:
                    if not err_handlers:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("Publishing ERROR message...")
                    Errors.publish(ErrorMessage("worker", "error message"))
                    print(f"{GREEN}Success publishing ERROR message{NC}\n")
                case 5:
                    if not cmd_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message with extra data...")
                    Commands.publish(CommandMessage("worker", "command message", "extra data"))
                    print(f"{GREEN}Success publishing COMMAND message with extra data{NC}\n")
                case 6:
                    if not cmd_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from THREAD...")
                    Thread(
                        target=Commands.publish,
                        args=(CommandMessage("worker", "command message from thread"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing COMMAND message from THREAD{NC}\n")
                case 7:
                    if not err_handlers:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from THREAD...")
                    Thread(
                        target=Errors.publish,
                        args=(ErrorMessage("worker", "error message from thread"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing ERROR message from THREAD{NC}\n")
                case 8:
                    # On Linux (fork start method) the child inherits the parent's
                    # open pipe file descriptors, so this publish is received by
                    # the parent's subscribers. On Windows/macOS (spawn start method)
                    # no file descriptors are inherited and no handler will fire.
                    if not cmd_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from PROCESS...")
                    Process(
                        target=Commands.publish,
                        args=(CommandMessage("worker", "command message from process"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing COMMAND message from PROCESS{NC}\n")
                case 9:
                    # On Linux (fork start method) the child inherits the parent's
                    # open pipe file descriptors, so this publish is received by
                    # the parent's subscribers. On Windows/macOS (spawn start method)
                    # no file descriptors are inherited and no handler will fire.
                    if not err_handlers:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from PROCESS...")
                    Process(
                        target=Errors.publish,
                        args=(ErrorMessage("worker", "error message from process"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing ERROR message from PROCESS{NC}\n")
                case 10:
                    # Deliberately pass an ErrorMessage to Commands.publish
                    # to exercise the type-check fatal-exit path.
                    print("Publishing invalid COMMAND message...")
                    Commands.publish(ErrorMessage("worker", "error message"))
                    print(f"{RED}Failed catching error sending error message to command queue{NC}\n")
                case 11:
                    # Deliberately pass a CommandMessage to Errors.publish
                    # to exercise the type-check fatal-exit path.
                    print("Publishing invalid ERROR message...")
                    Errors.publish(CommandMessage("worker", "command message"))
                    print(f"{RED}Failed catching error sending command message to error queue{NC}\n")
                case 12:
                    if not cmd_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(cmd_handlers))
                        raw = input(f"Active COMMAND handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in cmd_handlers:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing COMMAND handler {idx}...")
                            handler = cmd_handlers.pop(idx)
                            # Stop receiving messages
                            Commands.unsubscribe(handler.get_queue())
                            # Signal the thread to exit and confirms it has exited.
                            handler.stop()
                            print(f"{GREEN}COMMAND handler {idx} unsubscribed{NC}\n")
                case 13:
                    if not err_handlers:
                        print(f"{YELLOW}No subscribed ERROR handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(err_handlers))
                        raw = input(f"Active ERROR handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in err_handlers:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing ERROR handler {idx}...")
                            handler = err_handlers.pop(idx)
                            # Stop receiving messages
                            Errors.unsubscribe(handler.get_queue())
                            # Signal the thread to exit and confirms it has exited.
                            handler.stop()
                            print(f"{GREEN}ERROR handler {idx} unsubscribed{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
