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
    Provides publish-subscribe messaging pattern
    IMPORTANT:
        Validating messages on publish
        Errors stop application execution
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
THROTTLING_SOURCE = "Throttling message"
THROTTLING_ERROR_THROTTLED = "RPi throttled"
# USB
USB_SOURCE = "USB message"
USB_ABSENT = "USB drive absent"
USB_PRESENT = "USB drive present"
USB_ERROR_FILE = "USB file error"
USB_ERROR_SERVICE = "USB service error"

'''
# wifi
MESSAGE_WIFI_SOURCE          = "Wifi message"
MESSAGE_WIFI_FAIL_CONFIG     = "Failed to save credentials in NetworkManager"
MESSAGE_WIFI_FAIL_START_AP   = "Failed to start access point"
MESSAGE_WIFI_FAIL_CONNECT    = "Wifi failed to connect"
MESSAGE_WIFI_FAIL_STOP_AP    = "Failed to stop access point"
MESSAGE_WIFI_FAIL_DISCONNECT = "Wifi failed to disconnect"
# Messages from fastapi to web service
MESSAGE_REQUEST_CONNECT = "connect to wifi network"
MESSAGE_REQUEST_STOP    = "stop web service"
# web service
MESSAGE_WEB_SERVICE_SOURCE       = "web service message"
MESSAGE_WEB_SERVICE_PL1_PLAYLIST = "PL1 changed to playlist"
MESSAGE_WEB_SERVICE_PL2_PLAYLIST = "PL2 changed to playlist"
MESSAGE_WEB_SERVICE_PL3_PLAYLIST = "PL3 changed to playlist"
MESSAGE_WEB_SERVICE_PL1_WEBRADIO = "PL1 changed to webradio"
MESSAGE_WEB_SERVICE_PL2_WEBRADIO = "PL2 changed to webradio"
MESSAGE_WEB_SERVICE_PL3_WEBRADIO = "PL3 changed to webradio"
MESSAGE_WEB_SERVICE_PLAYING_SONG = "web service plays a song"
MESSAGE_WEB_SERVICE_FAIL_START   = "web service failed to start"
MESSAGE_WEB_SERVICE_FAIL_STOP    = "web service failed to stop"
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

class Topic(str, Enum):
    """
    Valid pub-sub topics.
    Inherits from str so members can be used directly as string keys,
    e.g. in JSON serialisation and log output, without explicit .value access.
    """
    COMMAND = "COMMAND"
    ERROR = "ERROR"

@dataclass(frozen=True) # Immutable instances after creation
class CommandMessage:
    """
    Message sent through the command queue.
    Attributes:
        source: Name of the process, service, or component sending the message
        message: Command payload or instruction
    """
    source: str
    message: str
    data: Any = None    # Optional

    def is_valid(self) -> bool:
        """
        Validate the message contents.
        A valid message:
        - contains string values for all fields
        - contains non-empty, non-whitespace-only values
        data is anything and optional, so not validated.
        Returns:
            True if the message is structurally valid, otherwise False.
        """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and bool(self.source.strip())
            and bool(self.message.strip())
        )

@dataclass(frozen=True) # Immutable instances after creation
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
        Validate the message contents.
        A valid message:
        - contains string values for all fields
        - contains non-empty, non-whitespace-only values
        Returns:
            True if the message is structurally valid, otherwise False.
        """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and bool(self.source.strip())
            and bool(self.message.strip())
        )

##### Helpers ##################################

def _safe_get(queue: Queue) -> CommandMessage | ErrorMessage | NoReturn:
    """
    Safely retrieve a message from a multiprocessing queue.
    Messages can be trusted as structurally valid; validation occurs at
    publish time. Terminates the application if the queue becomes broken.
    Args:
        queue: The source multiprocessing queue to read from
    Returns:
        A valid CommandMessage or ErrorMessage
    """
    try:
        # Wait for a message indefinitely
        return queue.get()

    except (OSError, EOFError, BrokenPipeError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        _fatal_exit("Queue is closed/broken — failed to get message", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        _fatal_exit("Queue internal error on get", exc=ex_err)

def _fatal_exit(message: str, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush logging handlers, and terminate execution.
    Intended for unrecoverable infrastructure failures such as
    queue corruption, invalid internal state, or IPC failure.
    Uses os._exit to ensure termination from any thread, including
    daemon threads where sys.exit would only exit the calling thread.
    Args:
        message: Human-readable fatal error description
        exc: Optional exception associated with the failure
        code: Process exit status code. Defaults to 1
    """
    # stacklevel needs to be 4 to show the file and line where _fatal_exit is called
    oradio_log.critical(message, stacklevel=4, exc_info=exc is not None)

    # Ensure disk flush
    oradio_log.shutdown()

    # Flush console buffers
    sys.stderr.flush()
    sys.stdout.flush()

    # Exit python execution
    os._exit(code)

##### Pub-Sub Infrastructure ####################

def _subscription_listener(queue: Queue, callback: callable, args: tuple) -> None:
    """
    Long-running loop that drives a subscriber callback.
    Intended to run in a dedicated daemon thread created by subscribe().
    Retrieves messages from the queue with _safe_get() and forwards each
    one to the callback as callback(message, *args).
    Args:
        topic:    The topic this listener is serving (used for log context only)
        queue:    The subscriber queue to drain
        callback: Callable invoked for every received message
        args:     Extra positional arguments forwarded to the callback
    """
    while True:
        message = _safe_get(queue)
        if callback(message, *args) is False:
            # Policy: any unhandled message is treated as unrecoverable.
            # _fatal_exit terminates the application to prevent undefined behaviour.
            _fatal_exit(f"Unhandled message: {message!r}")

@singleton
class PubSubManager:
    """
    Manages command and error subscribers
    Maintains a registry of per-topic subscriber queues and provides
    thread-safe subscribe, unsubscribe, and publish operations.
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

        # multiprocessing.Lock and multiprocessing.Queue are used intentionally
        # over their threading equivalents. This ensures the pub-sub infrastructure
        # is safe for use across both threads and child processes without change.
        self.lock = Lock()

    def subscribe(self, topic: Topic, callback: callable, args: tuple = ()):
        """
        Subscribe a new queue to receive messages for a given topic.
        All cached messages (the last message per source) are immediately
        enqueued into the new queue so the subscriber starts with a
        consistent view of the last known state.
        When a callback is supplied a daemon thread is started automatically.
        The thread calls callback(message, *args) for every received message,
        eliminating the need for callers to write their own listener loop.
        When no callback is given the queue is returned for the caller to
        drain manually (original behaviour, fully backward-compatible).
        Args:
            topic:    The topic to subscribe to
            callback: Callable invoked for every received message.
                      Signature: callback(message, *args)
            args:     Extra positional arguments forwarded to the callback.
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Validate callback
        if not callable(callback):
            _fatal_exit(f"callback must be callable, got: {callback!r}")

        # Create and add queue for publishing messages.
        # Replay cached messages inside the same lock so the new subscriber
        # cannot miss a publish that races with subscription.
        fatal_errors: list[tuple[str, BaseException | None]] = []
        with self.lock:
            queue = Queue(maxsize=MAX_QUEUE_SIZE)
            self._subscribers[topic].append(queue)

            # Deliver the last known message for every source to the new subscriber
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

        # Note: _fatal_exit is intentionally called outside the lock.
        if fatal_errors:
            for error, exc in fatal_errors[:-1]:
                oradio_log.critical(error, exc_info=exc is not None)
            last_error, last_exc = fatal_errors[-1]
            _fatal_exit(last_error, exc=last_exc)

        # Start a listener thread for callback provided.
        # The thread is started after the lock is released; any messages
        # published between cache-replay and thread-start land in the queue
        # and will be picked up when the thread begins draining it.
        Thread(target=_subscription_listener, args=(queue, callback, args), daemon=True).start()

    def publish(self, topic: Topic, message: CommandMessage | ErrorMessage) -> None:
        """
        Publish a message to all subscribers of a given topic.
        The last message per source is cached; subsequent subscribers will
        receive it on subscribe. Any previous cached message for the same
        source is replaced.
        Note: Callers are responsible for validating message type and structure
        before calling this method. Use publish_command() or publish_error()
        from the public API, which enforce this.
        Terminates the application if the queue is full or broken.
        Args:
            topic: The topic to publish to
            message: The validated message to deliver
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Thread-safe publish to all subscribers, collecting any failures.
        # Cache the most recent message per source inside the same lock so
        # the cache is always consistent with what subscribers have received.
        fatal_errors: list[tuple[str, BaseException | None]] = []
        with self.lock:
            # Replace the previous cached message for this source (if any)
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

        # Note: _fatal_exit is intentionally called outside the lock.
        # Storing the errors ensures the lock is released before terminating,
        # preventing other threads from hanging on acquire() during shutdown.
        if fatal_errors:
            # Log all failures except the last, then fatal-exit on the last
            for error, exc in fatal_errors[:-1]:
                oradio_log.critical(error, exc_info=exc is not None)
            last_error, last_exc = fatal_errors[-1]
            _fatal_exit(last_error, exc=last_exc)

# Global PubSub manager
pubsub = PubSubManager()

##### Public API ####################

def subscribe_commands(callback: callable, args: tuple = ()) -> None:
    """
    Subscribe a new queue to receive command messages.
    The queue is pre-populated with the last command message for every
    source that has published since the application started.
    When callback is provided a daemon thread is started automatically;
    it calls callback(message, *args) for each received message so the
    caller does not need to manage a listener loop manually.
    Args:
        callback: Optional callable invoked for every received message.
                  Signature: callback(message, *args)
        args:     Extra positional arguments forwarded to the callback.
    """
    pubsub.subscribe(Topic.COMMAND, callback, args)

def subscribe_errors(callback: callable, args: tuple = ()) -> None:
    """
    Subscribe a new queue to receive error messages.
    The queue is pre-populated with the last error message for every
    source that has published since the application started.
    When callback is provided a daemon thread is started automatically;
    it calls callback(message, *args) for each received message so the
    caller does not need to manage a listener loop manually.
    Args:
        callback: Optional callable invoked for every received message.
                  Signature: callback(message, *args)
        args:     Extra positional arguments forwarded to the callback.
    """
    pubsub.subscribe(Topic.ERROR, callback, args)

def publish_command(message: CommandMessage) -> None:
    """
    Publish a command message to all subscribers.
    The most recent message per source is cached; late subscribers receive
    it automatically on subscribe.
    The message type is verified before publishing.
    Args:
        message: The CommandMessage to publish
    """
    # Validate message type
    if not isinstance(message, CommandMessage):
        _fatal_exit(f"Wrong message type for publish_command: {message}")

    # Validate message structure
    if not message.is_valid():
        _fatal_exit(f"Invalid CommandMessage rejected: {message}")

    # Attempt to publish the command message safely
    pubsub.publish(Topic.COMMAND, message)

def publish_error(message: ErrorMessage) -> None:
    """
    Publish an error message to all subscribers.
    The most recent message per source is cached; late subscribers receive
    it automatically on subscribe.
    The message type is verified before publishing.
    Args:
        message: Error message to enqueue
    """
    # Validate message type
    if not isinstance(message, ErrorMessage):
        _fatal_exit(f"Wrong message type for publish_error: {message}")

    # Validate message structure
    if not message.is_valid():
        _fatal_exit(f"Invalid ErrorMessage rejected: {message}")

    # Attempt to publish the error message safely
    pubsub.publish(Topic.ERROR, message)

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
        """Monitor messaging queue"""
        print(f"[{topic}] - Handler {index} - Message received: {message!r}")

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu() -> None:     # pylint: disable=too-many-branches,too-many-statements
        """ Show menu with test options """

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
            "select: "
        )

        # Initialize
        cmd_index = 0
        err_index = 0

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
                    n = input("Enter number of COMMAND handlers to subscribe: ")
                    for _ in range(int(n)):
                        print(f"Subscribe COMMAND handler {cmd_index}...")
                        subscribe_commands(handler, args=(Topic.COMMAND, cmd_index,))
                        cmd_index += 1
                case 2:
                    n = input("Enter number of ERROR handlers to subscribe: ")
                    for _ in range(int(n)):
                        print(f"Subscribe ERROR handler {err_index}...")
                        subscribe_errors(handler, args=(Topic.ERROR, err_index,))
                        err_index += 1
                case 3:
                    if cmd_index == 0:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message...")
                    publish_command(CommandMessage("worker", "command message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message{NC}\n")
                case 4:
                    if err_index == 0:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("Publishing ERROR message...")
                    publish_error(ErrorMessage("worker", "error message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message{NC}\n")
                case 5:
                    if cmd_index == 0:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("Publishing COMMAND message with extra data...")
                    publish_command(CommandMessage("worker", "command message", "extra data"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message with extra data{NC}\n")
                case 6:
                    if cmd_index == 0:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from THREAD...")
                    Thread(target=publish_command, args=(CommandMessage("worker", "command message from thread"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message from THREAD{NC}\n")
                case 7:
                    if err_index == 0:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from THREAD...")
                    Thread(target=publish_error, args=(ErrorMessage("worker", "error message from thread"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message from THREAD{NC}\n")
                case 8:
                    if cmd_index == 0:
                        print(f"{YELLOW}No subscribed COMMAND handlers{NC}")
                    print("\nPublish COMMAND messages from PROCESS...")
                    Process(target=publish_command, args=(CommandMessage("worker", "command message from process"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing COMMAND message from PROCESS{NC}\n")
                case 9:
                    if err_index == 0:
                        print(f"{YELLOW}No subscribed ERROR handlers{NC}")
                    print("\nPublish ERROR messages from PROCESS...")
                    Process(target=publish_error, args=(ErrorMessage("worker", "error message from process"),), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing ERROR message from PROCESS{NC}\n")
                case 10:
                    print("Publishing invalid COMMAND message...")
                    publish_command(ErrorMessage("worker", "error message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching error sending error message to command queue{NC}\n")
                case 11:
                    print("Publishing invalid ERROR message...")
                    publish_error(CommandMessage("worker", "command message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching error sending command message to error queue{NC}\n")

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
