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

##### Oradio modules ####################
from oradio_logging import oradio_log
from messaging import (
    ErrorMessage,
    publish_error,
    subscribe_errors,
    THROTTLING_SOURCE,
    THROTTLING_ERROR_THROTTLED,
    USB_SOURCE,
    USB_ERROR_FILE,
    USB_ERROR_SERVICE,
)

##### LOCAL constants ####################
TEST_SOURCE = "Test error message"
UNEXPECTED = "Unexpected source"

def _error_handler(error) -> bool | None:
    """
    Error handling loop.
    Attempts to recover from known error conditions.
    Returns:
    True if if message was handled, False if not.
    """
    # Mitigation logic for known errors
    oradio_log.debug("Error message received: %r", error)
    if error.source == THROTTLING_SOURCE:
        if error.message == THROTTLING_ERROR_THROTTLED:
            oradio_log.debug("Throttled mitigation to be implemented")
        else:
            oradio_log.debug("Unexpected throttling error: '%s'", error.message)
            return False
    elif error.source == USB_SOURCE:
        if error.message == USB_ERROR_FILE:
            oradio_log.debug("USB file error mitigation to be implemented")
        if error.message == USB_ERROR_SERVICE:
            oradio_log.debug("USB service error mitigation to be implemented")
        else:
            oradio_log.debug("Unexpected USB error: '%s'", error.message)
            return False
    elif error.source == TEST_SOURCE:
        oradio_log.debug("Mitigating test error: '%s'", error.message)
    else:
        # Error not recognized
        return False

# Initialize and start the error handling service.
# A daemon thread is started automatically, allowing the service to run
# continuously in the background without blocking the main application thread.
subscribe_errors(_error_handler)

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from oradio_const import RED, YELLOW, GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """ Show menu with test options """

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Publish TEST message\n"
            " 2-Publish UNEXPECTED message (exits python application)\n"
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
                    publish_error(ErrorMessage(TEST_SOURCE, "error test message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing error message{NC}\n")
                case 2:
                    print("\nPublish unexpected message...")
                    publish_error(ErrorMessage(UNEXPECTED, "Unexpected message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching unknown error{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
