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
"""
from time import sleep
from threading import Thread

##### Oradio modules ####################
from oradio_logging import oradio_log
from oj_utils import (
    ErrorMessage,
    get_error_message,
    put_error_message,
    CommandMessage,
    get_command_message,
    put_command_message,
)

class ErrorService:
    """
    Background service responsible for handling runtime error messages

    The service continuously monitors the shared error queue for incoming
    error messages and applies predefined mitigation or recovery actions
    based on the error source

    Known errors are handled automatically when possible
    Unknown errors are logged as a fail-safe mechanism
    """
    def __init__(self):
        """
        Initialize and start the error handling service

        A daemon thread is started automatically, allowing the service
        to run continuously in the background without blocking the main
        application thread
        """
        Thread(target=self._error_handler, daemon=True).start()

    def _error_handler(self):
        """
        Error handling loop

        Continuously waits for error messages from the shared queue
        When an error is received, the service attempts to recover
        from known error conditions by issuing appropriate commands

        Behavior:
            - Retries if no message could be retrieved
            - Handles known worker-related errors by sending a reset command
            - Logs unknown errors for debugging and fail-safe purposes
        """
        while True:
            # Wait for error message
            error = get_error_message()

            # Error getting message
            if error is None:
                # Mitigate messaging error
                sleep(1)
                continue

            oradio_log.debug("[ERROR SERVICE] received: %r", error)

            # Mitigation logic for known errors
#OMJ: Nog te implementeren
#            if error.source == "worker":
#                put_command_message(CommandMessage("error service", "reset"))
#                continue

            # Fail-safe for unknown error
            oradio_log.error("[ERROR SERVICE] Unknown error!")
#OMJ: Wat doen we met unknown errors?

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    import os
    from multiprocessing import Process

    # GLOBAL constants
    from oradio_const import RED, GREEN, NC

    def _command_loop():
        """ Get messages from command queue """
        while True:
            # Wait for command message
            command = get_command_message()

            # Error getting message
            if command is None:
                # Mitigate messaging error
                sleep(1)
                continue

            print(f"[COMMAND SERVICE]: source={command.source}, message={command.message}")
            # Do something based on the source and command arguments

    def _worker(context="main"):
        """ Send command and error messages """
        put_command_message(CommandMessage("worker", f"worker in {context} context: command message in command queue"))
        put_error_message(ErrorMessage("worker", f"worker in {context} context: error message in error queue"))

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
#    def interactive_menu(queue) -> None:    # pylint: disable=too-many-branches
    def interactive_menu() -> None:
        """ Show menu with test options """

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Send COMMAND message to COMMAND queue\n"
            " 2-Send ERROR message to ERROR queue\n"
            " 3-Send ERROR message to COMMAND queue\n"
            " 4-Send COMMAND message to ERROR queue\n"
            " 5-From thread: Send messages to queues\n"
            " 6-From process: Send messages to queues\n"
            "select: "
        )

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
                    print("\nSending command message to command queue...")
                    result = put_command_message(CommandMessage("worker", "command message in command queue"))
                    sleep(0.5)  # Allow for message to propagate
                    if result:
                        print(f"{GREEN}Success sending command message to command queue{NC}\n")
                    else:
                        print(f"{RED}Failed sending command message to command queue{NC}\n")
                case 2:
                    print("\nSending error message to error queue...\n")
                    result = put_error_message(ErrorMessage("worker", "error message in error queue"))
                    sleep(0.5)  # Allow for message to propagate
                    if result:
                        print(f"{GREEN}Success sending error message to error queue{NC}\n")
                    else:
                        print(f"{RED}Failed sending error message to error queue{NC}\n")
                case 3:
                    print("\nSending error message to command queue...\n")
                    result = put_command_message(ErrorMessage("worker", "error message in command queue"))
                    sleep(0.5)  # Allow for message to propagate
                    if result:
                        print(f"{RED}Failed catching error sending error message to command queue{NC}\n")
                    else:
                        print(f"{GREEN}Success catching error sending error message to command queue{NC}\n")
                case 4:
                    print("\nSending command message to error queue...\n")
                    result = put_error_message(CommandMessage("worker", "command message in error queue"))
                    sleep(0.5)  # Allow for message to propagate
                    if result:
                        print(f"{RED}Failed catching error sending command message to error queue{NC}\n")
                    else:
                        print(f"{GREEN}Success catching error sending command message to error queue{NC}\n")
                case 5:
                    print("\nIn thread: sending messages to queues...")
                    Thread(target=_worker, args=("thread",), daemon=True).start()
                    sleep(0.5)  # Allow for message to propagate
                    print("")   # For clean layout
                case 6:
                    print("\nIn process: sending messages to queues...")
                    Process(target=_worker, args=("process",)).start()
                    sleep(0.5)  # Allow for message to propagate
                    print("")   # For clean layout

    # Start the error queue handler service
    ErrorService()

    # Start process to monitor the message queue
    Thread(target=_command_loop, daemon=True).start()

    # Present menu with tests
    interactive_menu()

    # Stop execution immediately
    os._exit(0)

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
