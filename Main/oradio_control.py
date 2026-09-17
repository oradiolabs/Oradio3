#!/usr/bin/env python3
# pylint: disable=missing-function-docstring
# there are a large amount of functions which have clear name, no need for docstring
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Januari 31, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio control and statemachine

"""
import threading
from os.path import ismount
from time import sleep

from log_service import oradio_log
from backlight_service import Backlighting
from volume_control import VolumeControl
from mpd_control import MPDControl
from mpd_service import mpd_is_ready
from mpd_monitor import MPDMonitor     # Optional: MPD events monitoring in the background
from led_control import LEDControl
from touch_buttons import TouchButtons
from rms_service import RMService, INCIDENT
from usb_service import USBService
from web_service import WebService
from wifi_service import WifiService
from utilities import has_internet, DeferredStarter
# from system_sounds import play_sound    # For better readability. pylint: disable=wrong-import-order
from system_sounds import play_sound
from incident_service import IncidentHandler
from log_monitor import LogHealthMonitor
from rpi_monitor import RPiThrottlingMonitor
from power_service import get_power_status

# Moved from constants
from messaging import (
    INCIDENT_SOURCE,
    INCIDENT_RECOVERED,
    INCIDENT_POWER_ERROR,
    Commands,
    CommandMessage,
    IncidentMessage,
    MessageHandlerTemplate,
    USB_SOURCE,
    USB_ABSENT,
    USB_PRESENT,
    POWER_SOURCE,
    POWER_ERROR,
    WIFI_SOURCE,
    WIFI_CONNECTED,
    WIFI_DISCONNECTED,
    WIFI_ACCESS_POINT,
    WIFI_CONNECT_FAILED,
    WEB_SOURCE,
    WEB_IDLE,
    WEB_ACTIVE,
    WEB_PL1_PLAYLIST,
    WEB_PL2_PLAYLIST,
    WEB_PL3_PLAYLIST,
    WEB_PL1_WEBRADIO,
    WEB_PL2_WEBRADIO,
    WEB_PL3_WEBRADIO,
    WEB_PLAYING_SONG,
    VOLUME_SOURCE,
    VOLUME_CHANGED,
    BUTTON_SOURCE,
    BUTTON_SHORT_PRESS_PLAY,
    BUTTON_SHORT_PRESS_STOP,
    BUTTON_SHORT_PRESS_PRESET1,
    BUTTON_SHORT_PRESS_PRESET2,
    BUTTON_SHORT_PRESS_PRESET3,
    BUTTON_LONG_PRESS_PLAY,
)

##### GLOBAL constants ####################################
from constants import (
    ERROR_BLINK_CYCLE,
    USB_MOUNT_POINT,
    MESSAGE_NO_ERROR,
    SOUND_START,
    SOUND_STOP,
    SOUND_PLAY,
    SOUND_NEXT,
    SOUND_PRESET1,
    SOUND_PRESET2,
    SOUND_PRESET3,
    SOUND_USB_PRESENT,
    SOUND_USB_ABSENT,
    SOUND_AP_START,
    SOUND_AP_STOP,
    SOUND_WIFI,
    SOUND_NO_WIFI,
    SOUND_NO_INTERNET,
    SOUND_NEW_PRESET,
    SOUND_NEW_WEBRADIO,
    SOUND_POWER_ERROR,
    LED_PLAY,
    LED_STOP,
    LED_PRESET1,
    LED_PRESET2,
    LED_PRESET3,
)

########## LOCAL constants ################################

WEB_PRESET_STATES = {"StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"}

# Blink cycle for "the Oradio is busy and will be ready shortly".
#
# Slower than ERROR_BLINK_CYCLE on purpose: the rate is what tells the two
# apart, so the user learns one meaning per speed rather than having to work out
# which state the Oradio is in.
#
# Local, unlike ERROR_BLINK_CYCLE: nothing outside this module blinks "busy".
# The crash handler cannot -- by the time it runs, the Oradio is not going to be
# ready shortly -- so there is no second reader and nothing to share.
STARTUP_BLINK_CYCLE = 1.0

# How long the STOP LED blinks on reaching Idle after the incident service
# repaired something. Long enough to catch an eye in the room, short enough that
# it is over before anyone walks up to press a button.
INCIDENT_BLINK_SECONDS = 3.0

# Blink cycle for "the web interface is open". The slowest of the three, because
# it is the only one that is not about something being wrong: the Oradio plays
# or idles as usual while someone configures it from a phone.
WEBSERVICE_BLINK_CYCLE = 2.0

def run_later(delay: float, function, *args) -> None:
    """
    Run function(*args) after delay seconds, without waiting for it.

    Args:
        delay:    Seconds to wait before calling.
        function: What to call.
        *args:    Positional arguments for it.

    One construct for every "do this in a moment" in this module, so a reader
    does not have to work out whether three slightly different Timer spellings
    mean three different things. They did not.

    Fire and forget: nothing is kept, so nothing can be cancelled. A delay that
    has to be called off is a different thing and has its own machinery --
    see _arm_delayed_transition(), which stores its Timer for exactly that.

    Daemon by convention rather than by need: the Oradio is killed, not asked to
    exit, so the interpreter never waits on threads anyway. It costs nothing and
    keeps this the same as every other helper thread here.
    """
    timer = threading.Timer(delay, function, args=args)
    timer.daemon = True
    timer.start()


################## Signal Primitives ######################

# -----------------------

def log_startup_step(step: str) -> None:
    """
    Log seconds since power-on at a named point in the start-up sequence.

    Everything below this line runs at module level, one statement after the
    other, and most of it logs nothing at all -- so a slow step shows up in the
    log as a silence rather than as a duration. These markers turn that silence
    into data: 'grep STARTUP oradio.log' prints the whole sequence with the
    cost of each step as the difference between two lines.

    Measured against /proc/uptime rather than a monotonic clock started here,
    so the numbers can be compared with systemd's own: subtract the service
    start time from 'journalctl -b -u oradio' and what remains is Python.
    Comparing the first marker with that time gives the cost of the imports,
    which happen before any of this runs.

    Args:
        step: Short label for what has just finished.
    """
    try:
        with open("/proc/uptime", encoding="utf-8") as file:
            uptime = float(file.readline().split()[0])
    except (FileNotFoundError, ValueError, IndexError) as ex_err:
        oradio_log.warning("Could not read uptime: %s", ex_err)
        return

    oradio_log.info("STARTUP %-26s at %6.2fs", step, uptime)

# First marker, before anything else runs: everything up to here is interpreter
# start-up and module imports.
log_startup_step("imports done")

# Start Remote Service before any incidents can happen, as othewise those incidents may nog be reported
remote_monitor = RMService()
remote_monitor.start()
log_startup_step("remote monitor")

""" Resource-owning modules have an explicit start/stop allowing it to possibly be restarted when failing. """  # pylint: disable=pointless-string-statement

# Any incident starting backlight is reported to and handled by IncidentHandler
oradio_log.info("Start backlighting")
Backlighting().start()
log_startup_step("backlighting")

# Instantiate led control
leds = LEDControl()
log_startup_step("led control")

# Any incident starting volume control is reported to and handled by IncidentHandler
oradio_log.info("Start volumen control")
VolumeControl().start()
log_startup_step("volume control")

# Instantiate and start the wifi service for monitoring wifi state
oradio_wifi_service = WifiService()
oradio_wifi_service.start()
log_startup_step("wifi service")

# Seconds between checks while waiting for WiFi to carry the power supply
# incident. Long on purpose: nothing else is happening, and a tight loop would
# only spin a core on a device that has already stopped being useful.
POWER_INCIDENT_WIFI_POLL = 10

# Get power supply info
power_status = get_power_status()
log_startup_step("power status")

# Verify the power contract.
#
# Only an actual verdict stops the Oradio. get_power_status() returns False
# when the HUSB238 answered and the contract is one Oradio cannot run on, and
# None when the register could not be read at all. The second is not a verdict:
# the supply may be fine, and refusing to run on an unanswered I2C transaction
# would cost a working device to guard against a fault that was never
# established.
#
# The runtime counterpart is on_incident_power_error(), for a supply that turns
# out not to keep up once the Oradio is running. Same fault, same remedy, and
# the same thing for the user to see and hear -- but a different mechanism,
# because StateMachine does not exist yet at this point in start-up and building
# it here would mean starting the whole Oradio on a supply that cannot run it.
if power_status is False:
    # Blink STOP/OFF led to indicate Oradio has an error
    leds.control_blinking_led(LED_STOP, ERROR_BLINK_CYCLE)

    # Inform the user to use the original power supply
    play_sound(SOUND_POWER_ERROR)

    # Report to RMS, but not before WiFi can carry it.
    #
    # send_message() drops anything queued while WiFi is down, and nothing
    # re-sends it. Firing it here would be firing into the dark: at this point
    # in start-up WifiService has usually only just handed its own start to a
    # background thread, so RMS has not seen a WIFI_CONNECTED message yet and
    # the incident would be discarded a few lines before the Oradio stops
    # doing anything else.
    #
    # Waiting has no cost. The Oradio is not going to serve this user again
    # either way, so the only thing left to accomplish is telling RMS why. An
    # Oradio that never reaches an access point never reports it, which is the
    # right outcome: there is no route by which it could.
    oradio_log.info("Waiting for WiFi to report the power supply incident")

    while not remote_monitor.wifi_connected:
        sleep(POWER_INCIDENT_WIFI_POLL)

    remote_monitor.send_message(INCIDENT, IncidentMessage(POWER_SOURCE, POWER_ERROR))

    # Stop execution: Oradio cannot function on this supply, and there is
    # nothing to recover from. Replacing the power supply means unplugging it,
    # which power-cycles the device, so the check runs again from the top on
    # its own. No re-check here, and no way out of this loop by design.
    while True:
        sleep(3600)

if power_status is None:
    # No verdict, so carry on and let the Oradio be useful. I2CService has
    # already published I2C_READ_FAILED, so the failed read is reported by the
    # layer that owns it and does not need a second incident here.
    oradio_log.warning("Power supply status unknown: continuing without a verified contract")
else:
    # Power contract is ok: log and continue
    oradio_log.info("Power supply: %sV @ %sA", power_status["voltage_v"], power_status["current_a"])

web_service_active = threading.Event() # Track status web_service
web_service_active.clear() # Start-up state is no Web service

# Set by on_incident_recovered() and read once by _state_idle().
#
# A flag rather than blinking from the handler itself, because run_state_method()
# calls turn_off_all_leds() immediately before every state handler: a blink
# started in the handler would be switched off by the transition it just asked
# for. Idle is the only destination on_incident_recovered() uses, so reading it
# there catches every case.
incident_recovered = threading.Event()
incident_recovered.clear()

def announcements_allowed() -> bool:
    """
    Whether the Oradio may speak an announcement right now.

    For sounds the user did not ask for. A button press, a knob turn and
    inserting or pulling the USB stick are all things the user did, and their
    confirmation sound plays whatever state the Oradio is in. An event that
    arrives on its own -- WiFi coming back an hour later, a connection attempt
    failing -- is the Oradio speaking unprompted, and an Oradio that is off
    must stay silent.

    So the line is not "is the Oradio on" but "did the user cause this".
    Callers that confirm a physical action deliberately do not consult this.

    Off is StateIdle: StateStop arms a transition to it four seconds after the
    stop sound, and that is where the Oradio sits until someone touches it
    again. Idle is therefore not a state in which unprompted sound is welcome.

    The exception is the web interface. While it is open the user is actively
    configuring the Oradio, usually its WiFi, and the announcements are the
    feedback on what they just did -- so they are wanted even though the state
    machine is idle.

    Returns:
        True while the Oradio is playing or the web interface is open.
    """
    return state_machine.state in PLAY_STATES or web_service_active.is_set()

# Any incident starting throttling monitor is reported to and handled by IncidentHandler
oradio_log.info("Start throttling monitor")
RPiThrottlingMonitor().start()

# Any incident starting log monitor is reported to and handled by IncidentHandler
oradio_log.info("Start log health monitor")
LogHealthMonitor().start()

# Initialise MPD client.
# Returns promptly even when mpd.service is not up yet: MPDService no longer
# connects in its constructor, and every command fails fast while the circuit
# breaker is open.
#
# The monitor and the library scan below decide when to start on
# mpd_is_ready(), which asks the server directly instead of going through this
# object: two poll loops connecting on one shared MPDClient corrupt it. It waits
# for MPD's greeting, not just for the socket, so the work it releases does not
# then sit on MPDService's lock waiting for a server that is not answering.
oradio_log.info("Initialising MPDControl")
#REVIEW Onno:
# Each thread/process should have its own MPDControl instance.
# A global instance may cause concurrent access conflicts with the MPD service.
# MPDControl includes built-in safeguards against improper use, so this works.
mpd_control = MPDControl()
log_startup_step("mpd control")

# Start the MPD event monitor once MPD is actually there.
#
# MPDMonitor.start() blocks until its worker reports ready, and ready means the
# full database snapshot has been built. Against an mpd.service that is still
# coming up that measured 6.4 seconds, all of it in front of the start-up tune
# -- and the tune needs neither the monitor nor MPD.
#
# Nothing between here and the tune needs it either: the monitor exists to
# notice what MPD does later, so starting it a few seconds late costs nothing
# beyond a few early events nobody was listening for yet.
oradio_log.info("Start MPD event monitoring")
mpd_monitor = MPDMonitor()

#
# inline=False because MPDMonitor.start() blocks until its worker has built the
# database snapshot.
mpd_monitor_starter = DeferredStarter(
    "MPD event monitor",
    mpd_is_ready,
    mpd_monitor.start,
    inline=False,
)
mpd_monitor_starter.start()

# Marks the hand-off, not the monitor being up: the start is deferred, so this
# is the point at which start-up stopped waiting for it.
log_startup_step("mpd monitor deferred")

# Preset validation and the first database scan both need MPD reachable AND the
# USB stick mounted. oradio.service is ordered After=basic.target only, and
# usb-drive-boot.service has nothing ordered after it, so at this point neither
# is guaranteed. Running them anyway reports every preset as broken and scans an
# empty library, and neither is ever retracted.
#
# Deferring costs nothing on the path that matters: the start-up tune, the
# buttons and the volume knob do not wait for the library to be scanned.

def _mpd_library_ready() -> bool:
    """True once MPD answers and the music directory is actually mounted."""
    return mpd_is_ready() and ismount(USB_MOUNT_POINT)

mpd_library = DeferredStarter(
    "MPD library scan",
    _mpd_library_ready,
    mpd_control.initialise_library,
    inline=False,
)
mpd_library.start()
log_startup_step("mpd library scan")

usb_present = threading.Event()
usb_present.set() # USB present to go over start-up sequence (will be updated after first message of USB service

# ----------------------State Machine------------------

class StateMachine:
    """Core Oradio application state machine: manages transitions between
    playback, presets, USB presence, web service, and networking states.
    """

    def __init__(self) -> None:
        self.state = "StateStartUp"
        self.prev_state: str | None = None
        self.task_lock = threading.Lock()
        self._websvc = None  # injected WebService
        self._pd_mode: str | None = None  # track power supply PD state "nom" or "max"

        # Dispatch table for run_state_method
        self._handlers = {
            "StatePlay": self._state_play,
            "StatePreset1": self._state_preset1,
            "StatePreset2": self._state_preset2,
            "StatePreset3": self._state_preset3,
            "StateStop": self._state_stop,
            "StatePlaySongWebIF": self._state_play_song_webif,
            "StateUSBAbsent": self._state_usb_absent,
            "StateStartUp": self._state_startup,
            "StateIdle": self._state_idle,
            "StateError": self._state_error,
        }
        self._delayed_timers: dict[str, threading.Timer] = {}   # key -> Timer

    def set_services(self, web_service):
        """Inject the (already-constructed) WebService instance."""
        self._websvc = web_service

    def start_webservice(self):
        """Start the injected WebService (if any) when USB is present."""
        web_service = self._websvc
        if web_service is None:
            return  # not yet injected

        if not usb_present.is_set():
            oradio_log.warning("WebService start blocked (USB absent)")
            return

        if web_service_active.is_set():
            oradio_log.debug("WebService is already active")
            play_sound(SOUND_AP_START)
            return

        oradio_log.debug("Starting WebService: %r", web_service)

        # Blink first, because the start below takes seconds: the radio has to
        # switch to access-point mode and the server has to come up. Without it
        # the user holds the button, hears the click and sees nothing happen.
        leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)

        if web_service.start():
            return

        # The portal did not come up, and WebService.start() has already fallen
        # back to normal operation -- and asked for a restart if this was the
        # second failure in a row. Nothing is left to repair here.
        #
        # What is left is the LED. It was set blinking above to say "coming up",
        # and web_service_active is never set on a failed start, so nothing else
        # would ever turn it off: the Oradio would sit there promising a portal
        # that is not there. Restored the same way on_webservice_idle() does it,
        # since this is the same end state reached by a different route.
        oradio_log.warning("WebService did not start; restoring the play LED")
        if self.state == "StatePlay":
            leds.turn_on_led(LED_PLAY)
        else:
            leds.turn_off_led(LED_PLAY)

    # --- transition() helpers ---

    def _same_state_next_song(self, requested_state: str) -> bool:
        """If already in the same PLAY_* state, advance to next song and return True."""
        if self.state == requested_state and requested_state in PLAY_STATES:
            if not mpd_control.is_webradio():
                mpd_control.next()
                play_sound(SOUND_NEXT)
                oradio_log.debug("Next song")
                return True
        return False

    def _stop_webservice_if_needed(self, requested_state: str) -> bool:
        """Stop AP webservice if transitioning to Stop; return True if handled."""
        if requested_state == "StateStop" and web_service_active.is_set():
            oradio_web_service.stop()
            return True
        return False

    def _block_webradio_without_internet(self, requested_state: str) -> bool:
        """
        Block WebRadio presets when no internet; return True if blocked.

        Asks NetworkManager rather than resolving a name. NM keeps a
        connectivity assessment it refreshes by probing, so this is one D-Bus
        read with no network traffic, and it is the same signal that decides
        whether WIFI_CONNECTED is published -- so this answer and the WiFi
        state can never contradict each other.

        It is also the better answer. A captive portal resolves every name it
        is asked, so a successful DNS lookup behind one proves nothing, and a
        stream started there plays a login page instead of audio. NM reports
        that as PORTAL and this blocks it.

        has_internet() survives as the fallback for one case: NM could not be
        asked at all, because the event listener is not running. Refusing to
        play on "we could not tell" would take music away over a fault that
        has nothing to do with the connection, so the DNS probe gets the last
        word there.
        """
        if requested_state in WEB_PRESET_STATES:
            preset_key = requested_state[len("State"):]

            # Preset check first, as before: a preset that is not a webradio
            # needs no connectivity answer at all.
            if not mpd_control.is_webradio(preset=preset_key):
                return False

            connected = oradio_wifi_service.has_connectivity()
            if connected is None:
                oradio_log.debug("NetworkManager could not be asked; falling back to a DNS probe")
                connected = has_internet()

            if not connected:
                oradio_log.info("Webradio blocked: no Internet")
                run_later(2, play_sound, SOUND_NO_INTERNET)
                return True
        return False

    def _commit_or_usb_absent(self, requested_state: str) -> None:
        """Commit the target state if USB present; else force USBAbsent."""
        if usb_present.is_set():
            self.prev_state = self.state
            self.state = requested_state
            oradio_log.debug("State changed: %s → %s", self.prev_state, self.state)
        else:
            oradio_log.info("Transition to %s blocked (USB absent)", requested_state)
            if self.state != "StateUSBAbsent":
                self.prev_state = self.state
                self.state = "StateUSBAbsent"
                oradio_log.debug("State set to StateUSBAbsent")

    def _spawn_state_worker(self) -> None:
        """Run the state handler in a separate daemon thread."""
        threading.Thread(
            target=self.run_state_method, args=(self.state,), daemon=True
        ).start()

    # ---- delayed-transition helpers ----
    def _cancel_all_delayed(self):
        """Cancel and clear all pending delayed transitions."""
        for timer in self._delayed_timers.values():
            try:
                timer.cancel()
            except (RuntimeError, ValueError):
                pass
        self._delayed_timers.clear()

    def _arm_delayed_transition(self, key: str, delay_s: float, target_state: str,
                                from_state: str | None = None):
        """
        Schedule an interruptible delayed transition; replaces any existing with same key.

        Args:
            key:          Identifies the timer, so re-arming replaces it.
            delay_s:      Seconds before the transition fires.
            target_state: Where to go when it fires.
            from_state:   Arm only while the machine is still in this state.

        from_state exists because the handler that arms a timer runs on a
        worker thread, started by transition() after it cancelled the previous
        timers. A button pressed in between is committed by transition() on its
        own thread -- and then this call arms a timer the cancel already came
        too early for, which a few seconds later throws away what the user just
        asked for. Pressing a preset while the start-up LED blinked started the
        music and then stopped it again when the blinking ended.

        This narrows that window from seconds to microseconds rather than
        closing it: state and timers are not committed under one lock, so a
        transition landing between the check and the arm below still wins.
        Closing it properly means giving the state machine a lock that spans
        both, which is a larger change than this guard.
        """
        if from_state is not None and self.state != from_state:
            oradio_log.debug(
                "Not arming %s: state moved from %s to %s", key, from_state, self.state
            )
            return

        old = self._delayed_timers.pop(key, None)
        if old is not None:
            try:
                old.cancel()
            except (RuntimeError, ValueError) as err:
                oradio_log.debug("Failed to cancel previous timer: %s", err)

        timer = threading.Timer(delay_s, lambda: self.transition(target_state))
        timer.daemon = True
        self._delayed_timers[key] = timer
        timer.start()

    def transition(self, requested_state: str) -> None:
        """Request a transition; applies guards and spawns the handler."""
        if self.state == "StateError":
            oradio_log.warning("Ignoring transition to %s because StateError is active", requested_state)
            return

        oradio_log.debug("Request Transitioning from %s to %s", self.state, requested_state)

        self._cancel_all_delayed()

        if self._same_state_next_song(requested_state):
            return

        if self._stop_webservice_if_needed(requested_state):
            return

        if self._block_webradio_without_internet(requested_state):
            return

        self._commit_or_usb_absent(requested_state)

        self._spawn_state_worker()

    def run_state_method(self, state_to_handle: str) -> None:
        """Dispatch state handling to the right handler."""
        with self.task_lock:
            leds.turn_off_all_leds()
            handler = self._handlers.get(state_to_handle, self._state_unknown)
            handler()

    # --- State handlers ---

    def _state_play(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)
        else:
            leds.turn_on_led(LED_PLAY)
        mpd_control.play()
        play_sound(SOUND_PLAY)

    def _state_preset1(self):
        leds.turn_on_led(LED_PRESET1)
        mpd_control.play(preset="Preset1")
        play_sound(SOUND_PRESET1)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)

    def _state_preset2(self):
        leds.turn_on_led(LED_PRESET2)
        mpd_control.play(preset="Preset2")
        play_sound(SOUND_PRESET2)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)

    def _state_preset3(self):
        leds.turn_on_led(LED_PRESET3)
        mpd_control.play(preset="Preset3")
        play_sound(SOUND_PRESET3)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)

    def _state_stop(self):
        leds.oneshot_on_led(LED_STOP, 4)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        play_sound(SOUND_STOP)
        # Schedule interruptible transition to Idle after 4 seconds (non-blocking)
        oradio_log.debug("Stop: scheduling transition to Idle in 4 s (interruptible)")
        self._arm_delayed_transition("StopToIdle", 4.0, "StateIdle", from_state="StateStop")
        # handler returns immediately; task_lock released, UI remains responsive

    def _state_play_song_webif(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)
        else:
            leds.turn_on_led(LED_PLAY)
        mpd_control.play()
        play_sound(SOUND_PLAY)

    def _state_usb_absent(self):
        leds.control_blinking_led(LED_STOP, ERROR_BLINK_CYCLE)
        mpd_control.stop()

        # Unguarded for the same reason as on_usb_present(): pulling the stick
        # is a user action, so it is answered even when the Oradio is off.
        play_sound(SOUND_STOP)
        play_sound(SOUND_USB_ABSENT)
        if web_service_active.is_set():
            oradio_web_service.stop()

    def _state_startup(self):
        # The tune goes FIRST, before anything else in this handler.
        #
        # It is what tells the user the Oradio is alive, and the window between
        # it and the first touch is the budget the rest of the system starts up
        # in. Every call placed ahead of it spends that budget instead of using
        # it: leds.control_blinking_led() below goes over i2c, and anything
        # else added here would come before the user hears anything.
        #
        # play_sound() is fire-and-forget -- it launches aplay detached and
        # returns -- so this costs the rest of the handler nothing.
        play_sound(SOUND_START)

        # FOR ANALYSIS: Get time since power-on. Read straight after the launch
        # above, so it measures when the tune actually started.
        try:
            with open("/proc/uptime", encoding="utf-8") as file:
                uptime = float(file.readline().split()[0])
            oradio_log.debug("Playing SOUND_START %.2f seconds after power-on", uptime)
        except (FileNotFoundError, ValueError, IndexError) as ex_err:
            oradio_log.warning("Could not read uptime: %s", ex_err)

        leds.control_blinking_led(LED_STOP, STARTUP_BLINK_CYCLE)
        oradio_log.debug("Starting-up")

        # No mpd_control.pause() here.
        #
        # It was meant to silence an MPD that might still be playing, which
        # cannot be the case: this process has just started and has not told it
        # to play anything. Every boot logged "Ignore pause: not currently
        # playing" -- the command never had anything to do.
        #
        # What it did do was cost time. It is the first MPD command of the run,
        # so it pays the connect, and the timer below is armed only after it
        # returns. On a cold boot MPD is not up yet at this point, which made
        # the LED blink for the connect plus five seconds instead of five, and
        # delayed the Oradio reaching Idle by the same amount.
        #
        # Nothing in this handler talks to MPD now. If the certainty is ever
        # wanted back, it belongs in initialise_library() on the background
        # thread, where it holds nothing up.

        oradio_log.debug("Startup: scheduling transition to Idle in 5 s")
        self._arm_delayed_transition("StartupToIdle", 5.0, "StateIdle", from_state="StateStartUp")

    def _state_idle(self):
        # Say that something was wrong, now that the Oradio is back in a state
        # it can be left in.
        #
        # ERROR_BLINK_CYCLE, the rate the user already knows as trouble, rather
        # than a fourth rate for "there was trouble but it is over": the LED
        # stopping after a few seconds is what says the fault is behind us, and
        # a vocabulary of four speeds is one nobody learns.
        #
        # run_later() and not a sleep: run_state_method() holds task_lock while
        # this runs, so waiting here would be three seconds in which no button
        # could change anything.
        if incident_recovered.is_set():
            incident_recovered.clear()
            leds.control_blinking_led(LED_STOP, ERROR_BLINK_CYCLE)
            run_later(INCIDENT_BLINK_SECONDS, leds.turn_off_led, LED_STOP)

# REVIEW: Is this only there because transitioning through StateIdle is used by on_webservice_plX_changed() ?
#         If yes, then fix on_webservice_plX_changed() to not abuse StateIdle to do something which should be handled in the StatePresetX state.
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)

        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        oradio_log.debug("In Idle state, wait for next step")

    def _state_error(self):
        leds.control_blinking_led(LED_STOP, ERROR_BLINK_CYCLE)

    def _state_unknown(self):
        oradio_log.error("Unknown state requested: %s", self.state)

# -------------Messages handler: -----------------

# 1) Functions which define the actions for the messages

# -------------------VOLUME------------------------

def on_volume_changed() -> None:
    oradio_log.info("Volume changed acknowlegded")
    if state_machine.state in {"StateIdle"}:
        state_machine.transition("StatePlay")

# -------------------USB---------------------------

def on_usb_absent():
    oradio_log.info("USB absent acknowlegded")
    if not usb_present.is_set():
        return
    usb_present.clear()
    if state_machine.state != "StateStartUp":
        state_machine.transition("StateUSBAbsent")

def on_usb_present():
    oradio_log.info("USB present acknowledged")
    if usb_present.is_set():
        return
    usb_present.set()

    # No announcements_allowed() check, on purpose: pushing a stick in is a
    # user action, and the confirmation that it was accepted is wanted whether
    # the Oradio is playing or off.
    play_sound(SOUND_USB_PRESENT)

    # The stick may have arrived after the boot-time set-up gave up, and it may
    # carry a different presets.json than the last one, so the library is
    # prepared again. Returns immediately if MPD is still not up.
    mpd_control.initialise_library()
    # Transition to Idle after USB is inserted
    if state_machine.state != "StateStartUp":
        state_machine.transition("StateIdle")

# -------------------WIFI--------------------------

# Messages when after the closure of the Oradio AP Webservice the Wifi connection is/not made

def on_wifi_connected():
    oradio_log.info("Wifi is connected acknowledged")

    # Checked when the timer fires, not when it is armed: four seconds is long
    # enough for the user to have pressed stop in between, and the Oradio would
    # then announce itself after going quiet.
    def _announce() -> None:
        if announcements_allowed():
            play_sound(SOUND_WIFI)
        else:
            oradio_log.debug("Oradio is off: not announcing WiFi connected")

    run_later(4, _announce)

def on_wifi_fail_connect():
    oradio_log.info("Wifi fail connect acknowledged")
    if announcements_allowed():
        play_sound(SOUND_NO_WIFI)
    else:
        oradio_log.debug("Oradio is off: not announcing WiFi connect failure")

def on_wifi_access_point():
    oradio_log.info("Configured as access point acknowledged")

def on_wifi_not_connected():
    oradio_log.info("Wifi is NOT connected acknowledged")
#    on_wifi_fail_connect() # do same actions as on_wifi_fail_connect

# -------------------WEB---------------------------

def on_webservice_active():
    oradio_log.info("WebService active is acknowledged")
    if web_service_active.is_set(): # check already taken the actions
        return
    web_service_active.set()
    leds.control_blinking_led(LED_PLAY, WEBSERVICE_BLINK_CYCLE)
    play_sound(SOUND_AP_START)
    # handle Webradio
    if mpd_control.is_webradio():
        state_machine.transition("StateIdle")
        oradio_log.info("Stopped WebRadio playback on Webservice entry")

def on_incident_power_error():
    """
    Stop for good after the supply stayed below the minimum.

    Nothing the Oradio can do raises the voltage, and every second it keeps
    drawing current is a second the SD card is at risk. Replacing the supply
    cuts the power anyway, so playing on until the user acts buys nothing.

    StateError rather than a dead-end loop: transition() refuses every request
    once that state is active, and _state_error() blinks the STOP LED at
    ERROR_BLINK_CYCLE. The process stays alive, so the LED keeps blinking and
    incidents keep reaching RMS.

    The start-up counterpart is the power_status check near the top of this
    module, which blinks the same LED at the same rate and plays the same sound
    but then sleeps rather than using StateError -- the state machine does not
    exist that early. Two mechanisms, one outcome; change one and check the
    other.
    """
    oradio_log.error("Supply voltage too low: stopping")
    play_sound(SOUND_POWER_ERROR)
    state_machine.transition("StateError")


def on_incident_recovered():
    """
    Start again from Idle after the incident service repaired something.

    The single handler for everything the incident service repairs. One and not
    one per subsystem, because they all leave the same problem behind -- the
    Oradio believing something that was true before the repair -- and because
    this is where an announcement like "the Oradio fixed a problem" would go if
    one is ever wanted.

    The case it was built for is mpd: restarting it takes the playback queue
    with it, so a state machine sitting in StatePresetN is describing music that
    is no longer playing -- the LED is on, the state says a preset, and mpd has
    nothing queued. Pressing that same preset again does not fix it either,
    because transition() reads a repeat of the current state as "next song".

    Idle and not StateStartUp, which is a claim about the Oradio's age rather
    than a set of LEDs: on_usb_absent() and on_webservice_idle() both skip their
    work while the state is StateStartUp, so entering it mid-life would make
    them ignore events that are real.
    """
    oradio_log.info("Incident recovered: starting again from Idle")
    incident_recovered.set()
    state_machine.transition("StateIdle")


def on_webservice_idle():
    oradio_log.info("WebService idle is acknowledged")
    if not web_service_active.is_set(): # check already taken the actions
        return
    web_service_active.clear()
    if state_machine.state == "StatePlay":
        leds.turn_on_led(LED_PLAY)
    else:
        leds.turn_off_led(LED_PLAY)
    play_sound(SOUND_AP_STOP)

def on_webservice_playing_song():
    if (
        state_machine.state == "StateStop"
    ):  # if webservice put songs in queue and plays it
        state_machine.transition(
            "StatePlaySongWebIF"
        )  #  and if player is switched of, switch it on, otherwise keep state
    oradio_log.debug("WebService playing song acknowledged")

def on_webservice_pl1_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset1")
    run_later(2, play_sound, SOUND_NEW_PRESET)
    oradio_log.debug("WebService on_webservice_pl1_changed acknowledged")

def on_webservice_pl2_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset2")
    run_later(2, play_sound, SOUND_NEW_PRESET)
    oradio_log.debug("WebService on_webservice_pl2_changed acknowledged")

def on_webservice_pl3_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset3")
    run_later(2, play_sound, SOUND_NEW_PRESET)
    oradio_log.debug("WebService on_webservice_pl3_changed acknowledged")

def on_web_pl1_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    run_later(2, play_sound, SOUND_NEW_WEBRADIO)
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

def on_web_pl2_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    run_later(2, play_sound, SOUND_NEW_WEBRADIO)
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

def on_web_pl3_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    run_later(2, play_sound, SOUND_NEW_WEBRADIO)
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

# ----------------- Touch buttons -----------------
# Thread-safety for transitions (shared with volume callbacks)
sm_lock = threading.RLock()

def _go(state: str) -> None:
    with sm_lock:
        state_machine.transition(state)

# --- Touch button policy wiring ---
def _on_play_pressed() -> None:
    _go("StatePlay")

def _on_stop_pressed() -> None:
    _go("StateStop")

def _on_preset1_pressed() -> None:
    _go("StatePreset1")

def _on_preset2_pressed() -> None:
    _go("StatePreset2")

def _on_preset3_pressed() -> None:
    _go("StatePreset3")

def _on_play_long_pressed() -> None:
    # Long-press Play starts the web service (guarded by SM + lock)
    with sm_lock:
        state_machine.start_webservice()
# --- end wiring ---

# 2)-----The Handler map, defining message content and the handler funtion---

# REVIEW Onno:
#   WIFI_CONNECT_FAILED wordt als incident gerapporteerd, daar nu als command doorgestuurd.
#   Te kiezen: is het een command of een incident?
HANDLERS = {
    VOLUME_SOURCE: {
        VOLUME_CHANGED: on_volume_changed,
    },
    USB_SOURCE: {
        USB_ABSENT: on_usb_absent,
        USB_PRESENT: on_usb_present,
    },
    WIFI_SOURCE: {
        WIFI_DISCONNECTED: on_wifi_not_connected,
        WIFI_CONNECTED: on_wifi_connected,
        WIFI_ACCESS_POINT: on_wifi_access_point,
        WIFI_CONNECT_FAILED: on_wifi_fail_connect,
    },
    INCIDENT_SOURCE: {
        INCIDENT_RECOVERED: on_incident_recovered,
        INCIDENT_POWER_ERROR: on_incident_power_error,
    },
    WEB_SOURCE: {
        WEB_IDLE: on_webservice_idle,
        WEB_ACTIVE: on_webservice_active,
        WEB_PLAYING_SONG: on_webservice_playing_song,
        WEB_PL1_PLAYLIST: on_webservice_pl1_changed,
        WEB_PL2_PLAYLIST: on_webservice_pl2_changed,
        WEB_PL3_PLAYLIST: on_webservice_pl3_changed,
        WEB_PL1_WEBRADIO: on_web_pl1_webradio_changed,
        WEB_PL2_WEBRADIO: on_web_pl2_webradio_changed,
        WEB_PL3_WEBRADIO: on_web_pl3_webradio_changed,
    },
    BUTTON_SOURCE: {
        BUTTON_SHORT_PRESS_PLAY: _on_play_pressed,
        BUTTON_SHORT_PRESS_STOP: _on_stop_pressed,
        BUTTON_SHORT_PRESS_PRESET1: _on_preset1_pressed,
        BUTTON_SHORT_PRESS_PRESET2: _on_preset2_pressed,
        BUTTON_SHORT_PRESS_PRESET3: _on_preset3_pressed,
        BUTTON_LONG_PRESS_PLAY: _on_play_long_pressed
    },

}

def handle_message(message: CommandMessage) -> None:
    """
    Handle a received command message.

    Args:
        message: The CommandMessage to be processed.
    """
    command_source = message.source
    state          = message.message
    error          = MESSAGE_NO_ERROR if message.data is None else message.data

    handlers = HANDLERS.get(command_source)
    if handlers is None:
        oradio_log.warning("Unhandled message source: %s", message)
        return

    if handler := handlers.get(state):
        handler()
    else:
        oradio_log.warning(
            "Unhandled state '%s' for message source '%s'.", state, command_source
        )

#REVIEW:
#   errors, tegenwoording incidents, worden niet  via de Command bus doorgegeven, gaan naar de incident handler.
#   CommandMessage kent een data veld met mogelijk extra info bij message.
#   Het is dus logischer om data hierboven aan de handler mee te geven en in handler te verwerken.
    if error != MESSAGE_NO_ERROR and isinstance(error, str):
        if handler := handlers.get(error):
            handler()
        else:
            oradio_log.warning(
                "Unhandled error '%s' for message source '%s'.", error, command_source
            )

# 3)----------- Process the messages---------

class OradioCommandHandler(MessageHandlerTemplate):
    """Dispatches every published CommandMessage straight to handle_message()."""

    def _handle_message(self, message: CommandMessage) -> None:
        handle_message(message)

# ------------------Start-up - instantiate and define other modules ---------------

# Instantiate and start the USB service monitoring USB present/absent
oradio_usb_service = USBService()
oradio_usb_service.start()
log_startup_step("usb service")

# No explicit sync of usb_present here: Commands.subscribe() below replays the
# last message from every source, so the USB service's own USB_PRESENT or
# USB_ABSENT reaches the handler as soon as it subscribes, and sets the event
# through the same path every later change takes.
#
# Reading the service directly as well gave two answers to one question --
# get_state() reporting the mount right now, the replay reporting what was last
# published -- with nothing deciding which wins when they differ.

# Subscribe to incidents bus so incidents published are mitigated
incident_handler = IncidentHandler()
log_startup_step("incident handler")

# Instantiate and start handling buttons
touch_buttons = TouchButtons()
log_startup_step("touch buttons")

# Instantiate and start the web service for managing the access point
oradio_web_service = WebService()
log_startup_step("web service")

# Instantiate the state machine
state_machine = StateMachine()

# inject the services into the Statemachine
state_machine.set_services(oradio_web_service)

# start the state_machine transition
state_machine.transition("StateStartUp")

# Warm the web stack now the Oradio is up and the tune is playing.
#
# WebService.start() runs on the command handler thread while holding the state
# machine lock, and its first call imports uvicorn and FastAPI -- about six
# seconds cold. A long press would freeze every other button, knob and event
# for that long, at the worst possible moment: a user configuring wifi on a new
# device presses it within seconds of switching on.
#
# A plain thread, not a DeferredStarter: there is no dependency to wait for.
# Daemon, so it never holds up a shutdown, and started without delay because
# the window it protects is exactly the first half-minute.
threading.Thread(
    target=oradio_web_service.preload, daemon=True, name="web-stack-warmup"
).start()

# Subscribe to and dispatch all command messages (starts its own worker thread)
oradio_command_handler = OradioCommandHandler(Commands.subscribe())

def main() -> None:
    """
    Main loop for oradio_control.
    """
    oradio_log.debug("Oradio control main loop running")
    while True:
        sleep(1)

if __name__ == "__main__":

    main()
