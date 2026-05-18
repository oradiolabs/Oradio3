#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on May 15, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides a top level command and error queue with safe wrapper methods
    IMPORTANT: Validating messages on put --> trust messages on get
"""
from queue import Full
from multiprocessing import Queue
from dataclasses import dataclass

##### Oradio modules ####################
from oradio_logging import oradio_log

##### LOCAL constants ####################
MAX_QUEUE_SIZE = 100                # Maximum number of messages for queues
CMD_QUEUE_NAME = "command_queue"    # Human readable name for command queue
ERR_QUEUE_NAME = "error_queue"      # Human readable name for error queue

@dataclass
class CommandMessage:
    """
    Message sent through the command queue
    Attributes:
        source: Name of the component sending the message
        message: Command payload
    """
    source: str
    message: str

    def is_valid(self) -> bool:
        """ Validate """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and self.source.strip() != ""
            and self.message.strip() != ""
        )

@dataclass
class ErrorMessage:
    """
    Message sent through the error queue
    Attributes:
        source: Name of the component reporting the error
        message: Error description
    """
    source: str
    message: str

    def is_valid(self) -> bool:
        """ Validate """
        return (
            isinstance(self.source, str)
            and isinstance(self.message, str)
            and self.source.strip() != ""
            and self.message.strip() != ""
        )

# Queue used for internal command messages
_command_queue = Queue(maxsize=MAX_QUEUE_SIZE)

# Queue used for internal error messages
_error_queue = Queue(maxsize=MAX_QUEUE_SIZE)

##### Helpers ####################

def _safe_put(queue: Queue, message: CommandMessage | ErrorMessage, queue_name: str) -> bool:
    """
    Safely validate and enqueue a message into a multiprocessing queue
    Validation is performed before enqueueing to ensure only well-formed
    messages enter the system
    Args:
        queue: Target multiprocessing queue.
        message: Message to enqueue (CommandMessage or ErrorMessage).
        queue_name: Human-readable name for logging.
    Returns:
        True if message was successfully enqueued, False otherwise.
    """
    # Validate CommandMessage
    if isinstance(message, CommandMessage):
        # Validate whether message is a well-formed CommandMessage
        if not message.is_valid():
            oradio_log.error("Invalid CommandMessage rejected: %r", message)
            return False

    # Validate ErrorMessage
    elif isinstance(message, ErrorMessage):
        # Validate whether message is a well-formed ErrorMessage
        if not message.is_valid():
            oradio_log.error("Invalid ErrorMessage rejected: %r", message)
            return False

    # Reject unknown types
    else:
        oradio_log.error("Unknown message type rejected: %r", message)
        return False

    # Enqueue safely
    try:
        # Use non-blocking mode so queue.Full can be raised immediately
        queue.put_nowait(message)
        return True

    except Full:
        # Queue has reached its capacity
        oradio_log.error("Queue '%s' is full — dropping message: %r", queue_name, message)

    except (OSError, EOFError, ValueError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        oradio_log.error("Queue '%s' is closed/broken — failed to put message: %r (%s)", queue_name, message, ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        oradio_log.critical("Queue '%s' internal error: %s", queue_name, ex_err, exc_info=True)

    # Error enqueuing message
    return False

def _safe_get(queue: Queue, queue_name: str) -> CommandMessage | ErrorMessage | None:
    """
    Safely retrieve and validate a message from a multiprocessing queue
    Messages can be trusted as they are validated upon sending
    Args:
        queue: Source multiprocessing queue
        queue_name: Human-readable name for logging
    Returns:
        A valid CommandMessage or ErrorMessage, or None if errors found
    """
    try:
        # Wait for a message indefinitely
        return queue.get()

    except (OSError, EOFError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        oradio_log.error("Queue '%s' is closed/broken — failed to get message (%s)", queue_name, ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        oradio_log.critical("Queue '%s' internal error during get: %s", queue_name, ex_err, exc_info=True)

    # Error getting message from queue
    return None

##### Public API ####################

def put_command_message(message: CommandMessage) -> bool:
    """
    Put a _command_ message into the _command_ queue
    Args:
        message: Command message to enqueue
    """
    # Validate
    if not isinstance(message, CommandMessage):
        oradio_log.error("Wrong message type for queue '%s': %r", CMD_QUEUE_NAME, message)
        return False

    # Attempt to enqueue the command message safely
    return _safe_put(_command_queue, message, CMD_QUEUE_NAME)

def get_command_message() -> CommandMessage | None:
    """
    Retrieve a command message from the command queue
    Messages can be trusted as they are validated upon sending
    Returns:
        The next message from the command queue, or None if the queue is unavailable
    """
    # Attempt to safely retrieve a message from the command queue
    return _safe_get(_command_queue, CMD_QUEUE_NAME)

def put_error_message(message: ErrorMessage) -> bool:
    """
    Put an _error_ message into the _error_ queue
    Args:
        message: Error message to enqueue
    """
    # Validate
    if not isinstance(message, ErrorMessage):
        oradio_log.error("Wrong message type for queue '%s': %r", ERR_QUEUE_NAME, message)
        return False

    # Attempt to enqueue the error message safely
    return _safe_put(_error_queue, message, ERR_QUEUE_NAME)

def get_error_message() -> ErrorMessage | None:
    """
    Retrieve an error message from the error queue
    Messages can be trusted as they are validated upon sending
    Returns:
        The next message from the error queue, or None if the queue is unavailable
    """
    # Attempt to safely retrieve a message from the error queue
    return _safe_get(_error_queue, ERR_QUEUE_NAME)
