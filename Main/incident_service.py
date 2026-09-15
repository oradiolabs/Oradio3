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
from typing import Any
from time import monotonic

##### Oradio modules ######################################
from log_service import oradio_log
from rms_service import RMService, INCIDENT
from mpd_service import mpd_is_ready
from mpd_monitor import MPDMonitor
from usb_service import USBObserver
from backlight_service import Backlighting
from volume_control import VolumeControl
from log_monitor import LogHealthMonitor
from rpi_monitor import RPiThrottlingMonitor
from utilities import (
    restart_service,
    SERVICE_RESTART_LIMIT,
    SERVICE_RESTART_WINDOW,
)
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    MessageHandlerTemplate,
    BACKLIGHTING_SOURCE, BACKLIGHTING_START_FAILED, BACKLIGHTING_STOPPED,
    GPIO_SOURCE, GPIO_PINS_FAILED, GPIO_BUTTONS_FAILED,
    I2C_SOURCE, I2C_BUS_FAILED, I2C_READ_FAILED, I2C_WRITE_FAILED,
    LED_SOURCE, LED_BLINK_START_FAILED, LED_BLINK_STOP_FAILED,
    MPD_SOURCE, MPD_CONNECT_FAILED, MPD_EXECUTE_FAILED, MPD_MONITOR_FAILED, MPD_PRESET_INVALID,
    LOG_SOURCE, LOG_START_FAILED, LOG_QUEUE_OVERFLOW, LOG_QUEUE_RECOVERED, LOG_LISTENER_DEAD, LOG_STOPPED,
    RMS_SOURCE, RMS_START_FAILED, RMS_POST_FAILED,
    SOUND_SOURCE, SOUND_MISSING_DIR, SOUND_MISSING_FILE, SOUND_PLAYBACK_FAILED,
    THROTTLING_SOURCE, THROTTLING_START_FAILED, THROTTLING_THROTTLED, THROTTLING_STOPPED,
    USB_SOURCE, USB_FILE_FAILED, USB_FSCK_FAILED, USB_WIFI_DEFERRED_FAILED, USB_EVENT_FAILED, USB_START_FAILED, USB_STOPPED,
    VOLUME_SOURCE, VOLUME_START_FAILED, VOLUME_SET_FAILED, VOLUME_STOPPED,
    WEB_SOURCE, WEB_SERVER_FAILED, WEB_START_FAILED, WEB_STOP_FAILED,
    INCIDENT_SOURCE, INCIDENT_RECOVERED,
    WIFI_SOURCE, WIFI_DBUS_FAILED, WIFI_NMCLI_FAILED, WIFI_CONNECT_FAILED, WIFI_DISCONNECT_FAILED, WIFI_AP_FAILED,
)

##### LOCAL constants #####################################
# Source identifier used when publishing incidents from this module's self-tests
TEST_SOURCE = "Test message"

# Placeholder source name used to exercise the unrecognised-incident code path
UNEXPECTED = "Unexpected source"

# systemd unit the Oradio plays its music through, restarted below as the
# mitigation for an mpd that stopped answering.
#
# Local: nothing else names the unit. mpd_service.py reaches the same server
# over MPD_HOST:MPD_PORT and never has to know how it was started, which is
# what keeps this handler the only place that can restart it.
MPD_SERVICE = "mpd.service"

class IncidentHandler(MessageHandlerTemplate):
    """
    Handle Incident messages and perform incident-specific mitigation.

    Dispatches each message to a source-specific handler method;
    unrecognised sources are logged as errors.

    Every incident is reported to RMS by _handle_message() before the dispatch,
    so no handler below has to do that. What a handler adds is the action that
    follows the report -- which for a good many incidents is nothing at all.

    The notes in those handlers say which is which:

      NO MITIGATION             Reporting it IS the mitigation. There is
                                nothing the Oradio can do about it from here.

      OPEN QUESTION             The subsystem already tried to recover and
                                failed -- ThreadTemplate.restart_on_crash spent
                                its budget before this incident was raised.
                                Retrying here would be a second mechanism
                                fighting the first. What is undecided is what
                                the Oradio should DO about a subsystem that
                                stays down.

      MITIGATION TO BE          Genuinely unimplemented: a subsystem that does
      IMPLEMENTED               not restart itself, or a fault that needs a
                                different repair than a restart.
    """
    def __init__(self) -> None:
        """
        Subscribe to incident messages and call the base class constructor,
        which subscribes to the incident bus and starts the worker thread.
        """
        # Subscribe to incident messages and initialise base class and start the worker thread
        self._queue = Incidents.subscribe()

        # Used to post incidents to Remote Monitoring Service
        self._rms = RMService()

        # Map each source constant to its handler method.
        # Adding a new source only requires one new line here.
        self._dispatch: dict[str, Callable[[IncidentMessage], None]] = {
            BACKLIGHTING_SOURCE: self._handle_backlighting_incident,
            GPIO_SOURCE:         self._handle_gpio_incident,
            I2C_SOURCE:          self._handle_i2c_incident,
            LED_SOURCE:          self._handle_led_incident,
            LOG_SOURCE:          self._handle_log_incident,
            MPD_SOURCE:          self._handle_mpd_incident,
            RMS_SOURCE:          self._handle_rms_incident,
            SOUND_SOURCE:        self._handle_sound_incident,
            THROTTLING_SOURCE:   self._handle_throttling_incident,
            USB_SOURCE:          self._handle_usb_incident,
            VOLUME_SOURCE:       self._handle_volume_incident,
            WEB_SOURCE:          self._handle_web_incident,
            WIFI_SOURCE:         self._handle_wifi_incident,
            TEST_SOURCE:         self._handle_test_incident,
        }

        # Timestamps of service restarts this handler performed, per unit.
        # Only touched from the worker thread that runs _handle_message().
        self._service_restarts: dict[str, list[float]] = {}

        super().__init__(self._queue)

    def _restart_service_within_budget(self, service_name: str) -> bool:
        """
        Restart a system service, unless it has been restarted too often lately.

        Args:
            service_name: Unit to restart, e.g. "mpd.service".

        Returns:
            True when the service was restarted and is active again.

        Same shape as ThreadTemplate's crash budget, and for the same reason: a
        service that fails because of something a restart cannot fix will fail
        again the moment it comes back, and an unbounded loop turns one fault
        into a cycle of restarts with an incident on every turn.

        The budget decays, so a service that misbehaved this morning can still
        be recovered this afternoon.
        """
        if not self._within_restart_budget(service_name):
            return False

        return restart_service(service_name)

    def _within_restart_budget(self, name: str) -> bool:
        """
        Whether something may be restarted again, and record it if so.

        Args:
            name: What is being restarted, for the log line and the tally.

        Returns:
            True when the caller may go ahead.

        Shared by the two kinds of repair this handler does -- a systemd unit
        and a subsystem of its own -- because the reason for a budget is the
        same either way: a thing that fails for something a restart cannot fix
        will fail again the moment it comes back, and an unbounded loop turns
        one fault into a cycle of restarts with an incident on every turn.

        The budget decays, so something that misbehaved this morning can still
        be recovered this afternoon.
        """
        now = monotonic()
        history = [t for t in self._service_restarts.get(name, [])
                   if now - t < SERVICE_RESTART_WINDOW]

        if len(history) >= SERVICE_RESTART_LIMIT:
            oradio_log.error(
                "Not restarting %s again: %d restarts within %.0fs did not help",
                name, len(history), SERVICE_RESTART_WINDOW,
            )
            self._service_restarts[name] = history
            return False

        history.append(now)
        self._service_restarts[name] = history

        oradio_log.warning(
            "Restarting %s (%d of %d within %.0fs)",
            name, len(history), SERVICE_RESTART_LIMIT, SERVICE_RESTART_WINDOW,
        )
        return True

    def _restart_subsystem(self, name: str, factory: Callable[[], Any]) -> None:
        """
        Restart an Oradio subsystem that failed to start, and say so if it worked.

        Args:
            name:    Subsystem name, for the log line and the restart budget.
            factory: Returns the subsystem. Every caller passes a @singleton
                     class, so calling it hands back the live instance rather
                     than building a second one.

        The counterpart of _restart_service_within_budget() for the things the
        Oradio starts itself. A '<x> failed to start' incident means safe_start()
        returned False -- the worker never ran, so ThreadTemplate's own
        restart_on_crash never saw it and nothing has tried again.

        stop() first, because a start that failed can leave a thread object
        behind that start() would refuse to replace.

        Deliberately silent towards oradio_control, unlike the mpd.service path.
        Restarting mpd discards the playback queue, so the state machine is left
        describing music that is no longer playing and has to be told. None of
        the subsystems here does that: the state machine tracks neither the
        backlight nor the volume worker nor a monitor, and the music keeps
        playing throughout. Sending it to Idle would stop the music for a fault
        the listener never noticed -- the opposite of what a repair is for.

        A subsystem that comes back invisibly should stay invisible, exactly as
        ThreadTemplate's own restart_on_crash does. The incident is already on
        its way to RMS; that is the record.
        """
        if not self._within_restart_budget(name):
            return

        try:
            subsystem = factory()
            subsystem.stop()
            subsystem.start()
        # Broad catch: these are other modules' start paths, reached at the
        # worst possible moment, and an exception here would kill the handler
        # thread and with it every mitigation after this one.
        except Exception as ex_err:      # pylint: disable=broad-exception-caught
            oradio_log.error("Restarting %s failed: %s", name, ex_err)
            return

        oradio_log.info("%s restarted", name)

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
            # MITIGATION: start it again.
            #
            # safe_start() returned False, so the worker never ran and
            # ThreadTemplate's restart_on_crash never saw it. Nothing has
            # tried again; this is the first attempt.
            self._restart_subsystem("backlighting", Backlighting)
        elif incident.message == BACKLIGHTING_STOPPED:
            # OPEN QUESTION, not a retry:
            #   Do NOT retry the worker here; see BACKLIGHTING_START_FAILED above.
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
            #   Can GPIO be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: call gpio_cleanup() and restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == GPIO_BUTTONS_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
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
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == I2C_READ_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == I2C_WRITE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Can I2C be reset? IF yes add and try, if not power cycle
            #   If retry_count < MAX_RETRIES: restart Oradio
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled I2C incident: '%s'", incident.message)

    def _handle_led_incident(self, incident: IncidentMessage) -> None:
        """
        Handle LED-related incident.

        Attempts recovery from known LED conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == LED_BLINK_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   If retry_count < MAX_RETRIES: retry the blink worker
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LED_BLINK_STOP_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
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
            # MITIGATION: start it again.
            #
            # safe_start() returned False, so the worker never ran and
            # ThreadTemplate's restart_on_crash never saw it. Nothing has
            # tried again; this is the first attempt.
            self._restart_subsystem("log health monitor", LogHealthMonitor)
        elif incident.message == LOG_QUEUE_OVERFLOW:
            # MITIGATION TO BE IMPLEMENTED:
            #   Wait to give log service chance to recover
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_QUEUE_RECOVERED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   The log service recovered on its own. Reporting it is the point:
            #   it closes the LOG_QUEUE_OVERFLOW that preceded it.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_LISTENER_DEAD:
            # MITIGATION TO BE IMPLEMENTED:
            #   Nothing beyond the report _handle_message already sends.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == LOG_STOPPED:
            # OPEN QUESTION, not a retry:
            #   Do NOT retry the worker here; see LOG_START_FAILED above.
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled log incident: '%s'", incident.message)

    def _handle_mpd_incident(self, incident: IncidentMessage) -> None:
        """
        Handle mpd-related incident.

        Attempts recovery from known MPD conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == MPD_MONITOR_FAILED:
            # MITIGATION: start the monitor again.
            #
            # Not a restart of mpd: this is published when MPDMonitor's own
            # worker fails to start, which says nothing about whether the server
            # is answering. safe_start() returned False, so the worker never ran
            # and ThreadTemplate's restart_on_crash never saw it.
            self._restart_subsystem("MPD monitor", MPDMonitor)
        elif incident.message in (MPD_CONNECT_FAILED, MPD_EXECUTE_FAILED):
            # MITIGATION: restart mpd.service.
            #
            # Both say the same thing by the time they get here: mpd is not
            # answering. MPDService has its own retries and a circuit breaker in
            # front of them, so these are published only once that gave up --
            # reconnecting again here would repeat work that has already failed.
            #
            # What has not been tried is restarting the server itself, and a
            # wedged mpd is exactly what that fixes. The cost is a few seconds of
            # silence; the database is on disk and MPDControl reconnects on its
            # next command.
            #
            # Not for MPD_PRESET_INVALID below: that one is about what is on the
            # USB drive, and restarting mpd will not change it.
            #
            # Checked first, because an incident can arrive after the fault it
            # describes is over. MPDService's retry burst takes seconds, so a
            # burst that began before an earlier restart can open the circuit
            # breaker after that restart already fixed things -- and the budget
            # would be spent restarting a server that is answering.
            if mpd_is_ready():
                oradio_log.info("mpd is answering again; no restart needed")
            elif self._restart_service_within_budget(MPD_SERVICE):
                # Bringing mpd back is only half of it. Whatever the Oradio was
                # playing is gone with the old process, and the state machine
                # still believes it is playing: the LED is on, the state says
                # StatePresetN, and mpd has an empty queue. Pressing the same
                # preset again does not help, because the state machine reads a
                # repeat as "next song".
                #
                # Telling oradio_control rather than reaching into it: the state
                # machine owns its own state, and it is the only place that
                # knows what StatePresetN should mean now.
                #
                # One source and one message for every repair that DOES leave
                # the Oradio believing something stale, so oradio_control needs
                # a single handler rather than one per subsystem. The subsystem
                # restarts elsewhere in this file stay silent on purpose: they
                # do not invalidate anything the state machine tracks.
                Commands.publish(CommandMessage(INCIDENT_SOURCE, INCIDENT_RECOVERED))
        elif incident.message == MPD_PRESET_INVALID:
            # MITIGATION TO BE IMPLEMENTED:
            #   Notify web interface so the user can reassign the preset
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
        if incident.message == RMS_START_FAILED:
            # OPEN QUESTION, not a retry:
            #   what cannot be done, since the sender that would post it is the
            #   thing that failed. _RmsSender opts into restart_on_crash, so by
            #   the time this arrives its budget is spent and the Oradio has no
            #   way left to tell anyone. Only the log file carries it.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == RMS_POST_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Nothing to repair and nothing that can be sent: the sender that
            #   would carry it is the one that failed. The log file keeps it, and
            #   the next successful POST is the recovery.
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled Remote monitoring incident: '%s'", incident.message)

    def _handle_sound_incident(self, incident: IncidentMessage) -> None:
        """
        Handle system-sound-related incident.

        Attempts recovery from known system sound conditions and logs
        unrecognised incidents for further investigation.

        Args:
            incident: Incident message received from the incident bus.
        """
        if incident.message == SOUND_MISSING_FILE:
            # NO MITIGATION: reporting it IS the mitigation.
            #   A sound file that is not on disk is a broken installation, the
            #   same as SOUND_MISSING_DIR below. Reinstalling is the fix and
            #   only a person can do that.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SOUND_MISSING_DIR:
            # NO MITIGATION: reporting it IS the mitigation.
            #   A missing sound directory is a broken installation, not a runtime
            #   fault. Reinstalling is the fix and only a person can do that.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == SOUND_PLAYBACK_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Replaying is pointless: the prompt belonged to a moment that has
            #   passed, and a second attempt on a broken audio path fails the
            #   same way. system_sounds raises this once per outage and clears
            #   it on the first sound that plays, so the pair of events is the
            #   whole story -- with aplay's own words in the log line.
            oradio_log.debug("Mitigation to be implemented")
        else:
            oradio_log.error("Unhandled system sound incident: '%s'", incident.message)

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
            #   Nothing beyond the report _handle_message already sends.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == THROTTLING_START_FAILED:
            # MITIGATION: start it again.
            #
            # safe_start() returned False, so the worker never ran and
            # ThreadTemplate's restart_on_crash never saw it. Nothing has
            # tried again; this is the first attempt.
            self._restart_subsystem("throttling monitor", RPiThrottlingMonitor)
        elif incident.message == THROTTLING_STOPPED:
            # OPEN QUESTION, not a retry:
            #   Do NOT retry the worker here; see THROTTLING_START_FAILED above.
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
        if incident.message == USB_EVENT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   The observer survived, but this insert or remove was not acted
            #   on: the Oradio still believes whatever drive state it had
            #   before. Re-reading USBService.get_state() and republishing it
            #   would resynchronise without waiting for the user to pull the
            #   drive and put it back.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_FSCK_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   fsck has already had its turn, and anything further needs the
            #   drive in a PC. The value is knowing: a drive reaching this state
            #   tends to do it again.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_WIFI_DEFERRED_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   The file is valid and still on the drive; NetworkManager simply
            #   never became available. Re-inserting the drive retries the
            #   import.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == USB_FILE_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Nothing beyond the report _handle_message already sends.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message in (USB_START_FAILED, USB_STOPPED):
            # MITIGATION: start the observer again.
            #
            # START_FAILED is a safe_start() that returned False; STOPPED is the
            # health check finding the watchdog observer thread gone. Neither is
            # a crash inside a ThreadTemplate worker -- USBService does not use
            # one -- so nothing else has tried.
            #
            # Worth trying: without the observer the Oradio never notices a
            # drive being inserted or removed again, which looks to the user
            # like a stick that simply does not work.
            #
            # STILL TO DO: an observer that was gone for a while may have missed
            # a mount or unmount, so usb_present can be wrong after this. The
            # repair for that is republishing USBService.get_state(), not the
            # INCIDENT_RECOVERED the mpd path sends -- see USB_EVENT_FAILED
            # above, which has the same problem from a different direction.
            self._restart_subsystem("USB observer", USBObserver)
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
            # MITIGATION: start it again.
            #
            # safe_start() returned False, so the worker never ran and
            # ThreadTemplate's restart_on_crash never saw it. Nothing has
            # tried again; this is the first attempt.
            self._restart_subsystem("volume control", VolumeControl)
        elif incident.message == VOLUME_SET_FAILED:
            # OPEN QUESTION, not a retry:
            #   amixer could not set a softvol control, which usually means the
            #   controls are not there -- alsactl restore failed at boot. See
            #   the note at that ExecStartPre= in oradio.service: this incident
            #   is deliberately the one place a broken audio path is reported,
            #   raised by the component that discovered it.
            #
            #   Retrying will not create a control that does not exist. What is
            #   undecided is what the Oradio should do with a volume knob that
            #   cannot move.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == VOLUME_STOPPED:
            # OPEN QUESTION, not a retry:
            #   Do NOT retry the worker here; see VOLUME_START_FAILED above.
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
            #   If retry_count < MAX_RETRIES: retry start
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_START_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   If retry_count < MAX_RETRIES: retry start
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WEB_STOP_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
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
        if incident.message == WIFI_AP_FAILED:
            # OPEN QUESTION, not a retry:
            #   The user long-pressed and no network appeared on their phone.
            #   WebService.start() already gave up, so retrying the access
            #   point here would race whatever it does next. What is undecided
            #   is whether the Oradio should say something -- this is the one
            #   failure the user is standing in front of, waiting for.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_DBUS_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   NetworkManager never appeared. WifiService already waited for it
            #   and gave up; there is nothing here that could make it arrive.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_NMCLI_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Nothing beyond the report _handle_message already sends.
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_CONNECT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Nothing beyond the report _handle_message already sends.
# REVIEW Onno:
#   WIFI_CONNECT_FAILED wordt als incident gerapporteerd, hier nu als command doorgestuurd.
#   Te kiezen: is het een command of een incident?
            Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_CONNECT_FAILED))
            oradio_log.debug("Mitigation to be implemented")
        elif incident.message == WIFI_DISCONNECT_FAILED:
            # MITIGATION TO BE IMPLEMENTED:
            #   Nothing beyond the report _handle_message already sends.
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
        # Warning, not debug: an incident is by definition something that went
        # wrong, and this is the only line in the log that names it. At debug it
        # disappears the moment the level is raised, taking with it the context
        # for every "Mitigation to be implemented" that follows.
        #
        # Source and message, not the whole message object: repr() of an
        # IncidentMessage includes the captured stack, which turns one entry
        # into a paragraph and makes the log unreadable exactly when someone is
        # trying to read it.
        oradio_log.warning("Incident from '%s': %s", message.source, message.message)

        # The stack stays at debug, on its own line, so it can be found when it
        # is wanted and skipped when it is not.
        if message.details:
            oradio_log.debug("Incident details: %s", message.details)

        # Post incident (if connected to internet)
        self._rms.send_message(INCIDENT, message)

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
    from utilities import input_prompt      # pylint: disable=ungrouped-imports
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
                    # Publish an unrecognised incident; handler should log an error
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
