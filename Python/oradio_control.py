#!/usr/bin/env python3
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
import subprocess
import os
import signal

# For stress test
import argparse
import random
import sys

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl
from mpd_control import MPDControl
from led_control import LEDControl
from play_system_sound import PlaySystemSound
from touch_buttons import TouchButtons
from remote_monitoring import RmsService

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

# Instantiate remote monitor
remote_monitor = RmsService()

# Use the spotify_connect_direct
from spotify_connect_direct import SpotifyConnect

spotify_connect_connected = threading.Event() # track status Spotify connected
spotify_connect_playing = threading.Event() # track Spotify playing
spotify_connect_available = threading.Event() # track Spotify playing & connetyec

#-----------------------
from usb_service import USBService
from web_service import WebService
from wifi_service import WifiService

usb_present_event = threading.Event() # track status USB


# Instantiate MPDControl
mpd = MPDControl()
# Instantiate  led control
leds = LEDControl()
# Instantiate sound player
sound_player = PlaySystemSound()


#----------------------State Machine------------------

class StateMachine:
    def __init__(self):
        self.state = "StateStartUp"
        self.task_lock = threading.Lock()

    def transition(self, requested_state):
        oradio_log.debug("Request Transitioning from %s to %s", self.state, requested_state)

        # ————————————————————————————————
        # 1. SAME-STATE “NEXT SONG” SHORTCUT
        # ————————————————————————————————
        play_states = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"}
        if self.state == requested_state and requested_state in play_states:
            if not mpd.current_is_webradio():
                threading.Thread(target=mpd.next).start()
                sound_player.play("Next")
                oradio_log.debug("Next song")
            return

        # ————————————————————————————————
        # 2. SPOTIFY-CONNECT REDIRECT
        # ————————————————————————————————
        if spotify_connect_available.is_set() and requested_state == "StatePlay":
            oradio_log.debug("Spotify Connect active → redirecting to StateSpotifyConnect")
            requested_state = "StateSpotifyConnect"

        # ————————————————————————————————
        # 3. USB PRESENCE GUARD + COMMIT
        # ————————————————————————————————
        if usb_present_event.is_set():
            old = self.state
            self.state = requested_state
            oradio_log.debug("State changed: %s → %s", old, self.state)
        else:
            oradio_log.warning("Transition to %s blocked (USB absent)", requested_state)
            if self.state != "StateUSBAbsent":
                self.state = "StateUSBAbsent"
                oradio_log.debug("State set to StateUSBAbsent")

        # ————————————————————————————————
        # 4. SPAWN THREAD FOR THIS REQUEST
        # ————————————————————————————————
        state_to_handle = requested_state
        threading.Thread(
            target=self.run_state_method,
            args=(state_to_handle,)
        ).start()



    def run_state_method(self, state_to_handle):

        with self.task_lock:
            leds.turn_off_all_leds()

            if state_to_handle == "StatePlay":
                if network_mgr.state == "APWebservice":
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
                if mpd.preset_is_webradio("Preset1") and network_mgr.state != "Internet":
                    threading.Timer(1.5, sound_player.play, args=("NoInternet",)).start() # delay prevent mixing
                if network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset2":
                leds.turn_on_led("LEDPreset2")
                mpd.play_preset("Preset2")
                sound_player.play("Preset2")
                if mpd.preset_is_webradio("Preset2") and network_mgr.state != "Internet":
                    threading.Timer(1.5, sound_player.play, args=("NoInternet",)).start() # delay prevent mixing
                if network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset3":
                leds.turn_on_led("LEDPreset3")
                mpd.play_preset("Preset3")
                sound_player.play("Preset3")
                if mpd.preset_is_webradio("Preset3") and network_mgr.state != "Internet":
                    threading.Timer(1.5, sound_player.play, args=("NoInternet",)).start() # delay prevent mixing
                if network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StateStop":
                leds.turn_on_led_with_delay("LEDStop", 4)
                mpd.pause()
                spotify_connect.pause()
                sound_player.play("Stop")
                if network_mgr.state == "APWebservice":
                    oradio_web_service.stop()

            elif state_to_handle == "StateSpotifyConnect":
                if network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                else:
                    leds.turn_on_led("LEDPlay")
                mpd.pause()
                spotify_connect.play()
                sound_player.play("Spotify")

            elif state_to_handle == "StatePlaySongWebIF":
                if network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                else:
                    leds.turn_on_led("LEDPlay")
                spotify_connect.pause()
                mpd.play()
                sound_player.play("Play")

            elif state_to_handle == "StateUSBAbsent":
                leds.control_blinking_led("LEDStop", 0.7)
                mpd.pause()
                spotify_connect.pause()
                sound_player.play("Stop")
                sound_player.play("NoUSB")
                if network_mgr.state == "APWebservice":
                    oradio_web_service.stop()
                    leds.control_blinking_led("LEDPlay", 0)
                self.wait_for_usb_present()
                self.transition("StateIdle")

            elif state_to_handle == "StateStartUp":
                leds.control_blinking_led("LEDStop", 1)
                oradio_log.debug("Starting-up")
                mpd.pause()
                spotify_connect.pause()
                mpd.start_update_mpd_database_thread()
                time.sleep(2)
                sound_player.play("StartUp")
                time.sleep(3)
                oradio_log.debug("Starting-up Completed")
                self.transition("StateIdle")

            elif state_to_handle == "StateIdle":
                mpd.pause()
                spotify_connect.pause()
                oradio_log.debug("In Idle state, wait for next step")

            elif state_to_handle == "StateWebService":
                leds.control_blinking_led("LEDPlay", 2)
                sound_player.play("Play")
                oradio_web_service.start() # to indicate that long press is confirmed
                oradio_log.debug("In WebService state, wait for next step")

            elif state_to_handle == "StateError":
                leds.control_blinking_led("LEDStop", 2)


    def wait_for_usb_present(self):
        """Waits for the USB to be present, cancels ongoing MPD updates, and restarts MPD if needed."""
        self.state = "StateWaitForUSBPresent"
        oradio_log.debug("Waiting for USB to be present...")
        # Cancel any ongoing MPD database update before waiting
        mpd.cancel_update()
        # Wait for the USB event to be set
        usb_present_event.wait()  # Blocks until the USB is inserted
        oradio_log.debug("USB is now present, checking MPD state...")
        # Restart MPD service to ensure a fresh start
        #    mpd.restart_mpd_service()  # seems not needed when the delay is used
        # Ensure MPD is ready before starting an update
        time.sleep(0.2)  # Small delay to allow MPD to recover and before start mpd update
        # Start MPD database update in a separate thread
        oradio_log.debug("Starting MPD database update...")
        sound_player.play("USBPresent")
        mpd.start_update_mpd_database_thread()

    def update_usb_event(self):
        usb_state = oradio_usb_service.get_state()  # Using the global instance
        if usb_state == STATE_USB_PRESENT:
            oradio_log.debug("USB is present. Setting usb_present_event.")
            usb_present_event.set()
        else:
            oradio_log.debug("USB is absent. Clearing usb_present_event.")
            usb_present_event.clear()

#------------Networking state machine------------
            
class Networking:
    """
    Combines web-service + wifi-service into one 4-state machine:
      • APWebservice
      • Internet
      • ConnectedNoInternet
      • Idle
    """

    def __init__(self, web_service, wifi_service):
        self.web_service = web_service
        self.wifi_service = wifi_service

        # start out in Idle
        self.state = "Idle"
        self.task_lock = threading.Lock()
        
        # track whether we've entered APWebservice and not yet played WifiConnected
        self.ap_tracker = False

    def transition(self):
        ws_state   = self.web_service.get_state()
        wifi_state = self.wifi_service.get_state()

        # Map (Wi-Fi, Web-Service) → Networking state:
        #  • (Idle, Idle) or (Idle, Active)                 → Idle
        #  • (Internet, Idle) or (Internet, Active)         → Internet
        #  • (ConnectedNoInternet, Idle) or (ConnectedNoInternet, Active)
        #                                                    → ConnectedNoInternet
        #  • (AccessPoint, Active)                          → APWebservice
        #  • (AccessPoint, Idle)                            → Idle

        if ws_state == STATE_WEB_SERVICE_ACTIVE and \
           wifi_state == STATE_WIFI_ACCESS_POINT:
            # AP mode + service running
            requested = "APWebservice"

        elif wifi_state == STATE_WIFI_INTERNET:
            # normal Wi-Fi client with Internet
            requested = "Internet"

        elif wifi_state == STATE_WIFI_CONNECTED:
            # Wi-Fi client without Internet
            requested = "ConnectedNoInternet"

        else:
            # covers both:
            #   • wifi_state == STATE_WIFI_IDLE
            #   • wifi_state == STATE_WIFI_ACCESS_POINT with WS idle
            requested = "Idle"

 #       oradio_log.info("Networking: %s → %s", self.state, requested)

        # 2 no‐op if same
        if requested == self.state:
            oradio_log.info("Networking state is already %s", self.state)
            return
        
        # 3 commit new state
        old = self.state
        self.state = requested
        oradio_log.info("Networking state changed: %s → %s", old, self.state)

        # 4 spawn handler with both old & new
        threading.Thread(
            target=self.run_state_method,
            args=(old, requested),
            daemon=True
        ).start()


    def run_state_method(self, old_state, new_state):
        """
        Perform the needed tasks on entry/exit of APWebservice,
        
        """
        with self.task_lock:
            # Handle APWebservice blinking on entry
            if new_state == "APWebservice":
                leds.control_blinking_led("LEDPlay", 2)
                sound_player.play("OradioAPstarted")
                self.ap_tracker = True
                oradio_log.info("Networking entered APWebservice state")

            #  Stop blinking only if we just left APWebservice
            elif old_state == "APWebservice" and new_state != "APWebservice":
                leds.control_blinking_led("LEDPlay", 0)
                sound_player.play("OradioAPstopped")
                play_webservice_states = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateWebService"}			
                if state_machine.state in play_webservice_states: # If in play states, 
           #         leds.control_blinking_led("LEDPlay", 0)
                    state_machine.transition("StateIdle")
                    state_machine.transition("StatePlay") # Move from APWebservice state to back to Play State
           #         sound_player.play("OradioAPstopped")
                APWebserviceTracker= True
                oradio_log.info("Networking left APWebservice state")

            if new_state == "Internet":
                #  play “WifiConnected” only when arriving (some states ago) from APWebservice
                if self.ap_tracker:
                    sound_player.play("WifiConnected")
                    self.ap_tracker = False

                # Send system info to Remote Monitoring Service
                remote_monitor.send_sys_info()
                # Send heartbeat every hour to Remote Monitoring Service
                remote_monitor.heartbeat_start()
                oradio_log.info("Networking is in Internet state")
            else:
                remote_monitor.heartbeat_stop()  # in all other cases, stop sending heartbeat
                
            if new_state == "ConnectedNoInternet":
                #  play “WifiNot  Connected” only when arriving from APWebservice
                if self.ap_tracker:
                    sound_player.play("NoInternet")
                    self.ap_tracker = False
                oradio_log.info("Networking is in Connected No Internet state")
                
            elif new_state == "Idle":
                oradio_log.info("Networking is in Idle state")
            else:
                # this covers new_state == "APWebservice" again, or unexpected
                pass



            
#-------------Messages handler: -----------------
            
# 1) Functions which define the actions for the messages

#-------------------VOLUME-----------------------

def on_volume_changed():
    if state_machine.state == "StateStop" or state_machine.state == "StateIdle":
        state_machine.transition("StatePlay") # Switch Oradio in Play when Volume buttons is turned

#-------------------USB---------------------------

def on_usb_absent():
    usb_present_event.clear()  # Clear the event so wait() will block
    state_machine.transition("StateUSBAbsent")
    oradio_log.debug("USB absent acknowlegded")

def on_usb_present():
    usb_present_event.set()  # Signal that USB is now present
    oradio_log.debug("USB present acknowledged")

#-------------------WIFI--------------------------

# All state  wifi and web services changes are handled by the Message handler and Networking_mgr

def on_wifi_connected_to_internet():
    oradio_log.debug("Wifi is connected to internet acknowledged")
    
def on_wifi_connected_no_internet():
    oradio_log.debug("Wifi is connected NO internet acknowledged")

def on_wifi_not_connected():
    oradio_log.debug("Wifi is NOT connected acknowledged")

def on_wifi_access_point():
    oradio_log.debug("Configured as access point acknowledged")

def on_wifi_error():
    sound_player.play("WifiNotConnected")  # 
    oradio_log.debug("Wifi Error acknowledged")

#-------------------WEB---------------------------

def on_webservice_active():
    oradio_log.debug("WebService active is acknowledged")

def on_webservice_idle():
     oradio_log.debug("WebService idle is acknowledged")

def on_webservice_playing_song():
    spotify_connect.pause() # spotify is on pause and will not work
    if state_machine.state == "StateStop": # if webservice put songs in queue and plays it
        state_machine.transition("StatePlaySongWebIF")   #  and if player is switched of, switch it on, otherwise keep state
    oradio_log.debug("WebService playing song acknowledged")

def on_webservice_pl1_changed():
    state_machine.transition("StateIdle")  # Step in bewteen if state is the same, preventing Next
    state_machine.transition("StatePreset1")
    threading.Timer(2, sound_player.play, args=("NewPlaylistPreset",)).start() # to make that first the Preset number is heard
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

#-------------------SPOTIFY-----------------------

def on_spotify_connect_connected():
    spotify_connect_connected.set() # Signal that spotify_connected is active
    update_spotify_connect_available()
    oradio_log.debug("Spotify active is acknowledged")

def on_spotify_connect_disconnected():
    spotify_connect_connected.clear() # Signal that spotify_connected is inactive
    update_spotify_connect_available()
    oradio_log.debug("Spotify inactive is acknowledged")

# Both can switch the Oradio remotely, which is not in line with "in Control"
def on_spotify_connect_playing():
    spotify_connect_connected.set() # Signal that spotify_connected is active
    spotify_connect_playing.set()
    update_spotify_connect_available()
    oradio_log.debug("Spotify playing is acknowledged")

def on_spotify_connect_paused():
    spotify_connect_connected.set() # Signal that spotify_connected is active
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
        if state_machine.state in ("StatePlay"):# if Spotify connect is  avalaible Switch to
            state_machine.transition("StateSpotifyConnect") # Switch to Spotify Connect
    else:
        spotify_connect_available.clear()
        if state_machine.state == "StateSpotifyConnect": # if Spotify connect is not avalaible
            state_machine.transition("StateStop") # Switch of as Spotify stops

    oradio_log.info(
        f"Spotify Connect States - Connected: {spotify_connect_connected.is_set()}, "
        f"Playing: {spotify_connect_playing.is_set()}, "
        f"Available: {spotify_connect_available.is_set()}"
    )

#2)-----The Handler map, defining message content and the handler funtion---

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
        MESSAGE_WIFI_FAIL_CONNECT: on_wifi_error,
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
    state        = message.get("state")
    error        = message.get("error", None)

    handlers = HANDLERS.get(command_type)
    if handlers is None:
        oradio_log.warning("Unhandled message type: %s", message)
        return

    if handler := handlers.get(state):
        handler()
    else:
        oradio_log.warning(
            "Unhandled state '%s' for message type '%s'.",
            state, command_type
        )

    if error and error != MESSAGE_NO_ERROR:
        if handler := handlers.get(error):
            handler()
        else:
            oradio_log.warning(
                "Unhandled error '%s' for message type '%s'.",
                error, command_type
            )
   # ── after you’ve done your per‐message logic, update Networking ──
    if command_type in (MESSAGE_WIFI_TYPE, MESSAGE_WEB_SERVICE_TYPE):
        network_mgr.transition()            
    

# 3)----------- Process the messages---------
def process_messages(queue):
    try:
        while True:
            msg = queue.get()
            oradio_log.debug("Received message in Queue: %s", msg)
            handle_message(msg)
    except Exception as ex:
        oradio_log.error("Unexpected error in process_messages: %s", ex)


#------------------Start-up - instantiate and define other modules ------------------------------

shared_queue = Queue() # Create a shared queue

# Instantiate the state machine
state_machine = StateMachine()

# Instantiate spotify
spotify_connect = SpotifyConnect(shared_queue)
spotify_connect.pause()  # pause spotify connect

# Initialize the oradio_usb class
oradio_usb_service = USBService(shared_queue)

# Check status usb
state_machine.update_usb_event()
if not usb_present_event.is_set(): # no USB present
    oradio_log.warning("USB is Absent")
    state_machine.transition("StateUSBAbsent")   # Go to StateUSBAbsent
else:
    state_machine.transition("StateStartUp") #Statemachine in start up mode

# Initialize TouchButtons and pass the state machine
touch_buttons = TouchButtons(state_machine)

# Initialize the volume_control, works stand alone, getting messages via the shared_queue
volume_control = VolumeControl(shared_queue)

# #Initialize the wifi_service
oradio_wifi_service = WifiService(shared_queue)
# 
# #Initialize the web_service
oradio_web_service = WebService(shared_queue)

# instantiate the Networking “manager” the additional Statemachine in this script
network_mgr = Networking(oradio_web_service, oradio_wifi_service)

# instantiate the process messages

threading.Thread(target=process_messages, args=(shared_queue,), daemon=True).start()  # start messages handler


# ——————————————————————————————
# STRESS-TEST SETUP
# ——————————————————————————————
# Use only in case of testing this script, it runs on top of the excisting controls
# start with  python oradio_control.py --stress (or with additional arguments
# like --min-delay 0.2 --max-delay 0.5 --max-transitions 10
# After stress test, Oradio need to function, without bugs and errors


STATES = [
    "StatePlay", "StatePreset1", "StatePreset2", "StatePreset3",
    "StateStop", "StateSpotifyConnect", "StatePlaySongWebIF",
    "StateUSBAbsent", "StateStartUp", "StateIdle", "StateWebService"
]

# Event that, when set, signals the stress loop to stop cleanly
_stress_stop       = threading.Event()

# Holds the Thread object running the stress loop once it's started;
# remains None until maybe_start_stress() launches it.
_stress_thread     = None

# Simple integer counter tracking how many transitions have been fired
_stress_count      = 0

# Lock to synchronize increments of _stress_count between the stress and main threads
_stress_count_lock = threading.Lock()

def _stress_loop(min_d, max_d, max_count):
    global _stress_count
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
            print(f"\n🛑 Reached max_transitions ({max_count}). Stopping stress test 🛑")
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
    global _stress_thread

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--stress",           action="store_true",
                   help="hammer the state machine with random transitions")
    p.add_argument("--min-delay",        type=float, default=0.1,
                   help="minimum delay between transitions")
    p.add_argument("--max-delay",        type=float, default=0.5,
                   help="maximum delay between transitions")
    p.add_argument("--max-transitions",  type=int, default=20,
                   help="stop automatically after this many transitions")
    args, _ = p.parse_known_args()

    if not args.stress:
        return

    print("🔥 Starting STRESS test 🔥")
    print("Press Q + Enter at any time to stop the stress test early.\n")

    # 1) spin up the stress loop (passing max-transitions)
    _stress_thread = threading.Thread(
        target=_stress_loop,
        args=(args.min_delay, args.max_delay, args.max_transitions),
        daemon=True
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

#---END Stress test part

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
        except Exception as ex_err:
            oradio_log.error("Error cleaning up touch_buttons: %s", ex_err)



if __name__ == "__main__":
    # start stress test if requested via the arguments --stress, otherwise simply skipped
    maybe_start_stress()

    # then hand over to your normal main loop
    main()

    signal.raise_signal(signal.SIGTERM)