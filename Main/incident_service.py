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
from log_service import oradio_log, INFO
from rms_service import RMService, INCIDENT
from mpd_service import mpd_is_ready
from mpd_monitor import MPDMonitor
from usb_service import USBObserver, republish_usb_state
from web_service import WebService
from backlight_service import Backlighting
from volume_control import VolumeControl
from log_monitor import LogHealthMonitor
from rpi_monitor import RPiThrottlingMonitor
from utilities import (
    fatal_exit,
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
    MPD_SOURCE, MPD_CONNECT_FAILED, MPD_EXECUTE_FAILED, MPD_MONITOR_FAILED,
    LOG_SOURCE, LOG_START_FAILED, LOG_QUEUE_OVERFLOW, LOG_QUEUE_RECOVERED, LOG_LISTENER_DEAD, LOG_STOPPED,
    RMS_SOURCE, RMS_START_FAILED, RMS_POST_FAILED,
    SOUND_SOURCE, SOUND_MISSING_DIR, SOUND_MISSING_FILE, SOUND_PLAYBACK_FAILED,
    THROTTLING_SOURCE, THROTTLING_START_FAILED, THROTTLING_THROTTLED, THROTTLING_STOPPED,
    POWER_UNDERVOLTAGE,
    USB_SOURCE, USB_FILE_FAILED, USB_FSCK_FAILED, USB_WIFI_DEFERRED_FAILED, USB_EVENT_FAILED, USB_START_FAILED, USB_STOPPED,
    VOLUME_SOURCE, VOLUME_START_FAILED, VOLUME_SET_FAILED, VOLUME_STOPPED,
    WEB_SOURCE, WEB_SERVER_FAILED, WEB_START_FAILED, WEB_STOP_FAILED,
    INCIDENT_SOURCE, INCIDENT_RECOVERED, INCIDENT_POWER_ERROR,
    WIFI_SOURCE, WIFI_DBUS_FAILED, WIFI_NMCLI_FAILED, WIFI_DISCONNECT_FAILED, WIFI_AP_FAILED,
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

# systemd unit that owns the radio. Restarted below when it cannot be reached
# over D-Bus at all.
#
# Local for the same reason as MPD_SERVICE: wifi_service and wifi_listener reach
# NetworkManager through nmcli and D-Bus and never have to know how it was
# started, which keeps this handler the only place that can restart it.
NM_SERVICE = "NetworkManager.service"

class IncidentHandler(MessageHandlerTemplate):
    """
    Receive incidents and do whatever can still be done about them.

    Each incident is dispatched to a handler for its source; an unrecognised
    source is logged as an error. _handle_message() reports every incident to
    RMS before that dispatch, so no handler has to. What a handler adds is
    whatever should happen next -- and for most incidents that is nothing.

    Every branch below opens with one of two labels:

      MITIGATION                Something is done: a service or subsystem is
                                restarted, state is republished, or the process
                                is ended so systemd and the crash handler can
                                take over.

      NO MITIGATION             Reporting it IS the mitigation, for one of three
                                reasons. The fault was already retried where it
                                belongs, so trying again here would be a second
                                mechanism fighting the first. Or nothing the
                                Oradio can do would change it -- a file the user
                                wrote, a supply that cannot deliver. Or the
                                subsystem that raised it deals with it itself,
                                which is the better place when the decision
                                needs context this handler does not have:
                                WebService counts failed portal starts because
                                only it knows what a long press was meant to do,
                                and volume_control counts failed amixer calls
                                because only it knows that one knob turn is a
                                dozen of them.

    A new branch gets one of those two. There is no "to be implemented" left,
    and adding one back would be hiding a decision rather than recording it.

    A note on the *_STOPPED incidents, which are all NO MITIGATION for the same
    reason: nothing in the operational Oradio calls stop() on those workers, and
    none of their do_work() bodies has a path that raises -- an I2C failure
    comes back as None, not as an exception. So a *_STOPPED means an exception
    nobody foresaw, repeated until ThreadTemplate.restart_on_crash gave up. A
    further restart from here would meet the same one.

    Their value is what they carry: ThreadTemplate.stop_reason(), the exception
    that ended the last attempt and how often it recurred. The note on each of
    those branches says only what is lost while that worker stays down.
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
            # NO MITIGATION: see the class docstring on *_STOPPED.
            #   The backlight holds its last brightness. The Oradio plays on and every
            #   button works.
            pass
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
        if incident.message in (GPIO_PINS_FAILED, GPIO_BUTTONS_FAILED):
            # MITIGATION: end the process and let tier three repair it.
            #
            # GPIO carries both the buttons and the LEDs, so without it the user
            # can neither tell the Oradio anything nor see what it is doing. The
            # music may still be playing, but nothing can change it: this is as
            # unusable as the Oradio gets while still running.
            #
            # Nothing in this process can fix that. GPIO.setup() has already
            # failed, and there is no OS-level reset below RPi.GPIO -- it writes
            # the pin registers through /dev/gpiomem, and gpio_cleanup() is the
            # whole of what can be undone from here.
            #
            # Exiting non-zero hands it to the recovery that does have stronger
            # remedies: systemd restarts the service, which runs GPIO.setup()
            # from scratch, and if that fails too the crash handler reboots --
            # a power-on reset of the pinctrl blocks, which is the strongest
            # reset there is. A third failure plays the service message and
            # leaves the STOP LED blinking.
            #
            # No budget here: systemd's StartLimitBurst is the budget, and
            # counting again in a process that is about to end would not
            # survive to count a second time.
            fatal_exit(
                f"GPIO failure leaves the Oradio unusable: {incident.message}",
                stacklevel=4,
            )
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
            # MITIGATION: end the process and let tier three repair it.
            #
            # The bus is gone, not one device on it. Four chips hang off it --
            # the power-delivery controller, the backlight DAC, the light sensor
            # and the volume ADC -- so the volume knob is dead, the backlight is
            # stuck and the Oradio cannot read its own supply. What is left is
            # music nobody can adjust.
            #
            # Nothing here can bring it back: I2CService has already found
            # /dev/i2c-1 missing or unusable, and there is no userspace reset
            # for the bcm2835 controller. Unbinding and rebinding its driver
            # through sysfs exists, but it is the weaker remedy -- the reboot
            # below is a power-on reset of the controller, which is as complete
            # as a reset gets.
            #
            # So the same escalation as GPIO: systemd restarts the service,
            # which reopens the bus from scratch; if that fails the crash
            # handler reboots; a third failure plays the service message and
            # leaves the STOP LED blinking.
            fatal_exit(
                f"I2C bus failure leaves the Oradio unusable: {incident.message}",
                stacklevel=4,
            )
        elif incident.message == I2C_READ_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   One device did not answer a read, not the bus: I2CService has
            #   already retried with backoff, and the three other chips are
            #   still reachable. The Oradio keeps playing and the buttons keep
            #   working -- what is lost is one function, which is not worth
            #   ending the process for.
            #
            #   The device address travels with the incident, so RMS shows
            #   which chip it was. If the field data ever shows one failing
            #   often enough to matter, that is the evidence to act on.
            pass
        elif incident.message == I2C_WRITE_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   One device did not answer a write, not the bus: I2CService has
            #   already retried with backoff, and the three other chips are
            #   still reachable. The Oradio keeps playing and the buttons keep
            #   working -- what is lost is one function, which is not worth
            #   ending the process for.
            #
            #   The device address travels with the incident, so RMS shows
            #   which chip it was. If the field data ever shows one failing
            #   often enough to matter, that is the evidence to act on.
            pass
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
            # NO MITIGATION: reporting it IS the mitigation.
            #   The blink worker lives for one blink request. Restarting it would revive
            #   a worker nobody is waiting for; the next control_blinking_led() builds a
            #   new one regardless.
            pass
        elif incident.message == LED_BLINK_STOP_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Same as LED_BLINK_START_FAILED above: a per-request worker, with nothing
            #   worth reviving.
            pass
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
            # NO MITIGATION: reporting it IS the mitigation.
            #   The log service drains the queue again once the burst is over, and says
            #   so with LOG_QUEUE_RECOVERED. A listener that will never drain it is a
            #   different incident -- LOG_LISTENER_DEAD -- and that one is repaired.
            pass
        elif incident.message == LOG_QUEUE_RECOVERED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   The log service recovered on its own. Reporting it is the point:
            #   it closes the LOG_QUEUE_OVERFLOW that preceded it.
            pass
        elif incident.message == LOG_LISTENER_DEAD:
            # MITIGATION: start the queue listener again.
            #
            # A dead listener is silent rather than noisy: records keep going
            # into the queue and nothing takes them out, until it is full and
            # every later record is dropped. QueueListener does not supervise
            # its own thread, so nothing recovers from this on its own.
            #
            # Worth repairing even though the user never notices: from here on
            # the Oradio has no record of what went wrong next, which is exactly
            # what the next incident will be read with.
            #
            # No INCIDENT_RECOVERED: the state machine tracks nothing about
            # logging. LOG_QUEUE_RECOVERED arrives by itself once the listener
            # drains the backlog, and that is the confirmation this worked.
            # Every line below goes through health_notice(), which writes
            # straight to the fallback sinks. Ordinary logging cannot report
            # this: the queue is full because nothing is draining it, so a
            # record saying so is dropped like every other -- the explanation
            # would disappear into the problem it describes.
            if self._within_restart_budget("log queue listener"):
                if oradio_log.restart_listener():
                    oradio_log.health_notice("log queue listener restarted", INFO)
                else:
                    oradio_log.health_notice("log queue listener could not be restarted")
            else:
                # The budget message from _within_restart_budget() was dropped
                # with the rest. Said again here, where it can be read.
                oradio_log.health_notice(
                    "giving up on the log queue listener; nothing will be logged "
                    "from here on and no further notice will follow"
                )

        elif incident.message == LOG_STOPPED:
            # NO MITIGATION: see the class docstring on *_STOPPED.
            #   What stops is the watching. LOG_QUEUE_OVERFLOW and LOG_LISTENER_DEAD can
            #   no longer be raised, so a logging failure after this one passes
            #   unnoticed.
            pass
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
            # MITIGATION: start the RMS service again.
            #
            # The construction of the sender or its worker thread failed, so
            # nothing this Oradio has to say can leave it. That is worth
            # repairing precisely because the user will never notice: the music
            # plays, the buttons work, and the only thing missing is the one
            # channel through which a fault could be seen from anywhere else.
            #
            # The incident itself does not reach RMS: _handle_message() tried to
            # send it through the service that just failed to start. The log
            # file keeps it, and the next crash upload carries it along.
            #
            # Restart the subsystem as this one leaves an Oradio that works but
            # cannot report, which blocks remote incident monitoring.
            self._restart_subsystem("RMS service", RMService)
        elif incident.message == RMS_POST_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Nothing to repair and nothing that can be sent: the sender that
            #   would carry it is the one that failed. The log file keeps it, and
            #   the next successful POST is the recovery.
            pass
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
            pass
        elif incident.message == SOUND_MISSING_DIR:
            # NO MITIGATION: reporting it IS the mitigation.
            #   A missing sound directory is a broken installation, not a runtime
            #   fault. Reinstalling is the fix and only a person can do that.
            pass
        elif incident.message == SOUND_PLAYBACK_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Replaying is pointless: the prompt belonged to a moment that has
            #   passed, and a second attempt on a broken audio path fails the
            #   same way. system_sounds raises this once per outage and clears
            #   it on the first sound that plays, so the pair of events is the
            #   whole story -- with aplay's own words in the log line.
            pass
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
        if incident.message == POWER_UNDERVOLTAGE:
            # MITIGATION: stop, say so, and stay stopped.
            #
            # Nothing here can raise the supply voltage, and continuing to draw
            # current while it is too low is what risks corrupting the SD card.
            #
            # The same answer as an unsupported PD contract at start-up, because
            # it is the same fault and the same remedy: the supply has to be
            # replaced, and replacing it cuts the power anyway -- so playing on
            # until the user acts buys nothing and costs every second of it.
            Commands.publish(CommandMessage(INCIDENT_SOURCE, INCIDENT_POWER_ERROR))
        elif incident.message == THROTTLING_THROTTLED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   The Pi is protecting itself against heat or load and recovers on its
            #   own. Under-voltage is raised separately as POWER_UNDERVOLTAGE, because
            #   that one does not pass, and is not the Pi protecting itself but the
            #   supply failing to deliver. The decoded flags travel with the incident,
            #   so RMS learns which protection kicked in.
            pass
        elif incident.message == THROTTLING_START_FAILED:
            # MITIGATION: start it again.
            #
            # safe_start() returned False, so the worker never ran and
            # ThreadTemplate's restart_on_crash never saw it. Nothing has
            # tried again; this is the first attempt.
            self._restart_subsystem("throttling monitor", RPiThrottlingMonitor)
        elif incident.message == THROTTLING_STOPPED:
            # NO MITIGATION: see the class docstring on *_STOPPED.
            #   Nothing user-visible is lost; what stops is the watching. A later under-
            #   voltage or over-temperature goes unreported.
            pass
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
            # MITIGATION: republish the drive state.
            #
            # The observer survived, but this insert or remove was not acted on,
            # so the Oradio still believes whatever it believed before. Reading
            # the mount point and publishing that resynchronises the two without
            # waiting for the user to pull the drive and put it back.
            republish_usb_state()
        elif incident.message == USB_FSCK_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   fsck has already had its turn, and anything further needs the
            #   drive in a PC. The value is knowing: a drive reaching this state
            #   tends to do it again.
            pass
        elif incident.message == USB_WIFI_DEFERRED_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   The file is valid and still on the drive; NetworkManager simply
            #   never became available. Re-inserting the drive retries the
            #   import.
            pass
        elif incident.message == USB_FILE_FAILED:
            # NO MITIGATION: only the user can fix the file.
            #   Wifi_invoer.json on the drive could not be used: malformed JSON, a
            #   'networks' key that is not a list, or an entry without a usable SSID and
            #   password. The user wrote that file, and only the user can correct it.
            #
            #   The file is deliberately left on the drive -- it is only removed once
            #   every network in it was accepted -- so correcting it and putting the
            #   drive back in is the whole repair.
            #
            #   One of the six does not fit that description: NetworkManager refusing a
            #   valid entry. That is an nmcli failure wearing a USB incident's name, and
            #   the same reasoning as WIFI_NMCLI_FAILED applies -- one call that did not
            #   work, not NetworkManager being broken. The details say which of the six
            #   it was.
            pass
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
            # And then republish, because an observer that was gone for a while
            # may have missed a mount or unmount: the restart brings the
            # watching back but not what happened while nobody was watching.
            #
            # Unconditional, even when the restart failed: the state is just as
            # likely to be stale either way, and reading a mount point costs
            # nothing.
            self._restart_subsystem("USB observer", USBObserver)
            republish_usb_state()
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
            # NO MITIGATION: volume_control escalates this itself.
            #   amixer could not set a softvol control. Usually that means the controls
            #   are not there at all -- alsactl restore failed at boot, see the note at
            #   that ExecStartPre= in oradio.service -- and then the volume knob does
            #   nothing, which the user notices at once.
            #
            #   Handled where it is raised. _set_volume() runs ten to fifteen times per
            #   knob turn with no retry in front of it, so a single miss says nothing;
            #   it counts failures inside SET_FAILURE_WINDOW and calls fatal_exit() past
            #   SET_FAILURE_LIMIT. Only a process restart re-runs oradio-prestart.sh,
            #   whose conditional alsactl restore is the one thing that recreates
            #   missing controls.
            #
            #   The limit is set generously on purpose, because nobody has measured how
            #   often amixer misses in the field. The count travels with every incident,
            #   which is the measurement it should eventually be tuned on.
            pass
        elif incident.message == VOLUME_STOPPED:
            # NO MITIGATION: see the class docstring on *_STOPPED.
            #   The volume knob stops responding, which the user notices at once. The
            #   Oradio keeps playing at whatever level it was on.
            pass
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
            # MITIGATION: make sure the message listener is running.
            #
            # Raised from two places. One is the lazy import of the web stack
            # failing, which repairs itself: _create_server() imports again on
            # the next long press. The other is the message-listener thread
            # failing to start, and nothing repairs that -- it is started once
            # in __init__ and has no stopping condition, so there is no loop
            # watching it.
            #
            # Worth repairing because of how it fails: the portal still answers
            # and still accepts what the user submits, and nothing happens with
            # it. To the user that is a web page that does not work, with no
            # error to point at.
            #
            # ensure_listener() returns at once when the thread is alive, which
            # is the case for the import failure, so one call covers both.
            if self._within_restart_budget("web message listener"):
                WebService().ensure_listener()
        elif incident.message == WEB_START_FAILED:
            # NO MITIGATION: the portal path handles it.
            #   WebService.start() falls back to normal operation on every failure and
            #   asks to be restarted on a second one within its window -- see
            #   _handle_failed_start(). Nothing to add here.
            pass
        elif incident.message == WEB_STOP_FAILED:
            # NO MITIGATION: the leftovers are inert or handled by the next start.
            #   One teardown step did not finish. Each of the three things that can be
            #   left behind is dealt with elsewhere, so there is nothing to add here.
            #
            #   The iptables rule: _ensure_port_redirect() is idempotent and takes a
            #   rule that is already there, so the next portal start uses it as-is.
            #
            #   The dnsmasq config: NetworkManager only reads that directory for a
            #   shared connection, so in client mode it sits there doing nothing.
            #
            #   A uvicorn thread that would not stop: it holds the port, so the next
            #   long press fails -- and WebService._handle_failed_start() falls back,
            #   counts it, and asks for a restart on the second attempt, which is the
            #   only thing that frees a thread Python cannot kill. The user pays two
            #   long presses for that, and there is no shorter route.
            pass
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
            # NO MITIGATION: the portal path handles it.
            #   The same event from the other side: WebService.start() waits on
            #   await_access_point(), so an access point that does not come up is a
            #   portal that does not start, and that path already falls back and counts.
            pass
        elif incident.message == WIFI_DBUS_FAILED:
            # MITIGATION: restart NetworkManager.
            #
            # NetworkManager could not be reached over D-Bus, so the Oradio has
            # no working WiFi handling at all: no state changes, no access
            # point, no reporting to RMS once the current connection drops.
            #
            # A restart starts it if it is gone and rebuilds its bus names if
            # they are wedged. Safe to do from here: WifiEventListener listens
            # for NameOwnerChanged and rebuilds its subscriptions when NM comes
            # back, which is the case this was written for.
            #
            # The connection drops for a moment, which would be a reason not to
            # do this if there were a working connection to protect. Without
            # D-Bus there is not.
            self._restart_service_within_budget(NM_SERVICE)
        elif incident.message == WIFI_NMCLI_FAILED:
            # NO MITIGATION: reporting it IS the mitigation.
            #   Raised by nmcli_try(), the wrapper around every nmcli call, so this is
            #   one command that did not work -- usually about what it was asked to do:
            #   a network that is not there, a wrong password, a connection that
            #   disappeared between listing it and using it. Restarting NetworkManager
            #   for that is the wrong tool and would break a connection that is working
            #   fine. WIFI_DBUS_FAILED above is the one that says NM itself is the
            #   problem.
            pass
        elif incident.message == WIFI_DISCONNECT_FAILED:
            # NO MITIGATION: a race, and covered further down the path.
            #   A race, not a fault: get_wifi_connection() found an active connection
            #   and nmcli could not bring it down, which happens when it went away in
            #   between. Beyond that only a broken nmcli.
            #
            #   Covered downstream either way. wifi_disconnect() is called on the way
            #   into access-point mode and from WebService.stop(); a disconnect that
            #   does not happen means the access point does not come up,
            #   await_access_point() fails, and WebService._handle_failed_start() falls
            #   back and counts it.
            pass
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
