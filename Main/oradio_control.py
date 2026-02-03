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


Created on Januari 31`, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio control and statemachine
 
"""
import subprocess
import sys
import shutil
import threading
from multiprocessing import Queue

from oradio_logging import oradio_log
from backlighting import Backlighting
from volume_control import VolumeControl
from mpd_control import MPDControl
from mpd_monitor import MPDMonitor     # Optional: MPD events monitoring in the background
from led_control import LEDControl
from touch_buttons import TouchButtons
from remote_monitoring import RMService
from spotify_connect_direct import SpotifyConnect
from usb_service import USBService
from web_service import WebService
from oradio_utils import has_internet,validate_oradio_message
from power_supply_control import PowerSupplyService
from system_sounds import play_sound    # For better readability. pylint: disable=wrong-import-order
# Runs a background thread logging throttled events
import throttled_monitor     # pylint: disable=unused-import

##### GLOBAL constants ####################
from oradio_const import (
    MESSAGE_NO_ERROR,
    MESSAGE_VOLUME_SOURCE,
    MESSAGE_VOLUME_CHANGED,
    STATE_WEB_SERVICE_IDLE,
    STATE_WEB_SERVICE_ACTIVE,
    MESSAGE_WEB_SERVICE_PL1_CHANGED,
    MESSAGE_WEB_SERVICE_PL2_CHANGED,
    MESSAGE_WEB_SERVICE_PL3_CHANGED,
    MESSAGE_WEB_SERVICE_PL_WEBRADIO,
    MESSAGE_WEB_SERVICE_PLAYING_SONG,
    MESSAGE_WEB_SERVICE_SOURCE,
    MESSAGE_SPOTIFY_SOURCE,
    SPOTIFY_CONNECT_CONNECTED_EVENT,
    SPOTIFY_CONNECT_DISCONNECTED_EVENT,
    SPOTIFY_CONNECT_PAUSED_EVENT,
    SPOTIFY_CONNECT_PLAYING_EVENT,
    MESSAGE_USB_SOURCE,
    STATE_USB_ABSENT,
    STATE_USB_PRESENT,
    MESSAGE_WIFI_SOURCE,
    MESSAGE_WIFI_FAIL_CONNECT,
    STATE_WIFI_ACCESS_POINT,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_IDLE,
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
    MESSAGE_BUTTON_SOURCE,
    MESSAGE_SHORT_PRESS_BUTTON_PLAY,
    MESSAGE_SHORT_PRESS_BUTTON_STOP,
    MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
    MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
    MESSAGE_SHORT_PRESS_BUTTON_PRESET3,
    MESSAGE_LONG_PRESS_BUTTON_PLAY,
    LED_PLAY,
    LED_STOP,
    LED_PRESET1,
    LED_PRESET2,
    LED_PRESET3,
    RED, NC,
)

##########Local constants##################

WEB_PRESET_STATES = {"StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_WEBSERVICE_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateIdle"}
LOW_POWER_STATES = {"StateIdle"}  # only Idle uses nominal voltage (9V)to reduce power consumption

##################Signal Primitives#########

spotify_connect_connected = threading.Event()  # track status Spotify connected
spotify_connect_playing = threading.Event()  # track Spotify playing
spotify_connect_available = threading.Event()  # track Spotify playing & connected

# -----------------------
web_service_active = threading.Event() # Track status web_service
web_service_active.clear() # Start-up state is no Web service

usb_present = threading.Event()
usb_present.set() # USB present to go over start-up sequence (will be updated after first message of USB service

oradio_log.info("Start backlighting")
backlighting = Backlighting()

oradio_log.info("Start MPD event monitoring")
mpd_monitor = MPDMonitor()

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
#----------GPIO clean up---------

def _gpio_in_use() -> bool:
    """
    Return True if any process has /dev/gpiochip0 or /dev/gpiochip1 open.
    Uses 'fuser' which exits 0 when there are users, 1 when none.
    If 'fuser' isn't available, assume 'not in use' (best-effort).
    """
    if shutil.which("fuser") is None:
        return False

    for dev in ("/dev/gpiochip0", "/dev/gpiochip1"):
        res = subprocess.run(
            ["fuser", "-s", dev],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            return True
    return False


def _demand_free_gpio_or_exit() -> None:
    """Exit with a helpful message if gpiochips are busy."""
    if _gpio_in_use():
        oradio_log.error(
            "GPIO is busy (/dev/gpiochip* in use). "
            "Stop that process first, e.g.:\n"
            "  sudo fuser -v /dev/gpiochip0 /dev/gpiochip1\n"
            "  sudo fuser -k /dev/gpiochip0 /dev/gpiochip1\n"
            "Or kill the previous test process (thonny, old oradio_control, etc.)."
        )
        sys.exit(1)


# Run this *before* instantiating LEDControl()
_demand_free_gpio_or_exit()

# --------Instantiate  led control
leds = LEDControl()

# ----------------------State Machine------------------

class StateMachine:
    """Core Oradio application state machine: manages transitions between
    playback, presets, USB presence, web service, and networking states.
    """

    def __init__(self):
        self.state = "StateStartUp"
        self.prev_state = None
        self.task_lock = threading.Lock()
        self._websvc = None  # injected WebService
        self._pd_mode = None  # track power supply PD state "nom" or "max"

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
        self._delayed_timers = {}  # key -> Timer

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
        spotify_connect.pause()
        play_sound(SOUND_PLAY)

    def _state_preset1(self):
        leds.turn_on_led(LED_PRESET1)
        mpd_control.play(preset="Preset1")
        play_sound(SOUND_PRESET1)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.pause()

    def _state_preset2(self):
        leds.turn_on_led(LED_PRESET2)
        mpd_control.play(preset="Preset2")
        play_sound(SOUND_PRESET2)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.pause()

    def _state_preset3(self):
        leds.turn_on_led(LED_PRESET3)
        mpd_control.play(preset="Preset3")
        play_sound(SOUND_PRESET3)
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        spotify_connect.pause()

    def _state_stop(self):
        leds.oneshot_on_led(LED_STOP, 4)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        spotify_connect.pause()
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
        spotify_connect.play()
        play_sound(SOUND_SPOTIFY)

    def _state_play_song_webif(self):
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        else:
            leds.turn_on_led(LED_PLAY)
        spotify_connect.pause()
        mpd_control.play()
        play_sound(SOUND_PLAY)

    def _state_usb_absent(self):
        leds.control_blinking_led(LED_STOP, 0.7)
        mpd_control.stop()
        spotify_connect.pause()
        play_sound(SOUND_STOP)
        play_sound(SOUND_NO_USB)
        if web_service_active.is_set():
            oradio_web_service.stop()

    def _state_startup(self):
        leds.control_blinking_led(LED_STOP, 1)
        oradio_log.debug("Starting-up")
        mpd_control.pause()
        spotify_connect.pause()
        play_sound(SOUND_START)
        oradio_log.debug("Startup: scheduling transition to Idle in 5 s")
        self._arm_delayed_transition("StartupToIdle", 5.0, "StateIdle")

    def _state_idle(self):
        # Listen for volume changed notifications
        volume_control.set_notify()
        if web_service_active.is_set():
            leds.control_blinking_led(LED_PLAY)
        if mpd_control.is_webradio():
            mpd_control.stop()
        else:
            mpd_control.pause()
        spotify_connect.pause()
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
    spotify_connect.pause()  # spotify is on pause and will not work
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


def on_web_pl_webradio_changed():
    """Handle WebService: Webradio playlist changed."""
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

HANDLERS = {
    MESSAGE_VOLUME_SOURCE: {
        MESSAGE_VOLUME_CHANGED: on_volume_changed,
    },
    MESSAGE_USB_SOURCE: {
        STATE_USB_ABSENT: on_usb_absent,
        STATE_USB_PRESENT: on_usb_present,
        # "USB error": on_usb_error,
    },
    MESSAGE_WIFI_SOURCE: {
        STATE_WIFI_IDLE: on_wifi_not_connected,
        STATE_WIFI_CONNECTED: on_wifi_connected,
        STATE_WIFI_ACCESS_POINT: on_wifi_access_point,
        MESSAGE_WIFI_FAIL_CONNECT: on_wifi_fail_connect,
    },
    MESSAGE_WEB_SERVICE_SOURCE: {
        STATE_WEB_SERVICE_IDLE: on_webservice_idle,
        STATE_WEB_SERVICE_ACTIVE: on_webservice_active,
        MESSAGE_WEB_SERVICE_PLAYING_SONG: on_webservice_playing_song,
        MESSAGE_WEB_SERVICE_PL1_CHANGED: on_webservice_pl1_changed,
        MESSAGE_WEB_SERVICE_PL2_CHANGED: on_webservice_pl2_changed,
        MESSAGE_WEB_SERVICE_PL3_CHANGED: on_webservice_pl3_changed,
        MESSAGE_WEB_SERVICE_PL_WEBRADIO: on_web_pl_webradio_changed,
    },
    MESSAGE_SPOTIFY_SOURCE: {
        SPOTIFY_CONNECT_CONNECTED_EVENT: on_spotify_connect_connected,
        SPOTIFY_CONNECT_DISCONNECTED_EVENT: on_spotify_connect_disconnected,
        SPOTIFY_CONNECT_PLAYING_EVENT: on_spotify_connect_playing,
        SPOTIFY_CONNECT_PAUSED_EVENT: on_spotify_connect_paused,
        # "Spotify error": on_spotify_error,
    },
    MESSAGE_BUTTON_SOURCE: {
        MESSAGE_SHORT_PRESS_BUTTON_PLAY: _on_play_pressed,
        MESSAGE_SHORT_PRESS_BUTTON_STOP: _on_stop_pressed,
        MESSAGE_SHORT_PRESS_BUTTON_PRESET1: _on_preset1_pressed,
        MESSAGE_SHORT_PRESS_BUTTON_PRESET2: _on_preset2_pressed,
        MESSAGE_SHORT_PRESS_BUTTON_PRESET3: _on_preset3_pressed,
        MESSAGE_LONG_PRESS_BUTTON_PLAY: _on_play_long_pressed
    },

}


def handle_message(message: dict):
    '''
    handle the received message
    :arguments
        message (dict) : the (Oradio) message to be processed 
    '''
    validated_message = validate_oradio_message(message)
    if validated_message:
        command_source  = validated_message.source
        state           = validated_message.state
        error           = validated_message.error

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

        if error and error != MESSAGE_NO_ERROR:
            if handler := handlers.get(error):
                handler()
            else:
                oradio_log.warning(
                    "Unhandled error '%s' for message source '%s'.", error, command_source
                )
    else:
        print(f"{RED}Invalid OradioMessage received {NC}")

# 3)----------- Process the messages---------
def process_messages(msg_queue):
    """Continuously read and handle messages from the shared queue."""
    while True:
        msg = msg_queue.get()  # blocking
        oradio_log.debug("Received message in Queue: %r", msg)
        handle_message(msg)

#-------------USB presence sync at start -up---------------------------------------

def sync_usb_presence_from_service():
    """
    One time sync at start-up
    """
    state = oradio_usb_service.get_state()
    oradio_log.info("USB service raw state: %r", state)

    if state == STATE_USB_PRESENT:
        usb_present.set()
        oradio_log.info("USB presence synced: present")
    elif state == STATE_USB_ABSENT:
        usb_present.clear()
        oradio_log.info("USB presence synced: absent")
    else:
        oradio_log.warning("Unexpected USB service state: %r", state)


# ------------------Start-up - instantiate and define other modules ---------------

shared_queue = Queue()  # Create a shared queue

# Instantiate the state machine
state_machine = StateMachine()

#REVIEW Onno: Gebruik shared queue om remote commando's door oradio control te laten doen, inclusief feedback naar gebruiker
# Instantiate remote monitor managing the heartbeat and sys_info messages when wifi state changes
remote_monitor = RMService()

# Instantiate spotify
spotify_connect = SpotifyConnect(shared_queue)

# Initialize the oradio_usb class
oradio_usb_service = USBService(shared_queue)
# sync the usb_present tracker
sync_usb_presence_from_service()

touch_buttons = TouchButtons(shared_queue)
# ----------- Volume Control -----------------

volume_control = VolumeControl(shared_queue)

# ---------Initialize the web_service---------
oradio_web_service = WebService(shared_queue)

# inject the services into the Statemachine
state_machine.set_services(oradio_web_service)

# start the state_machine transition
state_machine.transition("StateStartUp")

# instantiate the process messages
threading.Thread(
    target=process_messages, args=(shared_queue,), daemon=True
).start()
