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
    Top-level error handling service.

    Subscribes to the system error bus and applies mitigation for
    recognised error conditions from registered sources. Unknown
    errors are logged for further investigation.
"""
from typing import Callable

##### Oradio modules ######################################
from log_service import oradio_log
from messaging import (
    Errors,
    ErrorMessage,
    MessageHandlerBase,
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
    RMS_SOURCE,
    RMS_ERROR_SERVICE,
    WEB_SOURCE,
    WEB_ERROR_START,
    WEB_ERROR_STOP,
    WEB_ERROR_SERVICE,
    GPIO_SOURCE,
    GPIO_ERROR_SERVICE,
    GPIO_ERROR_BUTTONS,
    BACKLIGHT_SOURCE,
    BACKLIGHT_ERROR_START,
    BACKLIGHT_ERROR_STOP,
    I2C_SOURCE,
    I2C_ERROR_BUS,
    VOLUME_SOURCE,
    VOLUME_ERROR_START,
    VOLUME_ERROR_STOP,
    MPD_SOURCE,
    MPD_ERROR_CONNECT,
    MPD_ERROR_EXECUTE,
    MPD_ERROR_MONITOR,
    SPOTIFY_SOURCE,
    SPOTIFY_ERROR_MONITOR,
)

##### LOCAL constants #####################################
# Source identifier used when publishing errors from this module's self-tests
TEST_SOURCE = "Test error message"

# Placeholder source name used to exercise the unrecognised-error code path
UNEXPECTED = "Unexpected source"

class ErrorHandler(MessageHandlerBase):
    """
    Handle error messages and perform error-specific mitigation.

    Dispatches each message to a source-specific handler method;
    unrecognised sources are logged as errors.
    """
    def __init__(self):
        """
        Subscribe to error messages and call the base class constructor,
        which subscribes to the error bus and starts the worker thread.
        """
        # Subscribe to error messages and initialise base class and start the worker thread
        self._queue = Errors.subscribe()

        # Map each source constant to its handler method.
        # Adding a new source only requires one new line here.
        self._dispatch: dict[str, Callable[[ErrorMessage], None]] = {
            THROTTLING_SOURCE: self._handle_throttling_error,
            USB_SOURCE:        self._handle_usb_error,
            WIFI_SOURCE:       self._handle_wifi_error,
            RMS_SOURCE:        self._handle_rms_error,
            WEB_SOURCE:        self._handle_web_error,
            GPIO_SOURCE:       self._handle_gpio_error,
            BACKLIGHT_SOURCE:  self._handle_backlight_error,
            I2C_SOURCE:        self._handle_i2c_error,
            VOLUME_SOURCE:     self._handle_volume_error,
            MPD_SOURCE:        self._handle_mpd_error,
            SPOTIFY_SOURCE:    self._handle_spotify_error,
            TEST_SOURCE:       self._handle_test_error,
        }

        super().__init__(self._queue)

##### Helpers #############################################

    def _handle_throttling_error(self, error: ErrorMessage) -> None:
        """
        Handle throttling-related errors.

        Attempts recovery from known throttling conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == THROTTLING_ERROR_THROTTLED:
# NIET VERGETEN: implement throttle-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Throttled mitigation to be implemented")
        else:
            oradio_log.error("Unhandled throttling error: '%s'", error.message)

    def _handle_usb_error(self, error: ErrorMessage) -> None:
        """
        Handle USB-related errors.

        Attempts recovery from known USB failures and logs unrecognised
        errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == USB_ERROR_FILE:
# NIET VERGETEN: implement file-level USB error recovery (e.g. re-mount, rescan)
            oradio_log.debug("USB file error mitigation to be implemented")
        elif error.message == USB_ERROR_SERVICE:
# NIET VERGETEN: implement USB service recovery (e.g. restart udev / service)
            oradio_log.debug("USB service error mitigation to be implemented")
        else:
            oradio_log.error("Unhandled USB error: '%s'", error.message)

    def _handle_wifi_error(self, error: ErrorMessage) -> None:
        """
        Handle WiFi-related error messages.

        Attempts recovery from known WiFi failures and logs unrecognised
        errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == WIFI_ERROR_DBUS:
# NIET VERGETEN: implement D-Bus event handler error recovery
            oradio_log.debug("Failed D-Bus event handler error mitigation to be implemented")
        elif error.message == WIFI_ERROR_NMCLI:
# NIET VERGETEN: implement failed to interact with NetworkManager error recovery
            oradio_log.debug("Failed to interact with NetworkManager error mitigation to be implemented")
        elif error.message == WIFI_ERROR_CONNECT:
# NIET VERGETEN: implement wifi connect failed recovery
# LET OP: wordt in wifi_service als command verstuurd en in oradio_control in state machine afgehandeld
            oradio_log.debug("Wifi connect failed error mitigation to be implemented")
        elif error.message == WIFI_ERROR_DISCONNECT:
# NIET VERGETEN: implement wifi disconnect failed recovery
            oradio_log.debug("Wifi disconnect failed error mitigation to be implemented")
        else:
            oradio_log.error("Unhandled wifi error: '%s'", error.message)

    def _handle_rms_error(self, error: ErrorMessage) -> None:
        """
        Handle rms-related errors.

        Attempts recovery from known remote monitoring conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == RMS_ERROR_SERVICE:
# NIET VERGETEN: implement rms-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Remote monitoring mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Remote monitoring error: '%s'", error.message)

    def _handle_web_error(self, error: ErrorMessage) -> None:
        """
        Handle web-related errors.

        Attempts recovery from known web service/server conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == WEB_ERROR_SERVICE:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Web mitigation to be implemented")
        elif error.message == WEB_ERROR_START:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Web mitigation to be implemented")
        elif error.message == WEB_ERROR_STOP:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Web mitigation to be implemented")
        else:
            oradio_log.error("Unhandled web error: '%s'", error.message)

    def _handle_gpio_error(self, error: ErrorMessage) -> None:
        """
        Handle gpio-related errors.

        Attempts recovery from known GPIO conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == GPIO_ERROR_SERVICE:
# NIET VERGETEN: implement gpio-recovery logic (e.g. back-off, retry)
            oradio_log.debug("GPIO service mitigation to be implemented")
        elif error.message == GPIO_ERROR_BUTTONS:
# NIET VERGETEN: implement gpio-recovery logic (e.g. back-off, retry)
            oradio_log.debug("GPIO buttons mitigation to be implemented")
        else:
            oradio_log.error("Unhandled GPIO error: '%s'", error.message)

    def _handle_backlight_error(self, error: ErrorMessage) -> None:
        """
        Handle backlight-related errors.

        Attempts recovery from known backlight conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == BACKLIGHT_ERROR_START:
# NIET VERGETEN: implement backlight-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Backlight start mitigation to be implemented")
        elif error.message == BACKLIGHT_ERROR_STOP:
# NIET VERGETEN: implement backlight-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Backlight stop mitigation to be implemented")
        else:
            oradio_log.error("Unhandled backlight error: '%s'", error.message)

    def _handle_i2c_error(self, error: ErrorMessage) -> None:
        """
        Handle I2C-related errors.

        Attempts recovery from known I2C conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == I2C_ERROR_BUS:
# NIET VERGETEN: implement I2C-recovery logic (e.g. back-off, retry)
            oradio_log.debug("I2C bus mitigation to be implemented")
        else:
            oradio_log.error("Unhandled I2C error: '%s'", error.message)

    def _handle_volume_error(self, error: ErrorMessage) -> None:
        """
        Handle volume-related errors.

        Attempts recovery from known volume conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == VOLUME_ERROR_START:
# NIET VERGETEN: implement volume-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Volume start mitigation to be implemented")
        elif error.message == VOLUME_ERROR_STOP:
# NIET VERGETEN: implement volume-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Volume stop mitigation to be implemented")
        else:
            oradio_log.error("Unhandled volume error: '%s'", error.message)

    def _handle_mpd_error(self, error: ErrorMessage) -> None:
        """
        Handle mpd-related errors.

        Attempts recovery from known MPD conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == MPD_ERROR_CONNECT:
# NIET VERGETEN: implement MPDService-recovery logic (e.g. back-off, retry)
            oradio_log.debug("MPD connect mitigation to be implemented")
        elif error.message == MPD_ERROR_EXECUTE:
# NIET VERGETEN: implement MPDMonitor-recovery logic (e.g. back-off, retry)
            oradio_log.debug("MPD execute mitigation to be implemented")
        elif error.message == MPD_ERROR_MONITOR:
# NIET VERGETEN: implement MPDMonitor-recovery logic (e.g. back-off, retry)
            oradio_log.debug("MPD monitor mitigation to be implemented")
        else:
            oradio_log.error("Unhandled MPD error: '%s'", error.message)

    def _handle_spotify_error(self, error: ErrorMessage) -> None:
        """
        Handle Spotify-related errors.

        Attempts recovery from known Spotify conditions and logs
        unrecognised errors for further investigation.

        Args:
            error: Error message received from the error bus.
        """
        if error.message == SPOTIFY_ERROR_MONITOR:
# NIET VERGETEN: implement Spotify-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Spotify monitor mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Spotify error: '%s'", error.message)

    def _handle_test_error(self, error: ErrorMessage) -> None:
        """
        Handle test errors published by the stand-alone self-test.

        Args:
            error: Error message received from the error bus.
        """
        oradio_log.debug("Mitigating test error: '%s'", error.message)

##### Core ################################################

    def _handle_message(self, message: ErrorMessage) -> None:
        """
        Dispatch incoming error to its source-specific handler.

        Args:
            message: The received message from the queue.
        """
        oradio_log.debug("Error message received: %r", message)
        handler = self._dispatch.get(message.source)
        if handler:
            handler(message)
        else:
            oradio_log.error(
                "Unhandled error from source: '%s': %s",
                message.source,
                message.message,
            )

    def stop(self) -> None:
        """
        Unsubscribe from error messages and call the base class to stop the worker thread.
        """
        # Remove from registry first — no new messages after this point.
        Errors.unsubscribe(self._queue)
        super().stop()

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from utilities import input_prompt
    from constants import YELLOW, NC                # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """
        Run an interactive self-test menu.

        Publishes test messages onto the error bus so that ErrorHandler
        behaviour can be verified.
        """

        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Publish TEST message\n"
            " 2-Publish UNEXPECTED message\n"
            "select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    # Publish a known test error; handler should accept it
                    print("\nPublish error message...")
                    Errors.publish(ErrorMessage(TEST_SOURCE, "error test message"))
                case 2:
                    # Publish an unrecognised error; handler should return False
                    print("\nPublish unexpected message...")
                    Errors.publish(ErrorMessage(UNEXPECTED, "Unexpected message"))
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to error topics so messages published are printed to console
    err_handler = ErrorHandler()

    # Present menu with tests
    interactive_menu()

    err_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
