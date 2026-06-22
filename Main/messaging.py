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
@summary:       Provides a publish-subscribe messaging pattern for inter-module and inter-process communication.
    IMPORTANT:
        Messages are validated on publish; invalid messages or unknown topics
        terminate the application immediately to prevent silent data corruption.
"""
import os
import sys
from enum import Enum
from queue import Full
from threading import Thread
from typing import Any, NoReturn
from dataclasses import dataclass
from multiprocessing import Lock, Queue

##### Oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log

##### Messaging constants ####################
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
WEB_SOURCE       = "web service message"
WEB_IDLE         = "web service is idle"
WEB_ACTIVE       = "web service is running"
WEB_PL1_PLAYLIST = "PL1 changed to playlist"
WEB_PL2_PLAYLIST = "PL2 changed to playlist"
WEB_PL3_PLAYLIST = "PL3 changed to playlist"
WEB_PL1_WEBRADIO = "PL1 changed to webradio"
WEB_PL2_WEBRADIO = "PL2 changed to webradio"
WEB_PL3_WEBRADIO = "PL3 changed to webradio"
WEB_PLAYING_SONG = "web service plays a song"
WEB_ERROR_START  = "web service failed to start"
WEB_ERROR_STOP   = "web service failed to stop"

'''
# Messages from fastapi to web service
MESSAGE_REQUEST_CONNECT = "connect to wifi network"
MESSAGE_REQUEST_STOP    = "stop web service"
# Volume
MESSAGE_VOLUME_SOURCE  = "Vol Control message"
MESSAGE_VOLUME_CHANGED = "Volume changed"
# Spotify
MESSAGE_SPOTIFY_SOURCE    = "Spotify message"
# Touch buttons
MESSAGE_BUTTON_SOURCE      = "Button message"
MESSAGE_BUTTON_SHORT_PRESS = "Short press:"
MESSAGE_SHORT_PRESS_BUTTON_PLAY     = MESSAGE_BUTTON_SHORT_PRESS + BUTTON_PLAY
MESSAGE_SHORT_PRESS_BUTTON_STOP     = MESSAGE_BUTTON_SHORT_PRESS + BUTTON_STOP
MESSAGE_SHORT_PRESS_BUTTON_PRESET1  = MESSAGE_BUTTON_SHORT_PRESS + BUTTON_PRESET1
MESSAGE_SHORT_PRESS_BUTTON_PRESET2  = MESSAGE_BUTTON_SHORT_PRESS + BUTTON_PRESET2
MESSAGE_SHORT_PRESS_BUTTON_PRESET3  = MESSAGE_BUTTON_SHORT_PRESS + BUTTON_PRESET3
MESSAGE_BUTTON_LONG_PRESS   = "Long press:"
MESSAGE_LONG_PRESS_BUTTON_PLAY     = MESSAGE_BUTTON_LONG_PRESS + BUTTON_PLAY
'''

##### LOCAL constants ####################

MAX_QUEUE_SIZE = 100

# How long (seconds) unsubscribe() waits for a listener thread to stop after
# receiving the sentinel before logging a warning and moving on.
_UNSUBSCRIBE_JOIN_TIMEOUT = 2.0

# Sentinel value placed in a subscriber queue by unsubscribe() to tell its
# listener thread to exit cleanly.  A unique object (rather than None) avoids
# any possible collision with a legitimate queue payload.
_STOP_SENTINEL = object()

class Topic(str, Enum):
    """
    Valid pub-sub topics.
    Inherits from str so members can be used directly as string keys,
    e.g. in JSON serialisation and log output, without explicit .value access.
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
    data: Any = None    # Optional; not validated (may be any type)

    def is_valid(self) -> bool:
        """
        Return True if the message is structurally valid.

        A valid message has non-empty, non-whitespace-only string values for
        both source and message. data is intentionally excluded
        from validation because it is optional and may be any type.
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
        source: Name of the process, service, or component sending the message
        message: Error description or diagnostic information
    """
    source: str
    message: str

    def is_valid(self) -> bool:
        """
        Return True if the message is structurally valid.

        A valid message has non-empty, non-whitespace-only string values for
        both source and message.
        """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and bool(self.source.strip())
            and bool(self.message.strip())
        )

##### Helpers ##################################

def _safe_get(queue: Queue) -> CommandMessage | ErrorMessage | object:
    """
    Retrieve the next item from a multiprocessing queue, blocking until available.

    Returns the item as-is, including the _STOP_SENTINEL object, so the
    caller can distinguish a normal message from a shutdown signal.

    Terminates the application if the queue becomes broken or corrupted,
    as there is no safe way to continue without a working message bus.

    Args:
        queue: The multiprocessing queue to read from.

    Returns:
        The next item from the queue (a CommandMessage, ErrorMessage, or _STOP_SENTINEL).
    """
    try:
        return queue.get()

    except (OSError, EOFError, BrokenPipeError) as ex_err:
        # Queue is closed, corrupted, or the underlying pipe is gone
        _fatal_exit("Queue is closed/broken — failed to get message", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        _fatal_exit("Queue internal error on get", exc=ex_err)

def _fatal_exit(message: str, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush all buffers, and terminate the process.

    Intended for unrecoverable infrastructure failures such as queue
    corruption, invalid internal state, or IPC failure. Uses os._exit
    rather than sys.exit to ensure immediate termination from any thread,
    including daemon threads where sys.exit would only exit the calling
    thread.

    Args:
        message: Human-readable description of the fatal error.
        exc:     Optional exception associated with the failure; when provided,
                 the full traceback is included in the log entry.
        code:    Process exit status code (default: 1).
    """
    # stacklevel=4 ensures the logged location points to the original call site
    # that triggered the fatal error, not to this helper function.
    oradio_log.critical(message, stacklevel=4, exc_info=exc is not None)

    # Flush the logging framework before exiting so no records are lost
    oradio_log.shutdown()

    # Flush console buffers
    sys.stderr.flush()
    sys.stdout.flush()

    # Immediate exit python execution witohut any cleanup
    os._exit(code)

##### Pub-Sub Infrastructure ####################

def _subscription_listener(queue: Queue, callback: callable, args: tuple) -> None:
    """
    Drain a subscriber queue and forward each message to a callback.

    Runs in a dedicated daemon thread created by PubSubManager.subscribe.
    Loops indefinitely until a _STOP_SENTINEL is received, at which point
    the thread exits cleanly. Any message for which the callback returns
    False is treated as unhandled and terminates the application.

    Args:
        queue:    The subscriber queue to drain.
        callback: Callable invoked for every received message.
                  Signature: callback(message, *args).
        args:     Extra positional arguments forwarded to the callback.
    """
    while True:
        message = _safe_get(queue)

        # A sentinel means unsubscribe() has been called; exit the loop cleanly
        if message is _STOP_SENTINEL:
            return

        if callback(message, *args) is False:
            # Policy: a callback returning False means the message was not handled.
            # Treat this as unrecoverable to prevent silent data loss.
            _fatal_exit(f"Unhandled message: {message!r}")

@singleton
class PubSubManager:
    """
    Singleton manager for command and error pub-sub topics.

    Maintains a registry of per-topic subscriber queues and provides
    thread-safe subscribe, unsubscribe, and publish operations.

    multiprocessing.Lock and multiprocessing.Queue are used
    intentionally over their threading equivalents so the infrastructure
    is safe for use across both threads and child processes without change.
    """
    def __init__(self):
        self._subscribers: dict[Topic, list[Queue]] = {
            Topic.COMMAND: [],
            Topic.ERROR: [],
        }

        # Stores the most recent message per source, per topic.
        # New subscribers receive all cached messages on subscribe so they
        # immediately have a consistent view of the last known state.
        self._last_messages: dict[Topic, dict[str, CommandMessage | ErrorMessage]] = {
            Topic.COMMAND: {},
            Topic.ERROR: {},
        }

        # Maps each subscriber queue to its listener thread so unsubscribe()
        # can signal and join the thread for a clean shutdown.
        self._threads: dict[Queue, Thread] = {}

        self.lock = Lock()

    def subscribe(self, topic: Topic, callback: callable, args: tuple = ()) -> Queue:
        """
        Register a new subscriber for a given topic and start its listener thread.

        Creates a new Queue, appends it to the topic's subscriber list,
        and replays all cached messages (the last message per source) into the
        queue so the new subscriber starts with a consistent state.

        A daemon thread is started immediately to drive the callback; it calls
        callback(message, *args) for every subsequently received message,
        so the caller does not need to write its own listener loop.

        The replay and queue registration happen inside the same lock so no
        in-flight publish can be missed between the two steps.

        Args:
            topic:    The topic to subscribe to.
            callback: Callable invoked for every received message.
                      Signature: callback(message, *args).
            args:     Extra positional arguments forwarded to the callback.

        Returns:
            The subscriber Queue to use as an opaque token with
            unsubscribe().
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Validate callback
        if not callable(callback):
            _fatal_exit(f"callback must be callable, got: {callback!r}")

        # Collect any errors that occur during cache replay so the lock can be
        # released before calling _fatal_exit (prevents other threads hanging
        # on acquire() during shutdown).
        fatal_errors: list[tuple[str, BaseException | None]] = []

        with self.lock:
            queue = Queue(maxsize=MAX_QUEUE_SIZE)
            self._subscribers[topic].append(queue)

            # Replay cached messages inside the lock so a publish racing with
            # this subscribe cannot slip between cache-replay and registration.
            for cached_message in self._last_messages[topic].values():
                try:
                    queue.put_nowait(cached_message)
                except Full:
                    fatal_errors.append((
                        f"New subscriber queue for topic {topic!r} is full "
                        f"while replaying cached message: {cached_message}",
                        None,
                    ))
                except (OSError, EOFError, ValueError) as ex_err:
                    fatal_errors.append((
                        f"New subscriber queue for topic {topic!r} is closed/broken "
                        f"while replaying cached message: {cached_message}",
                        ex_err,
                    ))

        # Fatal exit is called outside the lock (see comment above)
        if fatal_errors:
            for error, exc in fatal_errors[:-1]:
                oradio_log.critical(error, exc_info=exc is not None)
            last_error, last_exc = fatal_errors[-1]
            _fatal_exit(last_error, exc=last_exc)

        # Store the thread before starting it so unsubscribe() can join it.
        # Any messages published between cache-replay and thread-start are
        # buffered in the queue and drained once the thread begins running.
        thread = Thread(target=_subscription_listener, args=(queue, callback, args), daemon=True)
        with self.lock:
            self._threads[queue] = thread
        thread.start()

        # Return the queue as an opaque subscription token for unsubscribe()
        return queue

    def unsubscribe(self, topic: Topic, token: Queue) -> None:
        """
        Stop a subscriber's listener thread and remove it from the topic.

        Removes the subscriber queue from the topic's registry (so no further
        messages are delivered), then places _STOP_SENTINEL into the queue
        to wake the blocked listener thread and cause it to exit. The thread is
        then joined with a short timeout; a warning is logged if it does not
        stop in time (it will still be collected when the process exits because
        it is a daemon thread).

        If token is not registered for topic a warning is logged and
        the call is a no-op, making repeated unsubscribe calls safe.

        Args:
            topic: The topic the subscriber was registered on.
            token: The Queue returned by the matching subscribe() call.
        """
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        with self.lock:
            if token not in self._subscribers[topic]:
                oradio_log.warning("unsubscribe called for a queue not registered on topic %r — ignored", topic)
                return
            # Remove from registry first so no new messages are enqueued after
            # this point; the sentinel we are about to send will be the last item.
            self._subscribers[topic].remove(token)

        # Send the sentinel outside the lock; the queue is no longer in the
        # registry so publish() cannot enqueue anything after this point.
        try:
            token.put_nowait(_STOP_SENTINEL)
        except Full:
            # Queue is saturated; the sentinel cannot be delivered.
            # The thread will drain existing messages first, then receive the
            # sentinel once space is available — log but do not fatal-exit.
            oradio_log.warning("Subscriber queue for topic %r is full; sentinel queued behind existing messages", topic)
            try:
                token.put(_STOP_SENTINEL)   # Block until space is available
            except (OSError, EOFError) as ex_err:
                oradio_log.error("Failed to send stop sentinel to subscriber on topic %r: %s", topic, ex_err)
                return

        # Join the listener thread so the caller knows the callback will not
        # be invoked again after unsubscribe() returns.
        thread = self._threads.pop(token, None)
        if thread is not None:
            thread.join(timeout=_UNSUBSCRIBE_JOIN_TIMEOUT)
            if thread.is_alive():
                oradio_log.warning(
                    "Listener thread for topic %r did not stop within %.1fs; "
                    "it is a daemon thread and will be collected on process exit.",
                    topic,
                    _UNSUBSCRIBE_JOIN_TIMEOUT,
                )
        oradio_log.debug("Unsubscribed from topic %r", topic)

    def publish(self, topic: Topic, message: CommandMessage | ErrorMessage) -> None:
        """
        Publish a validated message to all current subscribers of a topic.

        Caches the message as the latest for its source (replacing any
        previous entry) so new subscribers receive it during cache replay.
        Terminates the application if any subscriber queue is full or broken.

        Callers must validate message type and structure before calling this
        method. Use the public Commands or Errors class methods, which
        enforce these checks.

        Args:
            topic:   The topic to publish to.
            message: The validated message to deliver to all subscribers.
        """
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Collect failures so the lock can be released before calling
        # _fatal_exit, preventing other threads from hanging on acquire().
        fatal_errors: list[tuple[str, BaseException | None]] = []

        with self.lock:
            # Update the cache inside the lock so it stays consistent with
            # what has been delivered to subscribers.
            self._last_messages[topic][message.source] = message

            for queue in self._subscribers[topic]:
                try:
                    queue.put_nowait(message)
                except Full:
                    fatal_errors.append((f"Queue for topic {topic!r} is full. Message: {message}", None))
                except (OSError, EOFError, ValueError) as ex_err:
                    fatal_errors.append((f"Queue for topic {topic!r} is closed/broken: {message}", ex_err))
                except AssertionError as ex_err:
                    fatal_errors.append((f"Queue for topic {topic!r} internal error: {message}", ex_err))

        # Fatal exit is called outside the lock (see comment above)
        if fatal_errors:
            # Log all failures except the last, then fatal-exit on the last
            for error, exc in fatal_errors[:-1]:
                oradio_log.critical(error, exc_info=exc is not None)
            last_error, last_exc = fatal_errors[-1]
            _fatal_exit(last_error, exc=last_exc)

# Global PubSub manager (singleton — only one instance per process)
_pubsub = PubSubManager()

##### Public API ####################

class Commands:
    """
    Namespace for publishing, subscribing, and unsubscribing command messages.

    All methods are static; this class is never instantiated. Grouping
    operations by topic (Commands / Errors) rather than exposing nine
    top-level functions keeps the public surface small and makes the
    topic explicit at every call-site without requiring a topic argument.

    Usage:
        token = Commands.subscribe(my_callback)
        Commands.publish(CommandMessage("worker", "start"))
        Commands.unsubscribe(token)
    """

    @staticmethod
    def subscribe(callback: callable, args: tuple = ()) -> Queue:
        """
        Subscribe to command messages and start a listener thread.

        The new subscriber queue is pre-populated with the last command message
        for every source that has published since the application started, giving
        the subscriber an immediately consistent view of the current state.

        A daemon thread is started automatically; it calls
        callback(message, *args) for each received message so the caller
        does not need to manage a listener loop manually.

        Args:
            callback: Callable invoked for every received message.
                      Signature: callback(message, *args).
            args:     Extra positional arguments forwarded to the callback.

        Returns:
            An opaque subscription token (Queue) to pass to
            Commands.unsubscribe() when the subscription is no longer needed.
        """
        return _pubsub.subscribe(Topic.COMMAND, callback, args)

    @staticmethod
    def unsubscribe(token: Queue) -> None:
        """
        Stop a command subscriber's listener thread and remove it from the topic.

        Safe to call more than once for the same token; repeated calls are
        logged as warnings and ignored.

        Args:
            token: The Queue returned by the matching Commands.subscribe() call.
        """
        _pubsub.unsubscribe(Topic.COMMAND, token)

    @staticmethod
    def publish(message: CommandMessage) -> None:
        """
        Validate and publish a command message to all subscribers.

        The most recent message per source is cached; late subscribers receive
        it automatically during cache replay on subscribe.

        Terminates the application if message is not a CommandMessage or
        fails structural validation.

        Args:
            message: The CommandMessage to publish.
        """
        if not isinstance(message, CommandMessage):
            _fatal_exit(f"Wrong message type for Commands.publish: {message!r}")

        if not message.is_valid():
            _fatal_exit(f"Invalid CommandMessage rejected: {message!r}")

        _pubsub.publish(Topic.COMMAND, message)


class Errors:
    """
    Namespace for publishing, subscribing, and unsubscribing error messages.

    All methods are static; this class is never instantiated. Grouping
    operations by topic (Commands / Errors) rather than exposing nine
    top-level functions keeps the public surface small and makes the
    topic explicit at every call-site without requiring a topic argument.

    Usage:
        token = Errors.subscribe(my_callback)
        Errors.publish(ErrorMessage("worker", "something went wrong"))
        Errors.unsubscribe(token)
    """

    @staticmethod
    def subscribe(callback: callable, args: tuple = ()) -> Queue:
        """
        Subscribe to error messages and start a listener thread.

        The new subscriber queue is pre-populated with the last error message
        for every source that has published since the application started, giving
        the subscriber an immediately consistent view of the current state.

        A daemon thread is started automatically; it calls
        callback(message, *args) for each received message so the caller
        does not need to manage a listener loop manually.

        Args:
            callback: Callable invoked for every received message.
                      Signature: callback(message, *args).
            args:     Extra positional arguments forwarded to the callback.

        Returns:
            An opaque subscription token (Queue) to pass to
            Errors.unsubscribe() when the subscription is no longer needed.
        """
        return _pubsub.subscribe(Topic.ERROR, callback, args)

    @staticmethod
    def unsubscribe(token: Queue) -> None:
        """
        Stop an error subscriber's listener thread and remove it from the topic.

        Safe to call more than once for the same token; repeated calls are
        logged as warnings and ignored.

        Args:
            token: The Queue returned by the matching Errors.subscribe() call.
        """
        _pubsub.unsubscribe(Topic.ERROR, token)

    @staticmethod
    def publish(message: ErrorMessage) -> None:
        """
        Validate and publish an error message to all subscribers.

        The most recent message per source is cached; late subscribers receive
        it automatically during cache replay on subscribe.

        Terminates the application if message is not an ErrorMessage or
        fails structural validation.

        Args:
            message: The ErrorMessage to publish.
        """
        if not isinstance(message, ErrorMessage):
            _fatal_exit(f"Wrong message type for Errors.publish: {message!r}")

        if not message.is_valid():
            _fatal_exit(f"Invalid ErrorMessage rejected: {message!r}")

        _pubsub.publish(Topic.ERROR, message)


# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from time import sleep
    from multiprocessing import Process     # pylint: disable=ungrouped-imports

    # GLOBAL constants
    from oradio_const import RED, YELLOW, GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def handler(message, topic, index) -> None:
        """
        Print a received message to stdout (used as a subscriber callback).

        Args:
            message: The CommandMessage or ErrorMessage received.
            topic:   Bus topic label, injected via the args tuple on subscribe.
            index:   Subscriber index assigned at subscription time, used to
                     distinguish multiple handlers in the output.
        """
        print(f"[{topic}] - Handler {index} - Message received: {message!r}")

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu() -> None:     # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the messaging module.

        Allows subscribing and unsubscribing multiple handlers, publishing
        command and error messages (including from threads and processes), and
        deliberately triggering the invalid-message fatal-exit path.

        Subscription tokens returned by Commands.subscribe and Errors.subscribe
        are stored in cmd_tokens and err_tokens respectively so that individual
        handlers can later be unsubscribed by their assigned index.
        """

        # Show menu with test options
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

        cmd_index = 0       # Next index to assign to a new COMMAND handler
        err_index = 0       # Next index to assign to a new ERROR handler

        # Subscription tokens indexed by handler index so specific handlers
        # can be targeted by the unsubscribe options (12 and 13).
        cmd_tokens: dict[int, Queue] = {}
        err_tokens: dict[int, Queue] = {}

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                # Non-integer input; fall through to the default case
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    n = input("Enter number of COMMAND handlers to subscribe: ")
                    for _ in range(int(n)):
                        print(f"Subscribe COMMAND handler {cmd_index}...")
                        token = Commands.subscribe(handler, args=(Topic.COMMAND, cmd_index,))
                        cmd_tokens[cmd_index] = token
                        cmd_index += 1
                case 2:
                    n = input("Enter number of ERROR handlers to subscribe: ")
                    for _ in range(int(n)):
                        print(f"Subscribe ERROR handler {err_index}...")
                        token = Errors.subscribe(handler, args=(Topic.ERROR, err_index,))
                        err_tokens[err_index] = token
                        err_index += 1
                case 3:
                    if not cmd_tokens:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message...")
                    Commands.publish(CommandMessage("worker", "command message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message{NC}\n")
                case 4:
                    if not err_tokens:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("Publishing ERROR message...")
                    Errors.publish(ErrorMessage("worker", "error message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message{NC}\n")
                case 5:
                    if not cmd_tokens:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message with extra data...")
                    Commands.publish(CommandMessage("worker", "command message", "extra data"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message with extra data{NC}\n")
                case 6:
                    if not cmd_tokens:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from THREAD...")
                    Thread(target=Commands.publish, args=(CommandMessage("worker", "command message from thread"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message from THREAD{NC}\n")
                case 7:
                    if not err_tokens:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from THREAD...")
                    Thread(target=Errors.publish, args=(ErrorMessage("worker", "error message from thread"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message from THREAD{NC}\n")
                case 8:
                    if not cmd_tokens:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from PROCESS...")
                    Process(target=Commands.publish, args=(CommandMessage("worker", "command message from process"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message from PROCESS{NC}\n")
                case 9:
                    if not err_tokens:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from PROCESS...")
                    Process(target=Errors.publish, args=(ErrorMessage("worker", "error message from process"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message from PROCESS{NC}\n")
                case 10:
                    # Deliberately pass an ErrorMessage to Commands.publish
                    # to exercise the type-check fatal-exit path
                    print("Publishing invalid COMMAND message...")
                    Commands.publish(ErrorMessage("worker", "error message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching error sending error message to command queue{NC}\n")
                case 11:
                    # Deliberately pass a CommandMessage to Errors.publish
                    # to exercise the type-check fatal-exit path
                    print("Publishing invalid ERROR message...")
                    Errors.publish(CommandMessage("worker", "command message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching error sending command message to error queue{NC}\n")
                case 12:
                    if not cmd_tokens:
                        print(f"{YELLOW}No subscribed COMMAND handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(cmd_tokens))
                        raw = input(f"Active COMMAND handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in cmd_tokens:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing COMMAND handler {idx}...")
                            Commands.unsubscribe(cmd_tokens.pop(idx))
                            sleep(0.5)  # Allow the sentinel to propagate
                            print(f"{GREEN}COMMAND handler {idx} unsubscribed{NC}\n")
                case 13:
                    if not err_tokens:
                        print(f"{YELLOW}No subscribed ERROR handlers to unsubscribe{NC}\n")
                    else:
                        active = ", ".join(str(i) for i in sorted(err_tokens))
                        raw = input(f"Active ERROR handler indices [{active}] — enter index to unsubscribe: ")
                        try:
                            idx = int(raw)
                        except ValueError:
                            print(f"{YELLOW}Invalid index{NC}\n")
                            continue
                        if idx not in err_tokens:
                            print(f"{YELLOW}Handler {idx} is not subscribed{NC}\n")
                        else:
                            print(f"Unsubscribing ERROR handler {idx}...")
                            Errors.unsubscribe(err_tokens.pop(idx))
                            sleep(0.5)  # Allow the sentinel to propagate
                            print(f"{GREEN}ERROR handler {idx} unsubscribed{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
