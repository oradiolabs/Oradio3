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
    Inherits from str so members can be used directly as string keys.
    """
    COMMAND = "command"
    ERROR = "error"

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
        if fatal_error:
            _fatal_exit(fatal_error, exc=fatal_exception)

    def publish(self, topic: Topic, message: CommandMessage | ErrorMessage) -> None:
        """
        Publish a message to all subscribers of a given topic
        Validates the topic and message before delivery.
        Terminates the application if the queue is full or broken.
        Args:
            topic: The topic to publish to
            message: The validated message to deliver
        """
        # Validate topic
        if topic not in self._subscribers:
            _fatal_exit(f"Unknown topic: {topic!r}")

        # Validate message
        if isinstance(message, CommandMessage):
            if not message.is_valid():
                _fatal_exit(f"Invalid CommandMessage rejected: {message}")
        elif isinstance(message, ErrorMessage):
            if not message.is_valid():
                _fatal_exit(f"Invalid ErrorMessage rejected: {message}")
        else:
            _fatal_exit(f"Unknown message type rejected: {message}")

        # Thread-safe publish to subscribers
        fatal_error = None
        fatal_exception = None
        with self.lock:
            for queue in self._subscribers[topic]:
                try:
                    queue.put_nowait(message)
                except Full:
                    fatal_error = f"Queue for topic {topic!r} is full. Message: {message}"
                    break
                except (OSError, EOFError, ValueError) as ex_err:
                    fatal_error = f"Queue for topic {topic!r} is closed/broken: {message}"
                    fatal_exception = ex_err
                    break
                except AssertionError as ex_err:
                    fatal_error = f"Queue for topic {topic!r} internal error: {message}"
                    fatal_exception = ex_err
                    break
        if fatal_error:
            _fatal_exit(fatal_error, exc=fatal_exception)

# Global PubSub manager
pubsub = PubSubManager()

##### Public API ####################

def subscribe_command() -> Queue:
    """Subscribe a new queue to receive command messages"""
    return pubsub.subscribe(Topic.COMMAND)

def subscribe_error() -> Queue:
    """Subscribe a new queue to receive error messages"""
    return pubsub.subscribe(Topic.ERROR)

def unsubscribe_command(queue: Queue) -> None:
    """
    Unsubscribe a queue from command messages
    Args:
        queue: The queue object to remove
    """
    pubsub.unsubscribe(Topic.COMMAND, queue)

def unsubscribe_error(queue: Queue) -> None:
    """
    Unsubscribe a queue from error messages
    Args:
        queue: The queue object to remove
    """
    pubsub.unsubscribe(Topic.ERROR, queue)

def publish_command(message: CommandMessage) -> None:
    """
    Publish a command message to all subscribers.
    Args:
        message: The CommandMessage to publish
    """
    pubsub.publish(Topic.COMMAND, message)

def publish_error(message: ErrorMessage) -> None:
    """
    Publish an error message to all subscribers.
    Args:
        message: The ErrorMessage to publish
    """
    pubsub.publish(Topic.ERROR, message)

def safe_get(queue: Queue) -> CommandMessage | ErrorMessage:
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
        _fatal_exit(f"Queue is closed/broken — failed to get message", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        _fatal_exit(f"Queue internal error on get", exc=ex_err)

#TODO: add stand-alone test menu
