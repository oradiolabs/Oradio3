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
@summary:       Top-level error resolution service. Subscribes to the system error bus
                and attempts to mitigate known error conditions from registered sources.
                Unrecognised errors are flagged and returned to the caller for further handling.
"""
from time import sleep

##### Oradio modules ####################
from oradio_logging import oradio_log
from messaging import (
    ErrorMessage,
    Errors.publish,
    Errors.subscribe,
    THROTTLING_SOURCE,
    THROTTLING_ERROR_THROTTLED,
    USB_SOURCE,
    USB_ERROR_FILE,
    USB_ERROR_SERVICE,
    WIFI_SOURCE,
    WIFI_ERROR_DBUS,
    WIFI_ERROR_NMCLI,
    WIFI_ERROR_CONNECT,
    WIFI_ERROR_DISCONNECT,
)

##### LOCAL constants ####################
# Source identifier used when publishing errors from this module's self-tests
TEST_SOURCE = "Test error message"

# Placeholder source name used to exercise the unrecognised-error code path
UNEXPECTED = "Unexpected source"

# Pylint allows more than 12 branches here because this is a test menu
def error_handler(error) -> bool | None:   # pylint: disable=too-many-branches,inconsistent-return-statements
    """
    Handle an incoming error message and attempt mitigation.

    Called by the messaging layer whenever an ErrorMessage is published on
    the error bus. Dispatches on error.source and applies the appropriate
    mitigation strategy for each known source/message combination.

    Args:
        error: An ErrorMessage instance with at least source and message attributes.

    Returns:
        True  – the error was recognised and handled (mitigation applied).
        False – the error was not recognised; the caller should escalate.
        None  – implicitly returned for recognised sources whose mitigation
                is not yet implemented (treated as handled for now).
    """
    oradio_log.debug("Error message received: %r", error)

    if error.source == THROTTLING_SOURCE:
        if error.message == THROTTLING_ERROR_THROTTLED:
# NIET VERGETEN: implement throttle-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Throttled mitigation to be implemented")
        else:
            oradio_log.debug("Unexpected throttling error: '%s'", error.message)
            return False

    elif error.source == USB_SOURCE:
        if error.message == USB_ERROR_FILE:
# NIET VERGETEN: implement file-level USB error recovery (e.g. re-mount, rescan)
            oradio_log.debug("USB file error mitigation to be implemented")
        elif error.message == USB_ERROR_SERVICE:
# NIET VERGETEN: implement USB service recovery (e.g. restart udev / service)
            oradio_log.debug("USB service error mitigation to be implemented")
        else:
            oradio_log.debug("Unexpected USB error: '%s'", error.message)
            return False

    elif error.source == WIFI_SOURCE:
        if error.message == WIFI_ERROR_DBUS:
# NIET VERGETEN: implement D-Bus event handler error recovery
            oradio_log.debug("Failed D-Bus event handler error mitigation to be implemented")
        if error.message == WIFI_ERROR_NMCLI:
# NIET VERGETEN: implement failed to interact with NetworkManager error recovery
            oradio_log.debug("Failed to interact with NetworkManager error mitigation to be implemented")
        elif error.message == WIFI_ERROR_CONNECT:
# NIET VERGETEN: implement wifi connect failed recovery
# LET OP: wordt in legacy wifi_service als command verstuurd en in oradio_control in state machine afgehandeld
            oradio_log.debug("Wifi connect failed error mitigation to be implemented")
        elif error.message == WIFI_ERROR_DISCONNECT:
# NIET VERGETEN: implement wifi disconnect failed recovery
            oradio_log.debug("Wifi disconnect failed error mitigation to be implemented")
        else:
            oradio_log.debug("Unexpected USB error: '%s'", error.message)
            return False

    elif error.source == TEST_SOURCE:
        # Test errors are considered handled; no real mitigation needed
        oradio_log.debug("Mitigating test error: '%s'", error.message)

    else:
        # Source is not registered with this handler; signal the caller
        return False

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from oradio_const import RED, YELLOW, GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """
        Run an interactive self-test menu for the error handling service.

        Presents numbered options that publish error messages onto the bus so
        the developer can verify that _error_handler responds correctly.
        Loops until the user selects quit (0).
        """

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
                    # Publish a known test error; handler should accept it
                    print("\nPublish error message...")
                    Errors.publish(ErrorMessage(TEST_SOURCE, "error test message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{GREEN}Success publishing error message{NC}\n")
                case 2:
                    # Publish an unrecognised error; handler should return False
                    print("\nPublish unexpected message...")
                    Errors.publish(ErrorMessage(UNEXPECTED, "Unexpected message"))
                    sleep(0.5)  # Allow for message to propagate
                    print(f"{RED}Failed catching unknown error{NC}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Register error_handler with the messaging layer.  Errors.subscribe() starts
    # a daemon thread internally, so the handler runs in the background without blocking
    # the main application thread, and is automatically torn down when the process exits.
    Errors.subscribe(error_handler)

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
