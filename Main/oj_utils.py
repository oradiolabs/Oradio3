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
"""
from queue import Empty, Full
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

# Queue used for internal command messages
_command_queue = Queue(maxsize=MAX_QUEUE_SIZE)

# Queue used for internal error messages
_error_queue = Queue(maxsize=MAX_QUEUE_SIZE)

##### Helpers ####################

def _is_valid_command(msg: object) -> bool:
    """
    Validate whether an object is a well-formed CommandMessage
    Args:
        msg: Any object received from the queue
    Returns:
        True if the object is a valid CommandMessage, False otherwise
    """
    # Must be correct type first
    if not isinstance(msg, CommandMessage):
        return False

    # Ensure required fields are valid strings
    return isinstance(msg.source, str) and isinstance(msg.message, str)


def _is_valid_error(msg: object) -> bool:
    """
    Validate whether an object is a well-formed ErrorMessage
    Args:
        msg: Any object received from the queue
    Returns:
        True if the object is a valid ErrorMessage, False otherwise
    """
    # Must be correct type first
    if not isinstance(msg, ErrorMessage):
        return False

    # Ensure required fields are valid strings
    return isinstance(msg.source, str) and isinstance(msg.message, str)

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
        if not _is_valid_command(message):
            oradio_log.error("Invalid CommandMessage rejected: %r", message)
            return False

    # Validate ErrorMessage
    elif isinstance(message, ErrorMessage):
        if not _is_valid_error(message):
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
    Args:
        queue: Source multiprocessing queue
        queue_name: Human-readable name for logging
    Returns:
        A valid CommandMessage or ErrorMessage, or None if errors found
    """
    try:
        # Wait for a message indefinitly
        message = queue.get()

        # Validate retrieved message
        if _is_valid_command(message):
            return message  # valid command message

        if _is_valid_error(message):
            return message  # valid error message

        # Reject invalid payload
        oradio_log.error("Invalid message received from '%s': %r", queue_name, message)

    except (OSError, EOFError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        oradio_log.error("Queue '%s' is closed/broken — failed to get message (%s)", queue_name, ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        oradio_log.critical("Queue '%s' internal error during get: %s", queue_name, ex_err, exc_info=True)

    # Error condition
    return None

##### Public API ####################

def put_command_message(command: CommandMessage) -> bool:
    """
    Put a command message into the command queue
    Args:
        command: Command message to enqueue
    """
    # Attempt to enqueue the command message safely
    return _safe_put(_command_queue, command, CMD_QUEUE_NAME)

def get_command_message() -> CommandMessage | None:
    """
    Retrieve a message from the command queue
    Returns:
        The next message from the command queue, or None if the queue
        is empty or unavailable
    """
    # Attempt to safely retrieve a message from the command queue
    return _safe_get(_command_queue, CMD_QUEUE_NAME)

def put_error_message(error: ErrorMessage) -> bool:
    """
    Put an error message into the error queue
    Args:
        error: Error message to enqueue
    """
    # Attempt to enqueue the error message safely
    return _safe_put(_error_queue, error, ERR_QUEUE_NAME)

def get_error_message() -> ErrorMessage | None:
    """
    Retrieve a message from the error queue
    Returns:
        The next message from the error queue, or None if the queue
        is empty or unavailable
    """
    # Attempt to safely retrieve a message from the error queue
    return _safe_get(_error_queue, ERR_QUEUE_NAME)
