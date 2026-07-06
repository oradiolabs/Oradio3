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
    Top-level incident handling service.

    Subscribes to the incident bus and applies mitigation
    for recognised incidents from registered sources.
    Unknown incidents are logged for further investigation.
"""
from collections.abc import Callable

##### Oradio modules ######################################
from log_service import oradio_log
from messaging import (
    Incidents,
    IncidentMessage,
    MessageHandlerBase,

    BACKLIGHTING_SOURCE,
    BACKLIGHTING_FAILED,
    BACKLIGHTING_STOPPED,

    GPIO_SOURCE,
    GPIO_INCIDENT_SERVICE,
    GPIO_INCIDENT_BUTTONS,

    I2C_SOURCE,
    I2C_INCIDENT_BUS,

    MPD_SOURCE,
    MPD_INCIDENT_CONNECT,
    MPD_INCIDENT_EXECUTE,
    MPD_INCIDENT_MONITOR,

    RMS_SOURCE,
    RMS_INCIDENT_SERVICE,

    SPOTIFY_SOURCE,
    SPOTIFY_INCIDENT_MONITOR,

    THROTTLING_SOURCE,
    THROTTLING_FAILED,
    THROTTLING_THROTTLED,
    THROTTLING_STOPPED,

    USB_SOURCE,
    USB_INCIDENT_FILE,
    USB_INCIDENT_SERVICE,

    VOLUME_SOURCE,
    VOLUME_INCIDENT_START,
    VOLUME_INCIDENT_STOP,

    WEB_SOURCE,
    WEB_INCIDENT_START,
    WEB_INCIDENT_STOP,
    WEB_INCIDENT_SERVICE,

    WIFI_SOURCE,
    WIFI_INCIDENT_DBUS,
    WIFI_INCIDENT_NMCLI,
    WIFI_INCIDENT_CONNECT,
    WIFI_INCIDENT_DISCONNECT,
)

##### LOCAL constants #####################################
# Source identifier used when publishing incidents from this module's self-tests
TEST_SOURCE = "Test message"

# Placeholder source name used to exercise the unrecognised-incident code path
UNEXPECTED = "Unexpected source"

class IncidentHandler(MessageHandlerBase):
    """
    Handle Incident messages and perform incident-specific mitigation.

    Dispatches each message to a source-specific handler method;
    unrecognised sources are logged as errors.
    """
    def __init__(self) -> None:
        """
        Subscribe to incident messages and call the base class constructor,
        which subscribes to the incident bus and starts the worker thread.
        """
        # Subscribe to incident messages and initialise base class and start the worker thread
        self._queue = Incidents.subscribe()

        # Map each source constant to its handler method.
        # Adding a new source only requires one new line here.
        self._dispatch: dict[str, Callable[[IncidentMessage], None]] = {
            BACKLIGHTING_SOURCE: self._handle_backlighting_incident,
            GPIO_SOURCE:         self._handle_gpio_incident,
            I2C_SOURCE:          self._handle_i2c_incident,
            MPD_SOURCE:          self._handle_mpd_incident,
            RMS_SOURCE:          self._handle_rms_incident,
            SPOTIFY_SOURCE:      self._handle_spotify_incident,
            THROTTLING_SOURCE:   self._handle_throttling_incident,
            USB_SOURCE:          self._handle_usb_incident,
            VOLUME_SOURCE:       self._handle_volume_incident,
            WEB_SOURCE:          self._handle_web_incident,
            WIFI_SOURCE:         self._handle_wifi_incident,
            TEST_SOURCE:         self._handle_test_incident,
        }

        super().__init__(self._queue)

##### Helpers #############################################

    def _handle_backlighting_incident(self, incident: IncidentMessage) -> None:
        """
        Handle backlight-related incident.

        Attempts recovery from known backlight incidents and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == BACKLIGHTING_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report backlighting start failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting Backlighting
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == BACKLIGHTING_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report backlighting stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting backlighting
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled backlighting incident: '%s'", incident.message)

    def _handle_gpio_incident(self, incident: IncidentMessage) -> None:
        """
        Handle gpio-related incident.

        Attempts recovery from known GPIO conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == GPIO_INCIDENT_SERVICE:
# NIET VERGETEN: implement gpio-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == GPIO_INCIDENT_BUTTONS:
# NIET VERGETEN: implement gpio-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled GPIO incident: '%s'", incident.message)

    def _handle_i2c_incident(self, incident: IncidentMessage) -> None:
        """
        Handle I2C-related incident.

        Attempts recovery from known I2C conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == I2C_INCIDENT_BUS:
# NIET VERGETEN: implement I2C-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled I2C incident: '%s'", incident.message)

    def _handle_mpd_incident(self, incident: IncidentMessage) -> None:
        """
        Handle mpd-related incident.

        Attempts recovery from known MPD conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == MPD_INCIDENT_CONNECT:
# NIET VERGETEN: implement MPDService-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == MPD_INCIDENT_EXECUTE:
# NIET VERGETEN: implement MPDMonitor-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == MPD_INCIDENT_MONITOR:
# NIET VERGETEN: implement MPDMonitor-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled MPD incident: '%s'", incident.message)

    def _handle_rms_incident(self, incident: IncidentMessage) -> None:
        """
        Handle rms-related incident.

        Attempts recovery from known remote monitoring conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == RMS_INCIDENT_SERVICE:
# NIET VERGETEN: implement rms-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Remote monitoring incident: '%s'", incident.message)

    def _handle_spotify_incident(self, incident: IncidentMessage) -> None:
        """
        Handle Spotify-related incident.

        Attempts recovery from known Spotify conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == SPOTIFY_INCIDENT_MONITOR:
# NIET VERGETEN: implement Spotify-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Spotify incident: '%s'", incident.message)

    def _handle_throttling_incident(self, incident: IncidentMessage) -> None:
        """
        Handle throttling-related incident.

        Attempts recovery from known throttling incidents and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == THROTTLING_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report throttling monitor start failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting throttling monitor
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == THROTTLING_THROTTLED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report RPi throttled to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == THROTTLING_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report throttling monitor stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting throttling monitor
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled throttling incident: '%s'", incident.message)

    def _handle_usb_incident(self, incident: IncidentMessage) -> None:
        """
        Handle USB-related incident.

        Attempts recovery from known USB failures and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == USB_INCIDENT_FILE:
# NIET VERGETEN: implement file-level USB error recovery (e.g. re-mount, rescan)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_INCIDENT_SERVICE:
# NIET VERGETEN: implement USB service recovery (e.g. restart udev / service)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled USB incident: '%s'", incident.message)

    def _handle_volume_incident(self, incident: IncidentMessage) -> None:
        """
        Handle volume-related incident.

        Attempts recovery from known volume conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == VOLUME_INCIDENT_START:
# NIET VERGETEN: implement volume-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == VOLUME_INCIDENT_STOP:
# NIET VERGETEN: implement volume-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled volume incident: '%s'", incident.message)

    def _handle_web_incident(self, incident: IncidentMessage) -> None:
        """
        Handle web-related incident.

        Attempts recovery from known web service/server conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == WEB_INCIDENT_SERVICE:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_INCIDENT_START:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_INCIDENT_STOP:
# NIET VERGETEN: implement web-recovery logic (e.g. back-off, retry)
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled web incident: '%s'", incident.message)

    def _handle_wifi_incident(self, incident: IncidentMessage) -> None:
        """
        Handle WiFi-related incident.

        Attempts recovery from known WiFi failures and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == WIFI_INCIDENT_DBUS:
# NIET VERGETEN: implement D-Bus event handler error recovery
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_INCIDENT_NMCLI:
# NIET VERGETEN: implement failed to interact with NetworkManager error recovery
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_INCIDENT_CONNECT:
# NIET VERGETEN: implement wifi connect failed recovery
# LET OP: wordt in wifi_service als command verstuurd en in oradio_control in state machine afgehandeld
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_INCIDENT_DISCONNECT:
# NIET VERGETEN: implement wifi disconnect failed recovery
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled wifi incident: '%s'", incident.message)

    def _handle_test_incident(self, incident: IncidentMessage) -> None:
        """
        Handle test incident published by the stand-alone self-test.

        Args:
            incident: Incident message received from the incident bus.
        """
        oradio_log.debug("Mitigating test incident: '%s'", incident.message)

##### Core ################################################

    def _handle_message(self, message: IncidentMessage) -> None:
        """
        Dispatch incoming incident to its source-specific handler.

        Args:
            message: The received message from the queue.
        """
        oradio_log.debug("Incident message received: %r", message)
        handler = self._dispatch.get(message.source)
        if handler:
            handler(message)
        else:
            oradio_log.error(
                "Unhandled incident from source: '%s': %s",
                message.source,
                message.message,
            )

    def stop(self) -> None:
        """
        Unsubscribe from Incident messages and call the base class to stop the worker thread.
        """
        # Remove from registry first — no new messages after this point.
        Incidents.unsubscribe(self._queue)
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

        Publishes test messages onto the incident bus so that IncidentHandler
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
                    # Publish a known test incident; handler should accept it
                    print("\nPublish Incident message...")
                    Incidents.publish(IncidentMessage(TEST_SOURCE, "Test incident"))
                case 2:
                    # Publish an unrecognised incident; handler should return False
                    print("\nPublish unexpected message...")
                    Incidents.publish(IncidentMessage(UNEXPECTED, "Unexpected incident"))
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to incident topics so messages published are printed to console
    incident_handler = IncidentHandler()

    # Present menu with tests
    interactive_menu()

    incident_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
