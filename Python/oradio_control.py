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

#from mpd_control import MPDControl
from mpd_control import get_mpd_control

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

# Instantiate MPDControl
mpd = get_mpd_control()

# Instantiate  led control
leds = LEDControl()
# Instantiate sound player
sound_player = PlaySystemSound()


#----------------------State Machine------------------

class StateMachine:
    def __init__(self):
        self.state = "StateStartUp"
        self.prev_state = None
        
        self.task_lock = threading.Lock()
        # placeholders until you wire them in
        self._websvc    = None
        self.network_mgr = None
        
    def set_networking(self, network_mgr):
        """Inject the Networking instance so run_state_method can see it."""
        self.network_mgr = network_mgr    
  
    def set_services(self, web_service):
        """
        Inject the (already‐constructed) WebService.
        """
        self._websvc  = web_service
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
        if not hasattr(self, "usb_media_mgr") or \
           self.usb_media_mgr.state != USB_Media.USB_StatePresent:
            oradio_log.warning("WebService start blocked (USB absent)")
            return

        # Good to go
        oradio_log.debug("Starting WebService: %r", ws)
        leds.control_blinking_led("LEDPlay", 2)
        ws.start()
        

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
        # 3. SWITCH OFF WEB SERVICE IN AP MODE
        # ————————————————————————————————
        if self.network_mgr and self.network_mgr.state == "APWebservice" and requested_state == "StateStop":
            oradio_web_service.stop()
            return

        # ————————————————————————————————
        # 4. BLOCK WEBRADIO PRESETS WHEN NO INTERNET
        # ————————————————————————————————
        web_preset_states = {"StatePreset1", "StatePreset2", "StatePreset3"}
        if requested_state in web_preset_states:
            preset_key = requested_state.replace("State", "")
            if mpd.preset_is_webradio(preset_key) and self.network_mgr.state != "Internet":
                oradio_log.info(
                    "Blocked transition to %s: preset is web radio but no Internet",
                    requested_state
                )
                threading.Timer(2, sound_player.play, args=("NoInternet",)).start()
                return

        # ————————————————————————————————
        # 5. USB PRESENCE GUARD + COMMIT (via USB_Media)
        # ————————————————————————————————
        if self.usb_media_mgr.state == USB_Media.USB_StatePresent:
            # USB is present → commit the requested state
            self.prev_state = self.state
            self.state      = requested_state
            oradio_log.debug("State changed: %s → %s", self.prev_state, self.state)
        else:
            # USB absent → block and force the USB‐absent state
            oradio_log.warning("Transition to %s blocked (USB absent)", requested_state)
            if self.state != "StateUSBAbsent":
                self.prev_state = self.state
                self.state      = "StateUSBAbsent"
                oradio_log.debug("State set to StateUSBAbsent")

        # ————————————————————————————————
        # 6. SPAWN THREAD FOR THE ACTUAL STATE
        # ————————————————————————————————
        state_to_handle = self.state
        threading.Thread(
            target=self.run_state_method,
            args=(state_to_handle,),
            daemon=True
        ).start()

    def run_state_method(self, state_to_handle):

        with self.task_lock:
            leds.turn_off_all_leds()

            if state_to_handle == "StatePlay":
                if self.network_mgr and self.network_mgr.state == "APWebservice":
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
                if self.network_mgr and self.network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset2":
                leds.turn_on_led("LEDPreset2")
                mpd.play_preset("Preset2")
                sound_player.play("Preset2")
                if self.network_mgr and self.network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StatePreset3":
                leds.turn_on_led("LEDPreset3")
                mpd.play_preset("Preset3")
                sound_player.play("Preset3")
                if self.network_mgr and self.network_mgr.state == "APWebservice":
                    leds.control_blinking_led("LEDPlay", 2)
                spotify_connect.pause()

            elif state_to_handle == "StateStop":
                leds.turn_on_led_with_delay("LEDStop", 4)
                mpd.pause()
                spotify_connect.pause()
                sound_player.play("Stop")

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
                if self.network_mgr and self.network_mgr.state == "APWebservice":
                    oradio_web_service.stop()

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

            elif state_to_handle == "StateError":
                leds.control_blinking_led("LEDStop", 2)
                

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
                
                # 1) If we're currently in a preset state that is a WebRadio, stop it
                play_presets = {"StatePreset1", "StatePreset2", "StatePreset3"}
                curr = state_machine.state
                if curr in play_presets:
                    # turn "StatePreset1" → "Preset1"
                    preset_key = curr.replace("State", "")
                    if mpd.preset_is_webradio(preset_key):
#                        mpd.stop()
                        state_machine.transition("StateIdle")
                        oradio_log.info("Stopped WebRadio preset %s on APWebservice entry", preset_key)

                # 2) Or if we're in plain Play and it's a WebRadio, stop it
                elif curr == "StatePlay" and mpd.current_is_webradio():
#                    mpd.stop()
                    state_machine.transition("StateIdle")
                    oradio_log.info("Stopped WebRadio playback on APWebservice entry")
                
                      
                leds.control_blinking_led("LEDPlay", 2)
                sound_player.play("OradioAPstarted")
                self.ap_tracker = True
                oradio_log.info("Networking entered APWebservice state")

            #  Stop blinking only if we just left APWebservice
            elif old_state == "APWebservice" and new_state != "APWebservice":
                if state_machine.state == "StatePlay":
                    leds.turn_on_led("LEDPlay")
                else:
                    leds.control_blinking_led("LEDPlay", 0)
                sound_player.play("OradioAPstopped")

                APWebserviceTracker= True
                oradio_log.info("Networking left APWebservice state")

            if new_state == "Internet":
                #  play “WifiConnected” only when arriving (some states ago) from APWebservice
                # only play “WifiConnected” when arriving from APWebservice
                # and both the *previous* and *current* Oradio state are in play/webservice modes
                
#                 oradio_log.info(
#                 "Networking → Internet: prev_state=%s, current_state=%s",
#                 state_machine.prev_state,
#                 state_machine.state
#                 )
                play_webservice_states = {
                    "StatePlay",
                    "StatePreset1",
                    "StatePreset2",
                    "StatePreset3",
                    "StateWebService",
                }
                if (
                    self.ap_tracker and
                    state_machine.state      in play_webservice_states
                ):
                    # delay the tone so it doesn’t clash with any other sounds
                    threading.Timer(4, sound_player.play, args=("WifiConnected",)).start()
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
                play_webservice_states = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateWebService"}
                if state_machine.state in play_webservice_states: # If in play states,
                    if self.ap_tracker:
                        sound_player.play("NoInternet")
                        self.ap_tracker = False
                oradio_log.info("Networking is in Connected No Internet state")
                
            elif new_state == "Idle":
                oradio_log.info("Networking is in Idle state")
            else:
                # this covers new_state == "APWebservice" again, or unexpected
                pass

#---------------USB_Media state machine-----------------------
            
class USB_Media:
    """
    USB presence handler with two states:
      • USB_StateAbsent  → forces StateUSBAbsent
      • USB_StatePresent → plays USBPresent tone when unblocking and transitions to Idle

    External code must call `transition()` on USB events with internal state names.
    """
    USB_StateAbsent  = "USB_StateAbsent"
    USB_StatePresent = "USB_StatePresent"

    def __init__(self, usb_service, state_machine, sound_player):
        """
        :param usb_service: USBService instance providing get_state()
        :param state_machine: StateMachine to drive transitions
        :param sound_player: PlaySystemSound for USBPresent tone
        """
        self.usb_service   = usb_service
        self.state_machine = state_machine
        self.sound_player  = sound_player

        # Set initial internal state
        service_state = self.usb_service.get_state()
        self.state = (
            self.USB_StatePresent
            if service_state == STATE_USB_PRESENT
            else self.USB_StateAbsent
        )
        oradio_log.debug(f"USB_Media init: initial state = {self.state}")

        # Immediately handle initial state
        threading.Thread(
            target=self.run_state_method,
            args=(None, self.state),
            daemon=True
        ).start()

    def transition(self, new_state):
        """
        Commit transition and run handler.
        :param new_state: USB_StateAbsent or USB_StatePresent
        """
        if new_state not in (self.USB_StateAbsent, self.USB_StatePresent):
            oradio_log.info(f"USB_Media: invalid transition requested: {new_state}")
            return

        old_state = self.state
        if old_state == new_state:
            oradio_log.debug(f"USB_Media: already in {new_state}, skipping.")
            return

        self.state = new_state
        oradio_log.debug(f"USB_Media: state changed {old_state} → {new_state}")

        threading.Thread(
            target=self.run_state_method,
            args=(old_state, new_state),
            daemon=True
        ).start()

    def run_state_method(self, old_state, new_state):
        """
        Handle USB insert/remove in background.
        :param old_state: previously internal state or None
        :param new_state: new internal state
        """
        oradio_log.info(f"USB_Media handling transition {old_state} → {new_state}")
        if new_state == self.USB_StateAbsent:
            # Force main state to USBAbsent, unless still in startup
            if self.state_machine.state != "StateStartUp":
                self.state_machine.transition("StateUSBAbsent")
            mpd.cancel_update()  # cancel if MPD database update runs
        else:
            # USB inserted: only play USBPresent if coming from absent internal state
            if old_state == self.USB_StateAbsent:
                self.sound_player.play("USBPresent")
            # Transition to Idle after USB is inserted
            if self.state_machine.state != "StateStartUp":
                self.state_machine.transition("StateIdle")
            mpd.start_update_mpd_database_thread() # MPD database update
                                  
#-------------Messages handler: -----------------
            
# 1) Functions which define the actions for the messages

#-------------------VOLUME-----------------------

def on_volume_changed():
    if state_machine.state == "StateStop" or state_machine.state == "StateIdle":
        state_machine.transition("StatePlay") # Switch Oradio in Play when Volume buttons is turned

#-------------------USB---------------------------

def on_usb_absent():
    usb_media_mgr.transition(USB_Media.USB_StateAbsent)
    oradio_log.debug("USB absent acknowlegded")

def on_usb_present():
    usb_media_mgr.transition(USB_Media.USB_StatePresent)
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
    play_webservice_states = {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3", "StateWebService"}
    if state_machine.state in play_webservice_states: # If in play states,
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


# Initialize the oradio_usb class
oradio_usb_service = USBService(shared_queue)

usb_media_mgr = USB_Media(oradio_usb_service, state_machine, sound_player)

# attach it so StateMachine can refer to it as self.usb_media_mgr
state_machine.usb_media_mgr = usb_media_mgr

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

# inject the services into the Statemachine
state_machine.set_services(oradio_web_service)
state_machine.set_networking(network_mgr)

# # start the state_machine transition
state_machine.transition("StateStartUp")

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