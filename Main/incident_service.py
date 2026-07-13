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
    MessageHandlerTemplate,
    BACKLIGHTING_SOURCE, BACKLIGHTING_START_FAILED, BACKLIGHTING_STOPPED,
    GPIO_SOURCE, GPIO_PINS_FAILED, GPIO_BUTTONS_FAILED,
    I2C_SOURCE, I2C_BUS_FAILED, I2C_READ_FAILED, I2C_WRITE_FAILED,
    LED_SOURCE, LED_BLINK_START_FAILED, LED_BLINK_STOP_FAILED,
    MPD_SOURCE, MPD_CONNECT_FAILED, MPD_EXECUTE_FAILED, MPD_MONITOR_FAILED, MPD_PRESET_INVALID,
    LOG_SOURCE, LOG_START_FAILED, LOG_QUEUE_OVERFLOW, LOG_QUEUE_RECOVERED, LOG_LISTENER_DEAD, LOG_STOPPED,
    POWER_SOURCE, POWER_NEGOTIATION_FAILED,
    RMS_SOURCE, RMS_START_FAILED, RMS_POST_FAILED,
    SOUND_SOURCE, SOUND_MISSING_DIR, SOUND_PLAYBACK_FAILED,
    SPOTIFY_SOURCE, SPOTIFY_START_FAILED, SPOTIFY_STOPPED, SPOTIFY_MUTE_FAILED, SPOTIFY_UNMUTE_FAILED,
    THROTTLING_SOURCE, THROTTLING_START_FAILED, THROTTLING_THROTTLED, THROTTLING_STOPPED,
    USB_SOURCE, USB_FILE_FAILED, USB_START_FAILED, USB_STOPPED,
    VOLUME_SOURCE, VOLUME_START_FAILED, VOLUME_SET_FAILED, VOLUME_STOPPED,
    WEB_SOURCE, WEB_SERVER_FAILED, WEB_START_FAILED, WEB_STOP_FAILED,
    WIFI_SOURCE, WIFI_DBUS_FAILED, WIFI_NMCLI_FAILED, WIFI_CONNECT_FAILED, WIFI_DISCONNECT_FAILED,
)

##### LOCAL constants #####################################
# Source identifier used when publishing incidents from this module's self-tests
TEST_SOURCE = "Test message"

# Placeholder source name used to exercise the unrecognised-incident code path
UNEXPECTED = "Unexpected source"

class IncidentHandler(MessageHandlerTemplate):
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
            LED_SOURCE:          self._handle_led_incident,
            LOG_SOURCE:          self._handle_log_incident,
            MPD_SOURCE:          self._handle_mpd_incident,
            POWER_SOURCE:        self._handle_power_incident,
            RMS_SOURCE:          self._handle_rms_incident,
            SOUND_SOURCE:        self._handle_sound_incident,
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
        if incident.message == BACKLIGHTING_START_FAILED:
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
        if incident.message == GPIO_PINS_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report GPIO pins setup failed + status to RMS
            #   Can GPIO be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: call gpio_cleanup() and restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        if incident.message == GPIO_BUTTONS_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report GPIO buttons setup failed + status to RMS
            #   Can GPIO be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: call gpio_cleanup() and restart Oradio
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
        if incident.message == I2C_BUS_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report I2C bus access failed + status to RMS
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        if incident.message == I2C_READ_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report I2C read failed + status to RMS
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        if incident.message == I2C_WRITE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report I2C write failed + status to RMS
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled I2C incident: '%s'", incident.message)

    def _handle_led_incident(self, incident: IncidentMessage) -> None:
        """Handle LED-related incident."""
        if incident.message == LED_BLINK_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report LED worker start failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry the blink worker
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LED_BLINK_STOP_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report LED worker stop failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry the blink worker
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled LED incident: '%s'", incident.message)

    def _handle_log_incident(self, incident: IncidentMessage) -> None:
        """
        Handle log-related incident.

        Attempts recovery from known log conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == LOG_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report log monitor failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry reconnect
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_QUEUE_OVERFLOW:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report log records dropped + status to RMS
            #   Wait to give log service chance to recover
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_QUEUE_RECOVERED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report log service recovered + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_LISTENER_DEAD:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report broken logging service + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report log monitor stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting log monitor
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled MPD incident: '%s'", incident.message)

    def _handle_mpd_incident(self, incident: IncidentMessage) -> None:
        """
        Handle mpd-related incident.

        Attempts recovery from known MPD conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == MPD_CONNECT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report MPD connect failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry reconnect
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == MPD_EXECUTE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report MPD execute failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry execute
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == MPD_MONITOR_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report MPD monitor failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry start
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == MPD_PRESET_INVALID:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report broken preset mapping + status to RMS
            #   Notify web interface so the user can reassign the preset
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled MPD incident: '%s'", incident.message)

    def _handle_power_incident(self, incident: IncidentMessage) -> None:
        """Handle power-supply-related incident."""
        if incident.message == POWER_NEGOTIATION_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report PD negotiation failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry negotiation, else fall back to standby voltage
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled power supply incident: '%s'", incident.message)

    def _handle_rms_incident(self, incident: IncidentMessage) -> None:
        """
        Handle rms-related incident.

        Attempts recovery from known remote monitoring conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == RMS_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report RMS start failure + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        if incident.message == RMS_POST_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report RMS post failure + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Remote monitoring incident: '%s'", incident.message)

    def _handle_sound_incident(self, incident: IncidentMessage) -> None:
        """Handle system-sound-related incident."""
        if incident.message == SOUND_MISSING_DIR:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report system sound mdirectory missing + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SOUND_PLAYBACK_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report system sound playback failed + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled system sound incident: '%s'", incident.message)

    def _handle_spotify_incident(self, incident: IncidentMessage) -> None:
        """
        Handle Spotify-related incident.

        Attempts recovery from known Spotify conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == SPOTIFY_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report Spotify monitor start failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting Spotify monitor
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SPOTIFY_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report Spotify monitor stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting Spotify monitor
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SPOTIFY_MUTE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report mute amixer failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry mute
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SPOTIFY_UNMUTE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report unmute amixer failure + status to RMS
            #   If retry_count < MAX_RETRIES: retry unmute
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
        if incident.message == THROTTLING_THROTTLED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report RPi throttled to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == THROTTLING_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report throttling monitor start failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting throttling monitor
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
        if incident.message == USB_FILE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report usb file import failed + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report usb file import failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting usb service
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report usb service stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting usb service
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
        if incident.message == VOLUME_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report volume control start failed + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting volume control
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == VOLUME_SET_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report volume amixer control failure + status to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == VOLUME_STOPPED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report volume control stopped + status to RMS
            #   If retry_count < MAX_RETRIES: retry starting volume control
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
        if incident.message == WEB_SERVER_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report web service failed to start to RMS
            #   If retry_count < MAX_RETRIES: retry start
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report web server failed to start to RMS
            #   If retry_count < MAX_RETRIES: retry start
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_STOP_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report web server failed to stop to RMS
            #   If retry_count < MAX_RETRIES: retry stop
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
        if incident.message == WIFI_DBUS_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report wifi D-Bus failure to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_NMCLI_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report wifi nmcli failure to RMS
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_CONNECT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report wifi internet connection failure to RMS
# LET OP: wordt in wifi_service als command verstuurd en in oradio_control in state machine afgehandeld
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_DISCONNECT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Report wifi disconnect failure to RMS
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
