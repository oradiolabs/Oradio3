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
import sys
import logging
from enum import Enum
from queue import Full
from typing import Any, NoReturn
from dataclasses import dataclass
from multiprocessing import Lock, Queue

##### Oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log

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
            and self.source.strip()
            and self.message.strip()
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
            and self.source.strip()
            and self.message.strip()
        )

##### Helpers ####################

def _fatal_exit(message: str, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush logging handlers, and terminate execution.
    Intended for unrecoverable infrastructure failures such as
    queue corruption, invalid internal state, or IPC failure.
    Args:
        message: Human-readable fatal error description
        exc: Optional exception associated with the failure
        code: Process exit status code. Defaults to 1
    Raises:
        SystemExit: Always raised to terminate execution
    """
    oradio_log.critical(message, exc_info=exc is not None)

    # Flush all logging handlers
    logger = logging.getLogger()
    for handler in logger.handlers:
        try:
            handler.flush()
        except (OSError, ValueError, EOFError):
            pass

    # Ensure disk flush
    logging.shutdown()

    # Flush console buffers
    sys.stderr.flush()
    sys.stdout.flush()

    # Exit python execution
    sys.exit(code)

##### Pub-Sub Infrastructure ####################

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

        # multiprocessing.Lock and multiprocessing.Queue are used intentionally
        # over their threading equivalents. This ensures the pub-sub infrastructure
        # is safe for use across both threads and child processes without change.
        self.lock = Lock()

    def subscribe(self, topic: Topic) -> Queue:
        """
        Subscribe a new queue to receive messages for a given topic.
        Args:
            topic: The topic to subscribe to
        Returns:
            A new Queue that will receive published messages for the topic
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Create and add queue for publishing messages
        with self.lock:
            queue = Queue(maxsize=MAX_QUEUE_SIZE)
            self._subscribers[topic].append(queue)
            return queue

    def unsubscribe(self, topic: Topic, queue: Queue) -> None:
        """
        Unsubscribe a queue from a given topic.
        Args:
            topic: The topic to unsubscribe from
            queue: The queue object to remove
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Thread-safe remove queue for publishing messages
        fatal_error = None
        fatal_exception = None
        with self.lock:
            try:
                self._subscribers[topic].remove(queue)
            except ValueError as ex_err:
                fatal_error = f"Queue not found for topic {topic!r} during unsubscribe"
                fatal_exception = ex_err

        # Note: _fatal_exit is intentionally called outside the lock.
        # Storing the error and breaking out of the `with` block first ensures
        # the lock is released before terminating, preventing other threads from
        # hanging on acquire() during shutdown.
        if fatal_error:
            _fatal_exit(fatal_error, exc=fatal_exception)

    def publish(self, topic: Topic, message: CommandMessage | ErrorMessage) -> None:
        """
        Publish a message to all subscribers of a given topic.
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

        # Thread-safe publish to all subscribers, collecting any failures
        fatal_errors: list[tuple[str, BaseException | None]] = []
        with self.lock:
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

def subscribe_commands() -> Queue:
    """Subscribe a new queue to receive command messages"""
    return pubsub.subscribe(Topic.COMMAND)

def subscribe_errors() -> Queue:
    """Subscribe a new queue to receive error messages"""
    return pubsub.subscribe(Topic.ERROR)

def unsubscribe_commands(queue: Queue) -> None:
    """
    Unsubscribe a queue from command messages
    Args:
        queue: The queue object to remove
    """
    pubsub.unsubscribe(Topic.COMMAND, queue)

def unsubscribe_errors(queue: Queue) -> None:
    """
    Unsubscribe a queue from error messages
    Args:
        queue: The queue object to remove
    """
    pubsub.unsubscribe(Topic.ERROR, queue)

def publish_command(message: CommandMessage) -> None:
    """
    Publish a command message to all subscribers.
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

def safe_get(queue: Queue) -> CommandMessage | ErrorMessage | NoReturn:
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

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from time import sleep
    from threading import Thread
    from multiprocessing import Process

    # GLOBAL constants
    from oradio_const import RED, YELLOW, GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def handler(topic: Topic, queue: Queue, index: int) -> None:
        while True:
            message = safe_get(queue)
            print(f"[{topic}] - Handler {index} - Message received: {message!r}")

    def interactive_menu() -> None:
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
            "10-Publish invalid COMMAND message\n"
            "11-Publish invalid ERROR message\n"
            "select: "
        )

        # Initialize
        cmd_index = 0
        err_index = 0
        cmd_queue = []
        err_queue = []
        cmd_thread = []
        err_thread = []

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
                    # Stop listening to messages
                    for q in range(cmd_index):
                        unsubscribe_commands(cmd_queue[q])
                    for q in range(err_index):
                        unsubscribe_errors(err_queue[q])
                    break
                case 1:
                    n = input("Enter number of COMMAND handlers to subscribe: ")
                    for i in range(int(n)):
                        print(f"Subscribe COMMAND handler {cmd_index+i}...")
                        cmd_queue.append(subscribe_commands())
                        cmd_thread.append(Thread(target=handler, args=(Topic.COMMAND, cmd_queue[cmd_index], cmd_index,), daemon=True))
                        cmd_thread[cmd_index].start()
                        cmd_index += 1
                    print(f"cmd_index={cmd_index}")
                case 2:
                    n = input("Enter number of ERROR handlers to subscribe: ")
                    for i in range(int(n)):
                        print(f"Subscribe ERROR handler {err_index+i}...")
                        err_queue.append(subscribe_errors())
                        err_thread.append(Thread(target=handler, args=(Topic.ERROR, err_queue[err_index], err_index,), daemon=True))
                        err_thread[err_index].start()
                        err_index += 1
                    print(f"err_index={err_index}")
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
