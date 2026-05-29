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
from messaging import (
    CommandMessage,
    subscribe_commands,
    publish_command,
    unsubscribe_commands,
    ErrorMessage,
    subscribe_errors,
    publish_error,
    unsubscribe_errors,
    safe_get,
    fatal_exit,

    get_error_message,
    put_error_message,
    get_command_message,
    put_command_message,
)

class ErrorService:
    """
    Background service responsible for handling runtime error messages.
    The service continuously monitors for incoming error messages and
    applies mitigation or recovery actions based on the error source.
    Known errors are handled automatically when possible.
    Unknown errors are logged as a fail-safe mechanism.
    """
    def __init__(self):
        """
        Initialize and start the error handling service.
        A daemon thread is started automatically, allowing the service
        to run continuously in the background without blocking the main
        application thread.
        """
        self._queue = subscribe_errors()
        Thread(target=_error_handler, args=(self._queue,), daemon=True).start()

    def _error_handler(self, queue):
        """
        Error handling loop.
        Continuously waits for error messages from the shared queue.
        When an error is received, the service attempts to recover
        from known error conditions by issuing appropriate commands.
        """
        while True:
            # Wait for error message
            error = safe_get(queue)

            # Error getting message
            if error is None:
                # Mitigate messaging error
                sleep(1)
                continue

            oradio_log.debug("[ERROR SERVICE] received: %r", error)

            # Mitigation logic for known errors
#OMJ: Nog te implementeren
#            if error.source == "worker":
#                publish_command(CommandMessage("error service", "command message"))
#                continue

            # Fail-safe for unknown errors
            fatal_exit(f"[ERROR SERVICE] Unhandled error: {error!r}")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # GLOBAL constants
    from oradio_const import GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """ Show menu with test options """

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Publish ERROR <xxx> message\n"
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
                    print("\nPublish error message...")
                    publish_error(ErrorMessage("worker", "error message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing error message{NC}\n")

    # Start the error queue handler service
    ErrorService()

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
