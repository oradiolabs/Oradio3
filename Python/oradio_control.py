#!/usr/bin/env python3
# pylint: disable=missing-function-docstring
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

Update for 0.4.0: OradioAP mode
Update the State machine and added a standard Stress test for statemachine
Added networking statemachine, to handle various states wifi and Web service

"""
import time
import threading
from multiprocessing import Queue
import signal

# For stress test
import argparse
import random
import sys

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl

# from mpd_control import MPDControl
from mpd_control import get_mpd_control

from led_control import LEDControl
from play_system_sound import PlaySystemSound
from touch_buttons import TouchButtons
from remote_monitoring import RmsService
from spotify_connect_direct import SpotifyConnect
from usb_service import USBService
from web_service import WebService
#OMJ: Zie regel 707-714
#from wifi_service import WifiService

##### GLOBAL constants ####################
#from oradio_const import *
from oradio_const import (
    MESSAGE_NO_ERROR,
    MESSAGE_SPOTIFY_TYPE,
    MESSAGE_STATE_CHANGED,
    MESSAGE_TYPE_VOLUME,
    MESSAGE_USB_TYPE,
    MESSAGE_WEB_SERVICE_PL1_CHANGED,
    MESSAGE_WEB_SERVICE_PL2_CHANGED,
    MESSAGE_WEB_SERVICE_PL3_CHANGED,
    MESSAGE_WEB_SERVICE_PL_WEBRADIO,
    MESSAGE_WEB_SERVICE_PLAYING_SONG,
    MESSAGE_WEB_SERVICE_TYPE,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_WIFI_TYPE,
    SPOTIFY_CONNECT_CONNECTED_EVENT,
    SPOTIFY_CONNECT_DISCONNECTED_EVENT,
    SPOTIFY_CONNECT_PAUSED_EVENT,
    SPOTIFY_CONNECT_PLAYING_EVENT,
    STATE_USB_ABSENT,
    STATE_USB_PRESENT,
    STATE_WEB_SERVICE_ACTIVE,
    STATE_WEB_SERVICE_IDLE,
    STATE_WIFI_ACCESS_POINT,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_INTERNET,
    STATE_WIFI_IDLE,
)

##########Local constants##################

WEB_PRESET_STATES = {"StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"}
PLAY_WEBSERVICE_STATES = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateIdle"}

##################Signal Primitives#########
# Signal primitives, to track the states of various status
# These are checked at every state change (button press), using this instead of get.state() is faster
# And are based upon only the messages and no polling
# Tested .get_state , especially for wifi service, it can take up to 300 - 500 ms

spotify_connect_connected = threading.Event()  # track status Spotify connected
spotify_connect_playing = threading.Event()  # track Spotify playing
spotify_connect_available = threading.Event()  # track Spotify playing & connected

# -----------------------
web_service_active = threading.Event() # Track status web_service
web_service_active.clear() # Start-up state is no Web service

internet_connected = threading.Event() # Track status wifi internet
internet_connected.clear()  # Start-up state is no wifi and Internet

usb_present = threading.Event()
usb_present.set() # USB present to go over start-up sequence (will be updated after first message of USB service

# Instantiate remote monitor
remote_monitor = RmsService()

# Instantiate MPDControl
mpd = get_mpd_control()

# Instantiate  led control
leds = LEDControl()
# Instantiate sound player
sound_player = PlaySystemSound()


# ----------------------State Machine------------------


class StateMachine:
    def __init__(self):
        """
        Core application state machine for Oradio, managing transitions between
        playback, presets, USB insertion/removal, web service, and networking.
        """
        self.state = "StateStartUp"
        self.prev_state = None

        self.task_lock = threading.Lock()
        # placeholders until you wire them in
        self._websvc = None

    def set_services(self, web_service):
        """
        Inject the (already‐constructed) WebService.
        """
        self._websvc = web_service
        # to start via long-press the webservice indepently from the statemachine
    def start_webservice(self):
        """
        Trigger the injected WebService to start by long-press, but only if USB is present.
        """
        ws = self._websvc
        if ws is None:
            # You tried to start before set_services() was called
            return
        # Guard: only start webservice when USB is present
        if not usb_present.is_set():
            oradio_log.warning("WebService start blocked (USB absent)")
            return
        # Good to go
        if web_service_active.is_set():
            oradio_log.debug("WebService is already active")
            sound_player.play("OradioAPstarted")  # play again to confirm
        else:
            oradio_log.debug("Starting WebService: %r", ws)
            leds.control_blinking_led("LEDPlay", 2)
            ws.start()

    def transition(self, requested_state):

        oradio_log.debug(
            "Request Transitioning from %s to %s", self.state, requested_state
        )
        # ————————————————————————————————
        # 1. SAME-STATE “NEXT SONG” SHORTCUT
        # ————————————————————————————————

        if self.state == requested_state and requested_state in PLAY_STATES:
            if not mpd.current_is_webradio() and mpd.current_queue_filled():
                threading.Thread(target=mpd.next).start()
                sound_player.play("Next")
                oradio_log.debug("Next song")
                return

        # ————————————————————————————————
        # 2. SPOTIFY-CONNECT REDIRECT
        # ————————————————————————————————
        if spotify_connect_available.is_set() and requested_state == "StatePlay":
            oradio_log.debug(
                "Spotify Connect active → redirecting to StateSpotifyConnect"
            )
            requested_state = "StateSpotifyConnect"

        # ————————————————————————————————
        # 3. SWITCH OFF WEB SERVICE IN AP MODE
        # ————————————————————————————————

        if requested_state == "StateStop" and web_service_active.is_set():
            oradio_web_service.stop()
            return

        # ————————————————————————————————
        # 4. BLOCK WEBRADIO PRESETS WHEN NO INTERNET
        # ————————————————————————————————

        if requested_state not in WEB_PRESET_STATES:
            pass  # normal flow
        elif not internet_connected.is_set():
            preset_key = requested_state[len("State"):]
            if mpd.preset_is_webradio(preset_key):
                oradio_log.info("Webradio blocked no Internet")
                threading.Timer(2, sound_player.play, args=("NoInternet",)).start()
                return

        # ————————————————————————————————
        # 5. USB PRESENCE GUARD + COMMIT (via USB_Media)
        # ————————————————————————————————
    #    if usb_present.is_set() or requested_state == "StateStartUp": # to insure start-up
        if usb_present.is_set(): # to insure start-up
            # USB is present → commit the requested state
            self.prev_state = self.state
            self.state = requested_state
            oradio_log.debug("State changed: %s → %s", self.prev_state, self.state)
        else:
            # USB absent → block and force the USB‐absent state
            oradio_log.info("Transition to %s blocked (USB absent)", requested_state)
            if self.state != "StateUSBAbsent":
                self.prev_state = self.state
                self.state = "StateUSBAbsent"
                oradio_log.debug("State set to StateUSBAbsent")

        # ————————————————————————————————
        # 6. SPAWN THREAD FOR THE ACTUAL STATE
        # ————————————————————————————————
        state_to_handle = self.state
        threading.Thread(
            target=self.run_state_method, args=(state_to_handle,), daemon=True
        ).start()

    def run_state_method(self, state_to_handle):

        with self.task_lock:
            leds.turn_off_all_leds()

            if state_to_handle == "StatePlay":
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                else:
                    leds.turn_on_led("LEDPlay")
                mpd.play()
                spotify_connect.pause()
                sound_player.play("Play")

            elif state_to_handle == "StatePreset1":
                leds.turn_on_led("LEDPreset1")
                mpd.play_preset("Preset1")
                sound_player.play("Preset1")
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset2":
                leds.turn_on_led("LEDPreset2")
                mpd.play_preset("Preset2")
                sound_player.play("Preset2")
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset3":
                leds.turn_on_led("LEDPreset3")
                mpd.play_preset("Preset3")
                sound_player.play("Preset3")
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StateStop":
                leds.turn_on_led_with_delay("LEDStop", 4)
                # smart‐pause: if this is a web-radio stream,
                # use STOP (clears MPD buffer) instead of PAUSE
                if mpd.current_is_webradio():
                    mpd.stop()
                else:
                    mpd.pause()
                spotify_connect.pause()
                sound_player.play("Stop")

            elif state_to_handle == "StateSpotifyConnect":
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                else:
                    leds.turn_on_led("LEDPlay")
                if mpd.current_is_webradio():
                    mpd.stop()
                else:
                    mpd.pause()
                spotify_connect.play()
                sound_player.play("Spotify")

            elif state_to_handle == "StatePlaySongWebIF":
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                else:
                    leds.turn_on_led("LEDPlay")
                spotify_connect.pause()
                mpd.play()
                sound_player.play("Play")

            elif state_to_handle == "StateUSBAbsent":
                leds.control_blinking_led("LEDStop", 0.7)
                mpd.stop()
                spotify_connect.pause()
                sound_player.play("Stop")
                sound_player.play("NoUSB")
                if web_service_active.is_set():
                    oradio_web_service.stop()

            elif state_to_handle == "StateStartUp":
                leds.control_blinking_led("LEDStop", 1)
                oradio_log.debug("Starting-up")
                mpd.pause()
                spotify_connect.pause()
#                mpd.start_update_mpd_database_thread()
                time.sleep(2)
                sound_player.play("StartUp")
                time.sleep(3)
                oradio_log.debug("Starting-up Completed")
                self.transition("StateIdle")

            elif state_to_handle == "StateIdle":
                if web_service_active.is_set():
                    leds.control_blinking_led("LEDPlay", 2)
                if mpd.current_is_webradio():
                    mpd.stop()
                else:
                    mpd.pause()
                spotify_connect.pause()
                oradio_log.debug("In Idle state, wait for next step")

            elif state_to_handle == "StateError":
                leds.control_blinking_led("LEDStop", 2)


# -------------Messages handler: -----------------

# 1) Functions which define the actions for the messages

# -------------------VOLUME-----------------------


def on_volume_changed():
    if state_machine.state in ("StateStop", "StateIdle"):
        state_machine.transition(
            "StatePlay"
        )  # Switch Oradio in Play when Volume buttons is turned


# -------------------USB---------------------------


def on_usb_absent():
    oradio_log.info("USB absent acknowlegded")
    if not usb_present.is_set():
        return
    usb_present.clear()
    mpd.cancel_update()  # cancel if MPD database update runs
    if state_machine.state != "StateStartUp":
        state_machine.transition("StateUSBAbsent")


def on_usb_present():
    oradio_log.info("USB present acknowledged")
    if usb_present.is_set():
        return
    usb_present.set()
    sound_player.play("USBPresent")
    mpd.start_update_mpd_database_thread()  # MPD database update
    # Transition to Idle after USB is inserted
    if state_machine.state != "StateStartUp":
        state_machine.transition("StateIdle")

# -------------------WIFI--------------------------

# Messages when after the closure of the Oradio AP Webservice the Wifi connection is/not made


def on_wifi_connected_to_internet():
    oradio_log.info("Wifi is connected to internet acknowledged")
#     if internet_connected.is_set():
#         return  # already marked disconnected; nothing to do
    internet_connected.set()

    if state_machine.state in PLAY_WEBSERVICE_STATES:  # If in play states,
        threading.Timer(
            4, sound_player.play, args=("WifiConnected",)
        ).start()

    remote_monitor.send_sys_info()
    # Send heartbeat every hour to Remote Monitoring Service
    remote_monitor.heartbeat_start()

def on_wifi_fail_connect():
    oradio_log.info("Wifi fail connect acknowledged")
#     if not internet_connected.is_set():
#         return  # already marked disconnected; nothing to do
    internet_connected.clear()
    if state_machine.state in PLAY_WEBSERVICE_STATES:  # If in play states,
        sound_player.play("WifiNotConnected")
    remote_monitor.heartbeat_stop()  # in all other cases, stop sending heartbeat

def on_wifi_access_point():
    oradio_log.info("Configured as access point acknowledged")
#    on_wifi_fail_connect() # do same actions as on_wifi_fail_connect

def on_wifi_connected_no_internet():
    oradio_log.info("Wifi is connected NO internet acknowledged")
#    on_wifi_fail_connect() # do same actions as on_wifi_fail_connect

def on_wifi_not_connected():
    oradio_log.info("Wifi is NOT connected acknowledged")
#    on_wifi_fail_connect() # do same actions as on_wifi_fail_connect

# -------------------WEB---------------------------

def on_webservice_active():
    oradio_log.info("WebService active is acknowledged")
    if web_service_active.is_set(): # check already taken the actions
        return
    web_service_active.set()
    internet_connected.clear()
    leds.control_blinking_led("LEDPlay", 2)
    sound_player.play("OradioAPstarted")
    # handle Webradio and Spotify
    if mpd.current_is_webradio() or state_machine.state == "StateSpotifyConnect":
        state_machine.transition("StateIdle")
        oradio_log.info("Stopped WebRadio and Spotify playback on Webservice entry")

def on_webservice_idle():
    oradio_log.info("WebService idle is acknowledged")
    if not web_service_active.is_set(): # check already taken the actions
        return
    web_service_active.clear()
    if state_machine.state == "StatePlay":
        leds.turn_on_led("LEDPlay")
    else:
        leds.control_blinking_led("LEDPlay", 0)
    sound_player.play("OradioAPstopped")

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
    threading.Timer(2, sound_player.play, args=("NewPlaylistPreset",)).start()
    oradio_log.debug("WebService on_webservice_pl1_changed acknowledged")


def on_webservice_pl2_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset2")
    threading.Timer(2, sound_player.play, args=("NewPlaylistPreset",)).start()
    oradio_log.debug("WebService on_webservice_pl2_changed acknowledged")


def on_webservice_pl3_changed():
    state_machine.transition("StateIdle")
    state_machine.transition("StatePreset3")
    threading.Timer(2, sound_player.play, args=("NewPlaylistPreset",)).start()
    oradio_log.debug("WebService on_webservice_pl3_changed acknowledged")


def on_webservice_pl_web_radio_changed():
    #   state_machine.transition("StateIdle")
    threading.Timer(2, sound_player.play, args=("NewPlaylistWebradio",)).start()
    oradio_log.debug("WebService on_webservice_pl_web_radio_changed acknowledged")


# -------------------SPOTIFY-----------------------


def on_spotify_connect_connected():
    spotify_connect_connected.set()  # Signal that spotify_connected is active
    update_spotify_connect_available()
    oradio_log.debug("Spotify active is acknowledged")


def on_spotify_connect_disconnected():
    spotify_connect_connected.clear()  # Signal that spotify_connected is inactive
    update_spotify_connect_available()
    oradio_log.debug("Spotify inactive is acknowledged")


# Both can switch the Oradio remotely, which is not in line with "in Control"
def on_spotify_connect_playing():
    spotify_connect_connected.set()  # Signal that spotify_connected is active
    spotify_connect_playing.set()
    update_spotify_connect_available()
    oradio_log.debug("Spotify playing is acknowledged")


def on_spotify_connect_paused():
    spotify_connect_connected.set()  # Signal that spotify_connected is active
    spotify_connect_playing.clear()
    update_spotify_connect_available()
    oradio_log.debug("Spotify paused is acknowledged")


def on_spotify_connect_stopped():
    spotify_connect_playing.clear()
    update_spotify_connect_available()  # simular as stopped
    oradio_log.debug("Spotify stopped is acknowledged")


def on_spotify_connect_changed():
    # TBD action
    oradio_log.debug("Spotify changed is acknowledged")


def update_spotify_connect_available():
    """
    Sets spotify_connect_available if both spotify_connect_connected and spotify_connect_playing
    are set. Otherwise, clears spotify_connect_available.
    After execution, logs the state of all three events.
    """
    if spotify_connect_connected.is_set() and spotify_connect_playing.is_set():
        spotify_connect_available.set()  # When this is the case, the ON button becomes Spotify Button
        if state_machine.state in (
            "StatePlay"
        ):  # if Spotify connect is  avalaible Switch to
            state_machine.transition("StateSpotifyConnect")  # Switch to Spotify Connect
    else:
        spotify_connect_available.clear()
        if (
            state_machine.state == "StateSpotifyConnect"
        ):  # if Spotify connect is not avalaible
            state_machine.transition("StateStop")  # Switch of as Spotify stops

    oradio_log.info(
        "Spotify Connect States - Connected: %s, Playing: %s, Available: %s",
        spotify_connect_connected.is_set(),
        spotify_connect_playing.is_set(),
        spotify_connect_available.is_set(),
    )


# 2)-----The Handler map, defining message content and the handler funtion---

HANDLERS = {
    MESSAGE_TYPE_VOLUME: {
        MESSAGE_STATE_CHANGED: on_volume_changed,
        # "Volume error": on_volume_error,
    },
    MESSAGE_USB_TYPE: {
        STATE_USB_ABSENT: on_usb_absent,
        STATE_USB_PRESENT: on_usb_present,
        # "USB error": on_usb_error,
    },
    MESSAGE_WIFI_TYPE: {
        STATE_WIFI_IDLE: on_wifi_not_connected,
        STATE_WIFI_INTERNET: on_wifi_connected_to_internet,
        STATE_WIFI_CONNECTED: on_wifi_connected_no_internet,
        STATE_WIFI_ACCESS_POINT: on_wifi_access_point,
        MESSAGE_WIFI_FAIL_CONNECT: on_wifi_fail_connect,
    },
    MESSAGE_WEB_SERVICE_TYPE: {
        STATE_WEB_SERVICE_IDLE: on_webservice_idle,
        STATE_WEB_SERVICE_ACTIVE: on_webservice_active,
        MESSAGE_WEB_SERVICE_PLAYING_SONG: on_webservice_playing_song,
        MESSAGE_WEB_SERVICE_PL1_CHANGED: on_webservice_pl1_changed,
        MESSAGE_WEB_SERVICE_PL2_CHANGED: on_webservice_pl2_changed,
        MESSAGE_WEB_SERVICE_PL3_CHANGED: on_webservice_pl3_changed,
        MESSAGE_WEB_SERVICE_PL_WEBRADIO: on_webservice_pl_web_radio_changed,
        # "Webservice error": on_webservice_error,
    },
    MESSAGE_SPOTIFY_TYPE: {
        SPOTIFY_CONNECT_CONNECTED_EVENT: on_spotify_connect_connected,
        SPOTIFY_CONNECT_DISCONNECTED_EVENT: on_spotify_connect_disconnected,
        SPOTIFY_CONNECT_PLAYING_EVENT: on_spotify_connect_playing,
        SPOTIFY_CONNECT_PAUSED_EVENT: on_spotify_connect_paused,
        # "Spotify error": on_spotify_error,
    },
}


def handle_message(message):
    command_type = message.get("type")
    state = message.get("state")
    error = message.get("error", None)

    handlers = HANDLERS.get(command_type)
    if handlers is None:
        oradio_log.warning("Unhandled message type: %s", message)
        return

    if handler := handlers.get(state):
        handler()
    else:
        oradio_log.warning(
            "Unhandled state '%s' for message type '%s'.", state, command_type
        )

    if error and error != MESSAGE_NO_ERROR:
        if handler := handlers.get(error):
            handler()
        else:
            oradio_log.warning(
                "Unhandled error '%s' for message type '%s'.", error, command_type
            )

# 3)----------- Process the messages---------
def process_messages(queue):
    try:
        while True:
            msg = queue.get()
            oradio_log.debug("Received message in Queue: %s", msg)
            handle_message(msg)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        oradio_log.error("Unexpected error in process_messages: %s", ex)

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
'''
#------------Monitor Internet, if still a connection has been made-----------------
def start_wifi_monitor(interval: float = 5.0):
    """
    Start a daemon thread polling `oradio_wifi_service.get_state()` every `interval` seconds
    (float, in seconds—e.g., 0.5 for half-second) to keep `internet_connected` synced.
    Sets the event on STATE_WIFI_INTERNET, clears it otherwise. Polling errors are logged but
    don’t override the last known state. Uncomment the debug line to see raw state and flag.
    This insures that in case of recovery via a stored credentials the internet is restored
    """
    def _worker():
        while True:
            try:
                state = oradio_wifi_service.get_state()
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Error polling Wi-Fi service state: %s", ex_err)
                state = None
            # debug/status line—can
#             oradio_log.info(
#                 "Wi-Fi monitor status: service state=%r, internet_connected=%s",
#                 state,
#                 internet_connected.is_set(),
#             )
            if state == STATE_WIFI_INTERNET:
                if not internet_connected.is_set():
                    internet_connected.set()
                    oradio_log.info("Detected internet access via Wi-Fi; marked connected")
            else:
                if internet_connected.is_set():
                    internet_connected.clear()
                    oradio_log.info("Wi-Fi no longer has internet; marked disconnected")
            time.sleep(interval)

    threading.Thread(
        target=_worker,
        daemon=True,
        name="WiFiInternetMonitor",
    ).start()
'''
# ------------------Start-up - instantiate and define other modules ---------------

shared_queue = Queue()  # Create a shared queue

# Instantiate the state machine
state_machine = StateMachine()

# Instantiate spotify
spotify_connect = SpotifyConnect(shared_queue)

# Initialize the oradio_usb class
oradio_usb_service = USBService(shared_queue)
# sync the usb_present tracker
sync_usb_presence_from_service()

# Initialize TouchButtons and pass the state machine
touch_buttons = TouchButtons(state_machine)

# Initialize the volume_control, works stand alone, getting messages via the shared_queue
volume_control = VolumeControl(shared_queue)

# #Initialize the web_service
oradio_web_service = WebService(shared_queue)

#OMJ: Wifi service wordt alleen gebruikt voor de wifi monitor
#     web service geeft de wifi messages door.
#Initialize the wifi_service
#oradio_wifi_service = WifiService(shared_queue)

# Start background polling (every 5 seconds) of the Wi-Fi service state.
#OMJ: Overbodig, want wifi state change messages komen vna wifi service, doorgegeven door web service --> Zie 
#start_wifi_monitor()

# inject the services into the Statemachine
state_machine.set_services(oradio_web_service)

# start the state_machine transition
state_machine.transition("StateStartUp")

# instantiate the process messages
threading.Thread(
    target=process_messages, args=(shared_queue,), daemon=True
).start()

# ——————————————————————————————
# STRESS-TEST SETUP
# ——————————————————————————————
# Use only in case of testing this script, it runs on top of the excisting controls
# start with  python oradio_control.py --stress (or with additional arguments
# like --min-delay 0.2 --max-delay 0.5 --max-transitions 10
# After stress test, Oradio need to function, without bugs and errors


STATES = [
    "StatePlay",
    "StatePreset1",
    "StatePreset2",
    "StatePreset3",
    "StateStop",
    "StateSpotifyConnect",
    "StatePlaySongWebIF",
    "StateUSBAbsent",
    "StateStartUp",
    "StateIdle",
    "StateWebService",
]

# Event that, when set, signals the stress loop to stop cleanly
_stress_stop = threading.Event()

# Holds the Thread object running the stress loop once it's started;
# remains None until maybe_start_stress() launches it.
_stress_thread = None # pylint: disable=invalid-name

# Simple integer counter tracking how many transitions have been fired
_stress_count = 0 # pylint: disable=invalid-name

# Lock to synchronize increments of _stress_count between the stress and main threads
_stress_count_lock = threading.Lock()


def _stress_loop(min_d, max_d, max_count):
    global _stress_count # pylint: disable=global-statement
    while not _stress_stop.is_set():
        nxt = random.choice(STATES)
        with _stress_count_lock:
            _stress_count += 1
            cnt = _stress_count

        print(f"[STRESS #{cnt}] → {nxt}   (threads: {threading.active_count()})")
        oradio_log.info("[STRESS #%s] → %s", cnt, nxt)

        state_machine.transition(nxt)

        # if we've hit the user-supplied maximum, stop
        if max_count is not None and cnt >= max_count:
            print(
                f"\n🛑 Reached max_transitions ({max_count}). Stopping stress test 🛑"
            )
            _stress_stop.set()
            break

        time.sleep(random.uniform(min_d, max_d))

    # final summary once the loop exits (whether by Q or max)
    print(f"\n✅ Stress test finished. Total transitions: {_stress_count}")
    print(f"   Active threads now: {threading.active_count()}")


def maybe_start_stress():
    """
    If --stress is passed, start the storm with optional max-transitions,
    and a Q-watcher that stops it early.
    """
    global _stress_thread # pylint: disable=global-statement

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--stress",
        action="store_true",
        help="hammer the state machine with random transitions",
    )
    parser.add_argument(
        "--min-delay", type=float, default=0.1, help="minimum delay between transitions"
    )
    parser.add_argument(
        "--max-delay", type=float, default=0.5, help="maximum delay between transitions"
    )
    parser.add_argument(
        "--max-transitions",
        type=int,
        default=20,
        help="stop automatically after this many transitions",
    )
    args, _ = parser.parse_known_args()

    if not args.stress:
        return

    print("🔥 Starting STRESS test 🔥")
    print("Press Q + Enter at any time to stop the stress test early.\n")

    # 1) spin up the stress loop (passing max-transitions)
    _stress_thread = threading.Thread(
        target=_stress_loop,
        args=(args.min_delay, args.max_delay, args.max_transitions),
        daemon=True,
    )
    _stress_thread.start()

    # 2) watch for Q+Enter to stop early
    def _stop_on_q():
        while not _stress_stop.is_set():
            line = sys.stdin.readline().strip().lower()
            if line == "q":
                print("\n🛑 Stopping STRESS test early via Q 🛑")
                _stress_stop.set()
                return

    threading.Thread(target=_stop_on_q, daemon=True).start()


# ---END Stress test part

def main():
    try:
        oradio_log.debug("Oradio control main loop running")
        while True:
            time.sleep(1)  # Main loop
    except KeyboardInterrupt:
        oradio_log.debug("KeyboardInterrupt detected. Exiting...")
    finally:
        try:
            touch_buttons.cleanup()
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Error cleaning up touch_buttons: %s", ex_err)

if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # start stress test if requested via the arguments --stress, otherwise simply skipped
    maybe_start_stress()

    # then hand over to your normal main loop
    main()

    # Close using signal to stop threads
    signal.raise_signal(signal.SIGTERM)
