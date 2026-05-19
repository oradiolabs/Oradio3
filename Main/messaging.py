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
    IMPORTANT:
        Validating messages on put --> trust messages on get
        Application execution is stopped on queue errors
"""
import sys
import logging
from queue import Full
from typing import NoReturn
from multiprocessing import Queue
from dataclasses import dataclass

##### Oradio modules ####################
from oradio_logging import oradio_log

##### LOCAL constants ####################
MAX_QUEUE_SIZE = 100                # Maximum number of messages for queues
CMD_QUEUE_NAME = "command_queue"    # Human readable name for command queue
ERR_QUEUE_NAME = "error_queue"      # Human readable name for error queue

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

# Queue used for internal command messages
_command_queue = Queue(maxsize=MAX_QUEUE_SIZE)

# Queue used for internal error messages
_error_queue = Queue(maxsize=MAX_QUEUE_SIZE)

##### Helpers ####################

def _safe_put(queue: Queue, message: CommandMessage | ErrorMessage, queue_name: str) -> None:
    """
    Safely validate and enqueue a message into a multiprocessing queue.
    Validation is performed before enqueueing to ensure only well-formed messages enter the system.
    This function operates in non-blocking mode, terminates the application on errors.
    Args:
        queue: Target multiprocessing queue
        message: Message to enqueue (CommandMessage or ErrorMessage)
        queue_name: Human-readable name for logging
    """
    # Validate CommandMessage
    if isinstance(message, CommandMessage):
        # Validate whether message is a well-formed CommandMessage
        if not message.is_valid():
            _fatal_exit(f"Invalid CommandMessage rejected: {message}")

    # Validate ErrorMessage
    elif isinstance(message, ErrorMessage):
        # Validate whether message is a well-formed ErrorMessage
        if not message.is_valid():
            _fatal_exit(f"Invalid ErrorMessage rejected: {message}")

    # Reject unknown types
    else:
        _fatal_exit(f"Unknown message type rejected: {message}")

    # Enqueue safely
    try:
        # Use non-blocking mode so queue.Full can be raised immediately
        queue.put_nowait(message)

    except Full:
        # Queue has reached its capacity
        _fatal_exit(f"Queue '{queue_name}' is full. Message: {message}")

    except (OSError, EOFError, ValueError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        _fatal_exit(f"Queue '{queue_name}' is closed/broken — failed to put message: {message}", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        _fatal_exit(f"Queue '{queue_name}' internal error on put: {message}", exc=ex_err)

def _safe_get(queue: Queue, queue_name: str) -> CommandMessage | ErrorMessage:
    """
    Safely retrieve and validate a message from a multiprocessing queue.
    This function blocks until a message becomes available, terminates the application on errors.
    Messages can be trusted as they are validated upon sending.
    Args:
        queue: Source multiprocessing queue
        queue_name: Human-readable name for logging
    Returns:
        A valid CommandMessage or ErrorMessage
    """
    try:
        # Wait for a message indefinitely
        return queue.get()

    except (OSError, EOFError, BrokenPipeError) as ex_err:
        # Queue is closed, corrupted, or no longer available
        _fatal_exit(f"Queue '{queue_name}' is closed/broken — failed to get message", exc=ex_err)

    except AssertionError as ex_err:
        # Rare internal multiprocessing queue failure
        _fatal_exit(f"Queue '{queue_name}' internal error on get", exc=ex_err)

##### Public API ####################

def put_command_message(message: CommandMessage) -> None:
    """
    Insert a command message into the internal command queue.
    The message type is verified before enqueueing.
    Additional structural validation is performed by `_safe_put()`.
    Args:
        message: Command message to enqueue
    """
    # Validate
    if not isinstance(message, CommandMessage):
        _fatal_exit(f"Wrong message type for queue '{CMD_QUEUE_NAME}': {message}")

    # Attempt to enqueue the command message safely
    _safe_put(_command_queue, message, CMD_QUEUE_NAME)

def get_command_message() -> CommandMessage:
    """
    Retrieve the next command message from the command queue.
    The returned message can be trusted because all messages are
    validated before entering the queue system.
    This function blocks until a message becomes available or a
    fatal queue error occurs.
    Returns:
        The next available `CommandMessage`
    """
    # Attempt to safely retrieve a message from the command queue
    return _safe_get(_command_queue, CMD_QUEUE_NAME)

def put_error_message(message: ErrorMessage) -> None:
    """
    Insert an error message into the internal error queue.
    The message type is verified before enqueueing.
    Additional structural validation is performed by `_safe_put()`.
    Args:
        message: Error message to enqueue
    """
    # Validate
    if not isinstance(message, ErrorMessage):
        _fatal_exit(f"Wrong message type for queue '{ERR_QUEUE_NAME}': {message}")

    # Attempt to enqueue the error message safely
    _safe_put(_error_queue, message, ERR_QUEUE_NAME)

def get_error_message() -> ErrorMessage:
    """
    Retrieve the next error message from the error queue.
    The returned message can be trusted because all messages are
    validated before entering the queue system.
    This function blocks until a message becomes available or a
    fatal queue error occurs.
    Returns:
        The next available `ErrorMessage`
    """
    # Attempt to safely retrieve a message from the error queue
    return _safe_get(_error_queue, ERR_QUEUE_NAME)

def _fatal_exit(message: str, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush logging handlers, and terminate execution.
    This function is intended for unrecoverable infrastructure failures
    such as queue corruption, invalid internal state, or IPC failure.
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
        except Exception:
            pass

    # If your logger uses file handlers, this helps ensure disk flush
    logging.shutdown()

    # Exit python execution
    sys.exit(code)
