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
    Provides a top level error resolution service
    Features:
    - a queue for communicating error messages
    - a dedicated error event dataclass
    - a single Error Service consumer thread in the main process
"""

'''
##### TODO #####
- Add proper doc strings and inline comments
- Validate approach against system architecture from Henk

- Add stand-alone test menu
- Split into separate files
- Test / verify how this works for a real module

- Move faulthandler here, currently in oradio_logging
- Add 'if all else fails' (exception?) handler
'''

##### message_bus.py #####

from queue import Empty, Full
from multiprocessing import Queue
from dataclasses import dataclass
from oradio_logging import oradio_log

@dataclass
class CommandMessage:
    source: str
    message: str

@dataclass
class ErrorMessage:
    source: str
    message: str

class MessageBus:
    def __init__(self, error_queue: Queue, command_queue: Queue) -> None:
        self.error_queue = error_queue
        self.command_queue = command_queue

    def _safe_put(self, q, message, q_name) -> None:
        """ Safely put a message into the queue """
        try:
            q.put(message)

        except Full:
            # Queue is full
            oradio_log.error("Queue '%s' is full — dropping message: %r", q_name, message)

        except (OSError, EOFError, ValueError) as ex_err:
            # Queue closed or broken
            oradio_log.error("Queue '%s' is closed/broken — failed to put message: %r (%s)", q_name, message, ex_err)

        except AssertionError as ex_err:
            # Rare internal queue corruption
            oradio_log.critical("Queue '%s' internal error: %s", q_name, ex_err, exc_info=True)

    def _safe_get(self, q, q_name) -> None:
        """ Safely get a message from the queue """
        try:
            return q.get()

        except (OSError, EOFError) as ex_err:
            # Queue closed or broken
            oradio_log.error("Queue '%s' is closed/broken — failed to get message: (%s)", q_name, ex_err)
            return None

        except AssertionError as ex_err:
            # Rare internal queue corruption
            oradio_log.critical("Queue '%s' internal error: %s", q_name, ex_err, exc_info=True)
            return None

    def put_error_message(self, error: ErrorMessage) -> None:
        """ Put error message in the error queue """
        self._safe_put(self.error_queue, error, "error_queue")

    def get_error_message(self) -> None:
        """ Get error message from the error queue """
        return self._safe_get(self.error_queue, "error_queue")

    def put_command_message(self, command: CommandMessage) -> None:
        """ Put command message in the command queue """
        self._safe_put(self.command_queue, command, "command_queue")

    def get_command_message(self) -> None:
        """ Get command message from the command queue """
        return self._safe_get(self.command_queue, "command_queue")

##### error_service.py #####

from threading import Thread
from message_bus import MessageBus, CommandMessage

class ErrorService:
    def __init__(self, bus: MessageBus):
        """ Initialize error service, starting the error handler thread """
        self.bus = bus
        Thread(target=self._run, daemon=True).start()

    def _run(self):
        """ Check if a new message is put into the queue """
        while True:
            # Wait for error message
            error = self.bus.get_error_message()
            
            if error is None:
                continue

            print(f"[ERROR] received: {error}")

            # Mitigation logic for known errors
            if error.source == "worker":
                self.bus.put_command_message(CommandMessage("error service", "reset"))
                continue

            # Fail-safe for unknown error
            self.bus.put_command_message(CommandMessage("error service", "unknown error"))

##### module.py #####

from threading import Thread
from multiprocessing import Process
from message_bus import MessageBus, CommandMessage, ErrorMessage

def dangerous():
    raise Exception("boom")

def worker(bus):
    bus.put_command_message(CommandMessage("worker", "started"))
    try:
        dangerous()
    except Exception:
        bus.put_error_message(ErrorMessage("worker", "failed"))

def start_thread(bus):
    Thread(target=worker, args=(bus,), daemon=True).start()

def start_process(bus):
    p = Process(target=worker, args=(bus,))
    p.start()
    return p

##### main.py #####

from multiprocessing import Queue
from error_service import ErrorService
from message_bus import MessageBus
import module

def main():
    # Create IPC primitives once
    error_queue = Queue()
    command_queue = Queue()
    # Create wrapper including safe methods
    bus = MessageBus(error_queue, command_queue)
    # Start error supervisor
    error_service = ErrorService(bus)

    # Start worker (direct call example)
    module.worker(bus)

    # Start thread worker example
    module.start_thread(bus)

    # Start process worker example
    module.start_process(bus)
    
    # Main command loop example
    while True:
        command = bus.get_command_message()
        if command is None:
            continue

        print(f"[MAIN] command: source={command.source}, message={command.message}")
        # Do something based on the source and command arguments

if __name__ == "__main__":
    main()
