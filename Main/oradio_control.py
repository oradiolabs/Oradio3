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
from time import sleep

from log_service import oradio_log
from backlight_service import Backlighting
from volume_control import VolumeControl
from mpd_control import MPDControl
from mpd_monitor import MPDMonitor     # Optional: MPD events monitoring in the background
from led_control import LEDControl
from touch_buttons import TouchButtons
from rms_service import RMService
from spotify_connect_direct import SpotifyConnect
from usb_service import USBService
from web_service import WebService
from wifi_service import WifiService
from utilities import has_internet
# from system_sounds import play_sound    # For better readability. pylint: disable=wrong-import-order
from system_sounds import play_sound
from incident_service import IncidentHandler
from log_monitor import LogHealthMonitor
from rpi_monitor import ThrottlingMonitor
from power_supply_control import PowerSupplyService

# Moved from constants
from messaging import (
    Commands,
    CommandMessage,
    MessageHandlerTemplate,
    USB_SOURCE,
    USB_ABSENT,
    USB_PRESENT,
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
    SPOTIFY_SOURCE,
    SPOTIFY_CONNECTED_EVENT,
    SPOTIFY_DISCONNECTED_EVENT,
    SPOTIFY_PLAYING_EVENT,
    SPOTIFY_PAUSED_EVENT,
)

##### GLOBAL constants ####################################
from constants import (
    MESSAGE_NO_ERROR,
    SOUND_START,
    SOUND_STOP,
    SOUND_PLAY,
    SOUND_NEXT,
    SOUND_PRESET1,
    SOUND_PRESET2,
    SOUND_PRESET3,
    SOUND_SPOTIFY,
    SOUND_USB,
    SOUND_NO_USB,
    SOUND_AP_START,
    SOUND_AP_STOP,
    SOUND_WIFI,
    SOUND_NO_WIFI,
    SOUND_NO_INTERNET,
    SOUND_NEW_PRESET,
    SOUND_NEW_WEBRADIO,
    LED_PLAY,
    LED_STOP,
    LED_PRESET1,
    LED_PRESET2,
    LED_PRESET3,
)

########## LOCAL constants ################################

WEB_PRESET_STATES = {"StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_WEBSERVICE_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateIdle"}
LOW_POWER_STATES = {"StateIdle"}  # only Idle uses nominal voltage (9V)to reduce power consumption

################## Signal Primitives ######################

spotify_connect_connected = threading.Event()  # track status Spotify connected
spotify_connect_playing = threading.Event()  # track Spotify playing
spotify_connect_available = threading.Event()  # track Spotify playing & connected

# -----------------------
web_service_active = threading.Event() # Track status web_service
web_service_active.clear() # Start-up state is no Web service

usb_present = threading.Event()
usb_present.set() # USB present to go over start-up sequence (will be updated after first message of USB service

""" Resource-owning modules have an explicit start/stop allowing it to possibly be restarted when failing. """  # pylint: disable=pointless-string-statement
# IMPORTANT: Start Remote Service before any incidents can happen, as othewise those incidents may nog be reported
remote_monitor = RMService()
remote_monitor.start()

# Any incident starting backlight is reported to and handled by IncidentHandler
oradio_log.info("Start backlighting")
Backlighting().start()

# Any incident starting throttling monitor is reported to and handled by IncidentHandler
oradio_log.info("Start throttling monitor")
ThrottlingMonitor().start()

# Any incident starting log monitor is reported to and handled by IncidentHandler
oradio_log.info("Start log health monitor")
LogHealthMonitor().start()

# Any incident starting volume control is reported to and handled by IncidentHandler
oradio_log.info("Start volumen control")
VolumeControl().start()

oradio_log.info("Start MPD event monitoring")
mpd_monitor = MPDMonitor()
mpd_monitor.start()

# Initialise MPD client
oradio_log.info("Initialising MPDControl")
#REVIEW Onno:
# Each thread/process should have its own MPDControl instance.
# A global instance may cause concurrent access conflicts with the MPD service.
# MPDControl includes built-in safeguards against improper use, so this works.
mpd_control = MPDControl()
# Update MPD database - happens in separate thread
mpd_control.update_database()

# Initialise power supply controller, to optimse supply voltage for the various states
power_supply_service = PowerSupplyService()

# Instantiate  led control
leds = LEDControl()

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
            "StateSpotifyConnect": self._state_spotify_connect,
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
        leds.control_blinking_led(LED_PLAY)
        web_service.start()

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

    def _redirect_spotify_if_needed(self, requested_state: str) -> str:
        """Redirect Play to SpotifyConnect when Spotify is available."""
        if spotify_connect_available.is_set() and requested_state == "StatePlay":
            oradio_log.debug("Spotify Connect active → redirecting to StateSpotifyConnect")
            return "StateSpotifyConnect"
        return requested_state

    def _stop_webservice_if_needed(self, requested_state: str) -> bool:
        """Stop AP webservice if transitioning to Stop; return True if handled."""
        if requested_state == "StateStop" and web_service_active.is_set():
            oradio_web_service.stop()
            return True
        return False

    def _block_webradio_without_internet(self, requested_state: str) -> bool:
        """Block WebRadio presets when no internet; return True if blocked."""
        if requested_state in WEB_PRESET_STATES:
            preset_key = requested_state[len("State"):]
            if mpd_control.is_webradio(preset=preset_key) and not has_internet():
                oradio_log.info("Webradio blocked: no Internet")
                threading.Timer(2, play_sound, args=(SOUND_NO_INTERNET,)).start()
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

    def _apply_power_policy_for_state(self, target_state: str) -> None:
        desired_mode = "nom" if target_state in LOW_POWER_STATES else "max"
        if desired_mode == self._pd_mode:
            return  # already correct -> do nothing

        if desired_mode == "nom":
            success = power_supply_service.set_nom_voltage()
        else:
            success = power_supply_service.set_max_voltage()

        if success:
            self._pd_mode = desired_mode

    # ---- delayed-transition helpers ----
    def _cancel_all_delayed(self):
        """Cancel and clear all pending delayed transitions."""
        for timer in self._delayed_timers.values():
            try:
                timer.cancel()
            except (RuntimeError, ValueError):
                pass
        self._delayed_timers.clear()

    def _arm_delayed_transition(self, key: str, delay_s: float, target_state: str):
        """Schedule an interruptible delayed transition; replaces any existing with same key."""
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

        requested_state = self._redirect_spotify_if_needed(requested_state)

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
        # outside lock (more responsive, and power policy can be changed even when it is playing)
        self._apply_power_policy_for_state(state_to_handle)

    # --- State handlers ---

    def _state_play(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        else:
            leds.turn_on_led(LED_PLAY)
        mpd_control.play()
        spotify_connect.mute()
        play_sound(SOUND_PLAY)

    def _state_preset1(self):
        leds.turn_on_led(LED_PRESET1)
        mpd_control.play(preset="Preset1")
        play_sound(SOUND_PRESET1)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.mute()

    def _state_preset2(self):
        leds.turn_on_led(LED_PRESET2)
        mpd_control.play(preset="Preset2")
        play_sound(SOUND_PRESET2)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.mute()

    def _state_preset3(self):
        leds.turn_on_led(LED_PRESET3)
        mpd_control.play(preset="Preset3")
        play_sound(SOUND_PRESET3)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.mute()

    def _state_stop(self):
        leds.oneshot_on_led(LED_STOP, 4)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        spotify_connect.mute()
        play_sound(SOUND_STOP)
        # Schedule interruptible transition to Idle after 4 seconds (non-blocking)
        oradio_log.debug("Stop: scheduling transition to Idle in 4 s (interruptible)")
        self._arm_delayed_transition("StopToIdle", 4.0, "StateIdle")
        # handler returns immediately; task_lock released, UI remains responsive

    def _state_spotify_connect(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        else:
            leds.turn_on_led(LED_PLAY)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        spotify_connect.unmute()
        play_sound(SOUND_SPOTIFY)

    def _state_play_song_webif(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        else:
            leds.turn_on_led(LED_PLAY)
        spotify_connect.mute()
        mpd_control.play()
        play_sound(SOUND_PLAY)

    def _state_usb_absent(self):
        leds.control_blinking_led(LED_STOP, 0.7)
        mpd_control.stop()
        spotify_connect.mute()
        play_sound(SOUND_STOP)
        play_sound(SOUND_NO_USB)
        if web_service_active.is_set():
            oradio_web_service.stop()

    def _state_startup(self):
        leds.control_blinking_led(LED_STOP, 1)
        oradio_log.debug("Starting-up")
        mpd_control.pause()
        spotify_connect.mute()

        # FOR ANALYSIS: Get time since power-on
        try:
            with open("/proc/uptime", encoding="utf-8") as file:
                uptime = float(file.readline().split()[0])
            oradio_log.debug("Playing SOUND_START %.2f seconds after power-on", uptime)
        except (FileNotFoundError, ValueError, IndexError) as ex_err:
            oradio_log.warning("Could not read uptime: %s", ex_err)

        play_sound(SOUND_START)
        oradio_log.debug("Startup: scheduling transition to Idle in 5 s")
        self._arm_delayed_transition("StartupToIdle", 5.0, "StateIdle")

    def _state_idle(self):
        # Listen for volume changed notifications
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        spotify_connect.mute()
        oradio_log.debug("In Idle state, wait for next step")

    def _state_error(self):
        leds.control_blinking_led(LED_STOP, 1)

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
    play_sound(SOUND_USB)
    # Ensure MPD database is updated
    mpd_control.update_database()
    # Transition to Idle after USB is inserted
    if state_machine.state != "StateStartUp":
        state_machine.transition("StateIdle")

# -------------------WIFI--------------------------

# Messages when after the closure of the Oradio AP Webservice the Wifi connection is/not made

def on_wifi_connected():
    oradio_log.info("Wifi is connected acknowledged")

    if state_machine.state in PLAY_WEBSERVICE_STATES:  # If in play states,
        threading.Timer(
            4, play_sound, args=(SOUND_WIFI,)
        ).start()

def on_wifi_fail_connect():
    oradio_log.info("Wifi fail connect acknowledged")
    if state_machine.state in PLAY_WEBSERVICE_STATES:  # If in play states,
        play_sound(SOUND_NO_WIFI)

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
    leds.control_blinking_led(LED_PLAY)
    play_sound(SOUND_AP_START)
    # handle Webradio and Spotify
    if mpd_control.is_webradio() or state_machine.state == "StateSpotifyConnect":
        state_machine.transition("StateIdle")
        oradio_log.info("Stopped WebRadio and Spotify playback on Webservice entry")

def on_webservice_idle():
    oradio_log.info("WebService idle is acknowledged")
    if not web_service_active.is_set(): # check already taken the actions
        return
    web_service_active.clear()
    if state_machine.state == "StatePlay":
        leds.turn_on_led(LED_PLAY)
    else:
        leds.control_blinking_led(LED_PLAY, 0)
    play_sound(SOUND_AP_STOP)

def on_webservice_playing_song():
    spotify_connect.mute()  # spotify is on pause and will not work
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
    threading.Timer(2, play_sound, args=(SOUND_NEW_PRESET,)).start()
    oradio_log.debug("WebService on_webservice_pl1_changed acknowledged")

def on_webservice_pl2_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset2")
    threading.Timer(2, play_sound, args=(SOUND_NEW_PRESET,)).start()
    oradio_log.debug("WebService on_webservice_pl2_changed acknowledged")

def on_webservice_pl3_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset3")
    threading.Timer(2, play_sound, args=(SOUND_NEW_PRESET,)).start()
    oradio_log.debug("WebService on_webservice_pl3_changed acknowledged")

def on_web_pl1_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    threading.Timer(2, play_sound, args=(SOUND_NEW_WEBRADIO,)).start()
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

def on_web_pl2_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    threading.Timer(2, play_sound, args=(SOUND_NEW_WEBRADIO,)).start()
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

def on_web_pl3_webradio_changed():
#REVIEW Onno: Er is geen indicatie voor welke preset de webradio is ingesteld
    threading.Timer(2, play_sound, args=(SOUND_NEW_WEBRADIO,)).start()
    oradio_log.debug("WebService on_web_pl_webradio_changed acknowledged")

# -------------------SPOTIFY-----------------------

def on_spotify_connect_connected():
    spotify_connect_connected.set()
    update_spotify_available()
    oradio_log.debug("Spotify active is acknowledged")

def on_spotify_connect_disconnected():
    spotify_connect_connected.clear()
    update_spotify_available()
    oradio_log.debug("Spotify inactive is acknowledged")

def on_spotify_connect_playing():
    spotify_connect_connected.set()
    spotify_connect_playing.set()
    update_spotify_available()
    oradio_log.debug("Spotify playing is acknowledged")

def on_spotify_connect_paused():
    spotify_connect_connected.set()
    spotify_connect_playing.clear()
    update_spotify_available()
    oradio_log.debug("Spotify paused is acknowledged")

def on_spotify_connect_stopped():
    spotify_connect_playing.clear()
    update_spotify_available()
    oradio_log.debug("Spotify stopped is acknowledged")

def on_spotify_connect_changed():
    # TBD action
    oradio_log.debug("Spotify changed is acknowledged")

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

def update_spotify_available():
    """Update the 'available' flag based on connected+playing, and react if needed."""
    if spotify_connect_connected.is_set() and spotify_connect_playing.is_set():
        spotify_connect_available.set()
        if state_machine.state in ("StatePlay",):
            state_machine.transition("StateSpotifyConnect")
    else:
        spotify_connect_available.clear()
        if state_machine.state == "StateSpotifyConnect":
            state_machine.transition("StateStop")

    oradio_log.info(
        "Spotify Connect States - Connected: %s, Playing: %s, Available: %s",
        spotify_connect_connected.is_set(),
        spotify_connect_playing.is_set(),
        spotify_connect_available.is_set(),
    )

# 2)-----The Handler map, defining message content and the handler funtion---

# REVIEW Onno:
#   Let op: WIFI_CONNECT_FAILED wordt ook als incident gerapporteerd.
#   Te kiezen: is het een command zoals nu, of een incident?
#   Voorstel: Het is een incident, met als mitigation een announcement en state wordt disconnected
HANDLERS = {
    VOLUME_SOURCE: {
        VOLUME_CHANGED: on_volume_changed,
    },
    USB_SOURCE: {
        USB_ABSENT: on_usb_absent,
        USB_PRESENT: on_usb_present,
        # "USB error": on_usb_error,
    },
    WIFI_SOURCE: {
        WIFI_DISCONNECTED: on_wifi_not_connected,
        WIFI_CONNECTED: on_wifi_connected,
        WIFI_ACCESS_POINT: on_wifi_access_point,
        WIFI_CONNECT_FAILED: on_wifi_fail_connect,
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
    SPOTIFY_SOURCE: {
        SPOTIFY_CONNECTED_EVENT: on_spotify_connect_connected,
        SPOTIFY_DISCONNECTED_EVENT: on_spotify_connect_disconnected,
        SPOTIFY_PLAYING_EVENT: on_spotify_connect_playing,
        SPOTIFY_PAUSED_EVENT: on_spotify_connect_paused,
        # "Spotify error": on_spotify_error,
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

#-------------USB presence sync at start -up---------------------------------------

def sync_usb_presence_from_service():
    """
    One time sync at start-up
    """
    state = oradio_usb_service.get_state()
    oradio_log.info("USB service raw state: %r", state)

    if state == USB_PRESENT:
        usb_present.set()
        oradio_log.info("USB presence synced: present")
    elif state == USB_ABSENT:
        usb_present.clear()
        oradio_log.info("USB presence synced: absent")
    else:
        oradio_log.warning("Unexpected USB service state: %r", state)

# ------------------Start-up - instantiate and define other modules ---------------

# Instantiate and start the wifi service for monitoring wifi state
oradio_wifi_service = WifiService()
oradio_wifi_service.start()

# Instantiate and start the USB service monitoring USB present/absent
oradio_usb_service = USBService()
oradio_usb_service.start()

# REVIEW Onno: sync_usb_presence_from_service is overbodig, want USB status komt via de command queue
sync_usb_presence_from_service()

# Subscribe to incidents bus so incidents published are mitigated
incident_handler = IncidentHandler()

# Instantiate and start Spotify connect
spotify_connect = SpotifyConnect()
spotify_connect.start()

# Instantiate and start handling buttons
touch_buttons = TouchButtons()

# Instantiate and start the web service for managing the access point
oradio_web_service = WebService()

# Instantiate the state machine
state_machine = StateMachine()

# inject the services into the Statemachine
state_machine.set_services(oradio_web_service)

# start the state_machine transition
state_machine.transition("StateStartUp")

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
