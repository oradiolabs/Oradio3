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
from utilities import ThreadTemplate

##### GLOBAL constants ####################################
from constants import (
    BUTTON_SHORT_PRESS,
    BUTTON_LONG_PRESS,
    BUTTON_PLAY,
    BUTTON_STOP,
    BUTTON_PRESET1,
    BUTTON_PRESET2,
    BUTTON_PRESET3,
)

##### LOCAL constants #####################################
# Bound queue size to detect runaway producers early.
_MAX_QUEUE_SIZE = 1000

##### Messaging constants #################################
# Backlighting
BACKLIGHTING_SOURCE  = "Backlighting message"
BACKLIGHTING_FAILED  = "Backlighting failed to start"
BACKLIGHTING_STOPPED = "Backlighting stopped"

# Buttons
BUTTON_SOURCE              = "Button message"
BUTTON_SHORT_PRESS_PLAY    = BUTTON_SHORT_PRESS + BUTTON_PLAY
BUTTON_SHORT_PRESS_STOP    = BUTTON_SHORT_PRESS + BUTTON_STOP
BUTTON_SHORT_PRESS_PRESET1 = BUTTON_SHORT_PRESS + BUTTON_PRESET1
BUTTON_SHORT_PRESS_PRESET2 = BUTTON_SHORT_PRESS + BUTTON_PRESET2
BUTTON_SHORT_PRESS_PRESET3 = BUTTON_SHORT_PRESS + BUTTON_PRESET3
BUTTON_LONG_PRESS_PLAY     = BUTTON_LONG_PRESS + BUTTON_PLAY

# GPIO
GPIO_SOURCE           = "GPIO message"
GPIO_INCIDENT_SERVICE = "GPIO service incident"
GPIO_INCIDENT_BUTTONS = "GPIO buttons incident"

# I2C
I2C_SOURCE       = "I2C service message"
I2C_INCIDENT_BUS = "I2C bus incident"

# MPD
MPD_SOURCE           = "MPD message"
MPD_INCIDENT_CONNECT = "MPD connect incident"
MPD_INCIDENT_EXECUTE = "MPD execute incident"
MPD_INCIDENT_MONITOR = "MPD monitor incident"

# Remote Monitoring
RMS_SOURCE           = "RMS message"
RMS_INCIDENT_SERVICE = "RMS service incident"

# Spotify
SPOTIFY_SOURCE                = "Spotify message"
SPOTIFY_CONNECTED_EVENT       = "Spotify connected event"
SPOTIFY_DISCONNECTED_EVENT    = "Spotify disconnected event"
SPOTIFY_PLAYING_EVENT         = "Spotify playing event"
SPOTIFY_PAUSED_EVENT          = "Spotify paused event"
SPOTIFY_INCIDENT_MONITOR      = "Spotify monitor incident"

# Throttling
THROTTLING_SOURCE    = "Throttling message"
THROTTLING_FAILED    = "RPi throttling monitor failed to start"
THROTTLING_THROTTLED = "RPi throttled"
THROTTLING_STOPPED   = "RPi throttling monitor stopped"

# USB
USB_SOURCE           = "USB message"
USB_ABSENT           = "USB drive absent"
USB_PRESENT          = "USB drive present"
USB_INCIDENT_FILE    = "USB file incident"
USB_INCIDENT_SERVICE = "USB service incident"

# Volume
VOLUME_SOURCE         = "Volume message"
VOLUME_CHANGED        = "Volume changed"
VOLUME_INCIDENT_START = "Volume failed to start"
VOLUME_INCIDENT_STOP  = "Volume failed to stop"

# Web interface
WEB_SOURCE           = "Web message"
WEB_IDLE             = "Web service is idle"
WEB_ACTIVE           = "Web service is running"
WEB_PL1_PLAYLIST     = "PL1 changed to playlist"
WEB_PL2_PLAYLIST     = "PL2 changed to playlist"
WEB_PL3_PLAYLIST     = "PL3 changed to playlist"
WEB_PL1_WEBRADIO     = "PL1 changed to webradio"
WEB_PL2_WEBRADIO     = "PL2 changed to webradio"
WEB_PL3_WEBRADIO     = "PL3 changed to webradio"
WEB_PLAYING_SONG     = "Web service plays a song"
WEB_INCIDENT_START   = "Web service failed to start"
WEB_INCIDENT_STOP    = "Web service failed to stop"
WEB_INCIDENT_SERVICE = "Web service incident"

# wifi
WIFI_SOURCE              = "Wifi message"
WIFI_CONNECTED           = "Wifi connected"
WIFI_DISCONNECTED        = "Wifi disconnected"
WIFI_ACCESS_POINT        = "Wifi configured as access point"
WIFI_INCIDENT_DBUS       = "D-Bus event handler failed"
WIFI_INCIDENT_NMCLI      = "NetworkManager wrapper failed"
WIFI_INCIDENT_CONNECT    = "Wifi failed to connect"
WIFI_INCIDENT_DISCONNECT = "Wifi failed to disconnect"

class Topic(str, Enum):
    """
    Enumeration of supported pub-sub topics.

    Inheriting from str allows enum members to behave like ordinary strings,
    making them convenient for logging, JSON serialization, and dictionary keys.
    """
    COMMAND  = "COMMAND"
    INCIDENT = "INCIDENT"

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
class IncidentMessage:
    """
    Message sent through the incident queue.

    Attributes:
        source:  Name of the process, service, or component sending the message.
        message: Incident description or diagnostic information.
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
    def __init__(self) -> None:
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
        self._last_messages: dict[Topic, dict[str, CommandMessage | IncidentMessage]] = {
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
            queue: Queue = Queue(_MAX_QUEUE_SIZE)
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

    def publish(self, topic: Topic, message: CommandMessage | IncidentMessage) -> None:
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

class Incidents:
    """
    Static namespace for INCIDENT topic operations.
    """

    @staticmethod
    def subscribe(sources: tuple[str, ...] | None = None) -> Queue:
        """
        Subscribe to incident messages.

        Args:
            sources: Optional source filter.

        Returns:
            Subscriber queue.
        """
        return _pubsub.subscribe(Topic.INCIDENT, sources)

    @staticmethod
    def unsubscribe(queue: Queue) -> None:
        """
        Remove a queue from the INCIDENT topic.

        Safe to call more than once for the same queue; repeated calls are
        logged as warnings and ignored.

        Args:
            queue: The Queue returned by the matching Incidents.subscribe() call.
        """
        _pubsub.unsubscribe(Topic.INCIDENT, queue)

    @staticmethod
    def publish(message: IncidentMessage) -> None:
        """
        Validate and publish an incident message.

        Invalid messages are treated as fatal errors.

        Args:
            message: Message to publish.
        """
        if not isinstance(message, IncidentMessage):
            _fatal_exit(f"Wrong message type for Incidents.publish: {message!r}", stacklevel=5)

        if not message.is_valid():
            _fatal_exit(f"Invalid IncidentMessage rejected: {message!r}", stacklevel=5)

        _pubsub.publish(Topic.INCIDENT, message)

def safe_get(queue: Queue) -> Any:
    """
    Return the next message from a queue.

    The concrete type of the returned object depends on the queue's producer:
    messaging bus queues deliver CommandMessage or IncidentMessage instances;
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

class MessageHandlerBase(ThreadTemplate):
    """
    Base class for background message handlers.

    Provides a framework for processing queue messages in a background thread.
    Subclasses must implement the _handle_message method to define how individual
    messages are processed.

    Built on ThreadTemplate with interval=0: do_work() performs one blocking
    get + dispatch per call, and ThreadTemplate's stop_event.wait(0) between
    calls returns immediately, so there's no added latency between finishing
    one message and calling safe_get() for the next.
    Because safe_get() blocks indefinitely, stop() must set the stop event itself
    (rather than relying on safe_stop() to do it after the fact) so that once the
    sentinel unblocks the pending get(), the worker's loop condition is already
    false and it exits on the next check instead of blocking on get() again.
    """
    def __init__(self, queue: Queue) -> None:
        """
        Initialize the message handler and start the worker thread.

        Terminates the process if the thread fails to start (or crashes
        during setup, though the default setup() here never does).

        Args:
            queue: The queue to handle messages from.
        """
        self._queue = queue
        self._lock = Lock()                                 # guards idempotent stop()
        self._stop_sentinel = f"STOP_{uuid.uuid4().hex}"    # Unique per instance

        # interval=0: do_work() itself blocks on safe_get(), so there's no
        # extra polling delay to add between iterations.
        super().__init__(interval=0, name=self.__class__.__name__)

        if not self.safe_start():
            _fatal_exit("Failed to start message handler thread")
        if self.crashed:
            _fatal_exit("Message handler thread crashed during startup", exc=self.exception)
        oradio_log.debug("Message handler thread started")

    def stop(self) -> None:
        """
        Stop the message handler gracefully.

        Sends the instance's unique stop sentinel via safe_put to unblock the
        worker thread, then waits for the thread to terminate using
        ThreadTemplate's default safe_stop() timeout. If the thread does not
        stop within that time, a warning is logged (by safe_stop() itself).

        Note:
            This method is idempotent. Calling it multiple times has no additional effect.
            The stop sentinel is unique per instance, so multiple handlers on the same
            queue will not interfere with each other.
        """
        with self._lock:
            if self.stopping:
                return
            # Set the stop flag before waking the worker, so that once the
            # sentinel unblocks its pending safe_get(), the loop condition
            # is already false and it exits immediately rather than calling
            # do_work() (and blocking on get()) again.
            self._stop_event.set()

        # Wake the worker thread out of its blocking safe_get().
        safe_put(self._queue, self._stop_sentinel)

        # Uses safe_stop()'s own default timeout; it already logs a
        # warning on timeout, so no extra logging is needed here.
        self.safe_stop()

    def do_work(self) -> None:
        """
        Retrieve and dispatch a single message.

        Exits early without dispatching if the message is this instance's
        stop sentinel. The equality check below (rather than identity) is
        intentional: messages arriving from other processes are reconstructed
        objects, not the original instances, so only value equality can match
        the sentinel string across process boundaries. Dataclass and other
        non-string messages safely compare unequal to the sentinel string
        rather than raising.

        Exceptions raised by _handle_message are caught and logged, but do
        not stop the handler.
        """
        message = safe_get(self._queue)

        if message == self._stop_sentinel:
            return

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
    def __init__(self, queue: Queue, index: int | None = None) -> None:
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
    from constants import RED, YELLOW, GREEN, NC    # pylint: disable=ungrouped-imports
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from multiprocessing import Process             # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu() -> None:     # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the messaging module.

        Allows subscribing and unsubscribing multiple handlers, publishing
        command and incident messages from both threads and the main process, and
        deliberately triggering the invalid-message fatal-exit path.

        DebugMessageHandler objects are stored in command_handlers / incident_handlers,
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
            " 2-Subscribe n INCIDENT message handlers\n"
            " 3-Publish COMMAND message\n"
            " 4-Publish INCIDENT message\n"
            " 5-Publish COMMAND message with extra data\n"
            " 6-Publish COMMAND message from THREAD\n"
            " 7-Publish INCIDENT message from THREAD\n"
            " 8-Publish COMMAND message from PROCESS\n"
            " 9-Publish INCIDENT message from PROCESS\n"
            "10-Publish invalid COMMAND message (exits python application)\n"
            "11-Publish invalid INCIDENT message (exits python application)\n"
            "12-Unsubscribe a COMMAND handler by index\n"
            "13-Unsubscribe an INCIDENT handler by index\n"
            "select: "
        )

        cmd_index = 1   # Next index to assign to a new COMMAND handler
        err_index = 1   # Next index to assign to a new INCIDENT handler

        # Handlers indexed by index so specific subscriptions
        # can be targeted by unsubscribe options (12 and 13).
        command_handlers: dict[int, DebugMessageHandler] = {}
        incident_handlers: dict[int, DebugMessageHandler] = {}

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    n = int(input("Enter number of COMMAND handlers to subscribe [1]: ").strip() or "1")
                    for _ in range(n):
                        print(f"Subscribe COMMAND handler {cmd_index}...")
                        command_handlers[cmd_index] = DebugMessageHandler(Commands.subscribe(), cmd_index)
                        cmd_index += 1
                case 2:
                    n = int(input("Enter number of INCIDENT handlers to subscribe [1]: ").strip() or "1")
                    for _ in range(n):
                        print(f"Subscribe INCIDENT handler {err_index}...")
                        incident_handlers[err_index] = DebugMessageHandler(Incidents.subscribe(), err_index)
                        err_index += 1
                case 3:
                    if not command_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message...")
                    Commands.publish(CommandMessage("worker", "command message"))
                    print(f"{GREEN}Success publishing COMMAND message{NC}\n")
                case 4:
                    if not incident_handlers:
                        print(f"{YELLOW}No subscribed INCIDENT handlers{NC}")
                    print("Publishing INCIDENT message...")
                    Incidents.publish(IncidentMessage("worker", "incident message"))
                    print(f"{GREEN}Success publishing INCIDENT message{NC}\n")
                case 5:
                    if not command_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message with extra data...")
                    Commands.publish(CommandMessage("worker", "command message", "extra data"))
                    print(f"{GREEN}Success publishing COMMAND message with extra data{NC}\n")
                case 6:
                    if not command_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from THREAD...")
                    Thread(
                        target=Commands.publish,
                        args=(CommandMessage("worker", "command message from thread"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing COMMAND message from THREAD{NC}\n")
                case 7:
                    if not incident_handlers:
                        print(f"{YELLOW}No subscribed INCIDENT handlers{NC}")
                    print("\nPublish INCIDENT messages from THREAD...")
                    Thread(
                        target=Incidents.publish,
                        args=(IncidentMessage("worker", "incident message from thread"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing INCIDENT message from THREAD{NC}\n")
                case 8:
                    # On Linux (fork start method) the child inherits the parent's
                    # open pipe file descriptors, so this publish is received by
                    # the parent's subscribers. On Windows/macOS (spawn start method)
                    # no file descriptors are inherited and no handler will fire.
                    if not command_handlers:
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
                    if not incident_handlers:
                        print(f"{YELLOW}No subscribed INCIDENT handlers{NC}")
                    print("\nPublish INCIDENT messages from PROCESS...")
                    Process(
                        target=Incidents.publish,
                        args=(IncidentMessage("worker", "incident message from process"),),
                        daemon=True,
                    ).start()
                    print(f"{GREEN}Success publishing INCIDENT message from PROCESS{NC}\n")
                case 10:
                    # Deliberately pass an IncidentMessage to Commands.publish
                    # to exercise the type-check fatal-exit path.
                    print("Publishing invalid COMMAND message...")
                    # Deliberately passing the wrong message type, so ignore pypi check
                    Commands.publish(IncidentMessage("worker", "incident message"))       # type: ignore[arg-type]
                    print(f"{RED}Failed catching error sending incident message to command queue{NC}\n")
                case 11:
                    # Deliberately pass a CommandMessage to Incidents.publish
                    # to exercise the type-check fatal-exit path.
                    print("Publishing invalid INCIDENT message...")
                    # Deliberately passing the wrong message type, so ignore pypi check
                    Incidents.publish(CommandMessage("worker", "command message"))     # type: ignore[arg-type]
                    print(f"{RED}Failed catching error sending command message to incident queue{NC}\n")
                case 12:
                    if not command_handlers:
                        print(f"{YELLOW}No subscribed COMMAND handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(command_handlers))
                        raw = input(f"Active COMMAND handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in command_handlers:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing COMMAND handler {idx}...")
                            handler = command_handlers.pop(idx)
                            # Stop receiving messages
                            Commands.unsubscribe(handler.get_queue())
                            # Signal the thread to exit and confirms it has exited.
                            handler.stop()
                            print(f"{GREEN}COMMAND handler {idx} unsubscribed{NC}\n")
                case 13:
                    if not incident_handlers:
                        print(f"{YELLOW}No subscribed INCIDENT handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(incident_handlers))
                        raw = input(f"Active INCIDENT handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in incident_handlers:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing INCIDENT handler {idx}...")
                            handler = incident_handlers.pop(idx)
                            # Stop receiving messages
                            Incidents.unsubscribe(handler.get_queue())
                            # Signal the thread to exit and confirms it has exited.
                            handler.stop()
                            print(f"{GREEN}INCIDENT handler {idx} unsubscribed{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
