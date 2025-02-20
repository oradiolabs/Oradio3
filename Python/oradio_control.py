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

Update Wifi_connected event
Update logic statemachine for No USB
Added messages when Wifi connected Not connected

"""
import time
import threading
from multiprocessing import Queue
import subprocess
import os

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl
from mpd_control import MPDControl
from led_control import LEDControl
from play_system_sound import PlaySystemSound
from touch_buttons import TouchButtons
from remote_monitoring import rms_service

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

# Instantiate remote monitor
remote_monitor = rms_service()

# Send system info to Remote Monitoring Service
remote_monitor.send_sys_info()
# Send heartbeat every hour to Remote Monitoring Service
remote_monitor.heartbeat_start()

#--------- Spotify test part
#----------Reservation------------
try:
    import spotify_connect
except ImportError:
    from queue import Queue  # Ensure Queue is imported

    class SpotifyConnectPlaceholder:
        def __init__(self, shared_queue):
            """Placeholder class for SpotifyConnect that accepts a queue."""
            self.shared_queue = shared_queue
            print("SpotifyConnectPlaceholder initialized with shared_queue")

        def play(self):
            print("Spotify play() called (Placeholder)")

        def pause(self):
            print("Spotify pause() called (Placeholder)")
# use as
# spotify_connect.play()
# spotify_connect.pause()

#from spotify_go_connect import SpotifyGoConnect  # test case

spotify_connected_active = threading.Event() # track status Spotify connected

#-----------------------
from usb_service import usb_service
from web_service import web_service



usb_present_event = threading.Event() # track status USB

wifi_connected_event = threading.Event() # track status wifi

Web_Service_Active=False # track status Webservice NOT used yet

# Instantiate MPDControl
mpd = MPDControl()
# Instantiate  led control
leds = LEDControl()
# Instantiate sound player
sound_player = PlaySystemSound()


# other classes initiated after Statemachine class is defined

#----------------------State Machine------------------

class StateMachine:
# The states are triggered by the buttons and messages
# Within the states, the actions are defined and initiated


    
    def __init__(self):
        self.state = "StateStartUp"
        self.task_lock = threading.Lock()
#        self.spot_con = None  # Placeholder, will be set later


    def transition(self, new_state):
        oradio_log.debug(f"Transitioning from {self.state} to {new_state}")
        if self.state == new_state:
            if self.state == "StatePlay" or self.state == "StatePreset1" or self.state == "StatePreset2" or self.state == "StatePreset3":
                threading.Thread(target=mpd.next).start()  # PLAY NEXT SONG
                oradio_log.debug(f"Next song")
                return  # Do not continue with change of state
        if spotify_connected_active.is_set(): # if Spotify connect is active 
            if new_state == "StatePlay":
                new_state = "StateSpotifyConnect" # redirect when Spotify Connect is active
            else:
                spotify_connect.pause() # pause Spotify_connect
        if usb_present_event.is_set():
            self.state = new_state # go to new state            
        else:
            oradio_log.warning(f"Transitioning from {self.state} to {new_state} blocked as USB is not present")
            if self.state != "StateUSBAbsent":
                self.state = "StateUSBAbsent"
        threading.Thread(target=self.run_state_method).start()
        
    def run_state_method(self):
        
        global Web_Service_Active # track status Webservice
 #       global Wifi_Connected
        
        with self.task_lock:  # is needed to prevent that mutiple calls to the statemachine are handled
            leds.turn_off_all_leds()
            
            if self.state == "StatePlay":
                leds.turn_on_led("LEDPlay")
                mpd.play()
                sound_player.play("Play")
     
            elif self.state == "StatePreset1":
                leds.turn_on_led("LEDPreset1")
                mpd.play_preset("Preset1")
                sound_player.play("Preset1")

                
            elif self.state == "StatePreset2":
                leds.turn_on_led("LEDPreset2")
                mpd.play_preset("Preset2")
                sound_player.play("Preset2")

      
            elif self.state == "StatePreset3":
                leds.turn_on_led("LEDPreset3")
                mpd.play_preset("Preset3")
                sound_player.play("Preset3")

                
            elif self.state == "StateStop":
                leds.turn_on_led_with_delay("LEDStop", 4)
                mpd.pause()
                spotify_connect.pause()
                sound_player.play("Stop")
     #           if Web_Service_Active:
    #              oradio_web_service.stop()# Stop Webservice When active 
    # As songs are playimh, it is much more logic that whith Stop, thw Webinterface is not stopped
      
            elif self.state == "StateSpotifyConnect":
                leds.turn_on_led("LEDPlay")
                sound_player.play("Spotify")
                mpd.pause()
                spotify_connect.play()
                
            elif self.state == "StateUSBAbsent":
                leds.control_blinking_led("LEDStop", 0.7)
                
                sound_player.play("Stop")
                sound_player.play("NoUSB")
                self.wait_for_usb_present()
                state_machine.transition("StateIdle") # when USB is preset gow to Idle
                ####
                
            elif self.state == "StateStartUp":
                leds.control_blinking_led("LEDStop", 1)
                oradio_log.debug(f"Starting-up")

                mpd.pause()  # just to make sure in case of restart
                mpd.start_update_mpd_database_thread()  # Update the MPD database in seperate thread            
                time.sleep(3)  #  START uo time reservation just take some margin in start-up

                sound_player.play("StartUp")
                oradio_log.debug(f"Starting-up Completed")
                
                self.transition("StateIdle")
                
            elif self.state == "StateIdle":   # Wait for next button/ command
                mpd.pause() # Stop the Mpd just to make sure
                oradio_log.debug(f"In Idle state, wait for next step")
        
            elif self.state == "StateWebService":  # Triggered by LONG PRESS
                leds.control_blinking_led("LEDPlay", 0.7)
                mpd.pause()
                oradio_web_service.start()
                if wifi_connected_event.is_set(): # if connected to wifi web_service will start
                    oradio_log.debug(f"In WebService State, wait for next step")
                    sound_player.play("WebInterface")
                else:
                    oradio_log.debug(f"Long Press resulted in OradioAP as not connecetd to wifi")
                    sound_player.play("OradioAP")
                Web_Service_Active = True
                time.sleep(4)
                leds.control_blinking_led("LEDPlay", 0)
                mpd.play()
                leds.turn_on_led("LEDPlay")

            elif self.state == "StateWebServiceForceAP": # Triggered by EXTRA LONG PRESS
            #    leds.control_blinking_led("LEDPlay", 0)# stop previous blinking
                leds.control_blinking_led("LEDPlay", 0.5)
                mpd.pause()
                oradio_web_service.start(force_ap=True)
                sound_player.play("OradioAP")
                oradio_log.debug(f"In WebServiceForceAP state, wait for next step")
                Web_Service_Active = True
            #    mpd.play()
            #    self.play_SysSound("Play")
        
            elif self.state == "StateError":
                leds.control_blinking_led("LEDStop", 2)
                sound_player.play("Stop")
                

    def wait_for_usb_present(self):
        """Waits for the USB to be present, cancels ongoing MPD updates, and restarts MPD if needed."""
        self.state = "StateWaitForUSBPresent"
        oradio_log.debug(f"Waiting for USB to be present...")
        # Cancel any ongoing MPD database update before waiting
        mpd.cancel_update()  
        # Wait for the USB event to be set
        usb_present_event.wait()  # Blocks until the USB is inserted
        oradio_log.debug(f"USB is now present, checking MPD state...")
        # Restart MPD service to ensure a fresh start
    #    mpd.restart_mpd_service()  # seems not needed when the delay is used
        # Ensure MPD is ready before starting an update
        time.sleep(0.2)  # Small delay to allow MPD to recover and before start mpd update
        # Start MPD database update in a separate thread
        oradio_log.debug(f"Starting MPD database update...")
        mpd.start_update_mpd_database_thread()
        
    def update_usb_event(self):
        print(" Update USB")
        usb_state = oradio_usb_service.get_state()  # Using the global instance
        if usb_state == STATE_USB_PRESENT:
            oradio_log.debug("USB is present. Setting usb_present_event.")
            usb_present_event.set()
        else:
            oradio_log.debug("USB is absent. Clearing usb_present_event.")
            usb_present_event.clear()
#            self.transition("StateUSBAbsent")

#---------------------------Messages and Queue handling------------------
        
def process_messages(queue):
    """
    Continuously process and handle messages from the queue.
    """
    def handle_message(message):
        handlers = {
            "Vol Control message": {
                "Volume changed": on_volume_changed,
                # For example, if an error is reported as "Volume error"
#               "Volume error": on_volume_error,
            },
            "USB message": {
                STATE_USB_ABSENT: on_usb_absent,
                STATE_USB_PRESENT: on_usb_present,
                # Example error key for USB messages
#                "USB error": on_usb_error,
            },
            "Wifi message": {
                STATE_WIFI_IDLE: on_wifi_not_connected,
                STATE_WIFI_INFRASTRUCTURE: on_wifi_connected_to_internet,
                STATE_WIFI_LOCAL_NETWORK: on_wifi_connected_to_local_network,
                STATE_WIFI_ACCESS_POINT: on_wifi_not_connected,
                # If an error occurs, the error text is used as the key.
                "Wifi failed to connect": on_wifi_error,
            },
            "web service message": {
                STATE_WEB_SERVICE_IDLE: on_webservice_not_active,
                MESSAGE_WEB_SERVICE_PLAYING_SONG: on_webservice_playing_song,
#                "Webservice error": on_webservice_error,
            },
            "SPOTIFY_CONNECT": {
                "ACTIVE": on_spotify_connect_active,
                "INACTIVE": on_spotify_connect_inactive,
                "PLAYING": on_spotify_connect_playing,
                "PAUSED": on_spotify_connect_paused,
#                "Spotify error": on_spotify_error,
            },
            # Add more mappings as needed.
        }

        command_type = message.get("type")
        state = message.get("state")
        error = message.get("error", None)

        if command_type not in handlers:
            oradio_log.debug(f"Unhandled message type: {message}")
            return

        # Process the normal state message, if a handler exists.
        if state in handlers[command_type]:
            handlers[command_type][state]()
        else:
            oradio_log.debug(f"Unhandled state '{state}' for message type '{command_type}'.")

        # If an error is provided, handle it as if it were another state.
        if error is not None:
            if error in handlers[command_type]:
                handlers[command_type][error]()
            else:
                oradio_log.debug(f"Unhandled error '{error}' for message type '{command_type}'.")

    try:
        while True:
            message = queue.get()  # Blocks until a message is available
            oradio_log.debug(f"Received message in Queue: {message}")
            handle_message(message)
    except Exception as e:
        oradio_log.error(f"Unexpected error in process_messages: {e}")



# Define the actions

def on_volume_changed():
    if state_machine.state == "StateStop" or state_machine.state == "StateIdle":
        state_machine.transition("StatePlay") # Switch Oradio in Play when Volume buttons is turned

def on_usb_absent():
    usb_present_event.clear()  # Clear the event so wait() will block
    state_machine.transition("StateUSBAbsent")
    oradio_log.debug(f"USB absent acknowlegded")

def on_usb_present():
    usb_present_event.set()  # Signal that USB is now present
    oradio_log.debug("USB present acknowledged")
    
    
def on_wifi_connected_to_internet():
    wifi_connected_event.set()
    # Send system info to Remote Monitoring Service
    remote_monitor.send_sys_info()
    if state_machine.state == "StateWebServiceForceAP" or state_machine.state == "StateWebService": # If connection made, move to stop
        sound_player.play("WifiConnected")
        state_machine.transition("StateStop")
    oradio_log.debug(f"Wifi is connected acknowledged")

def on_wifi_connected_to_local_network():
    wifi_connected_event.set()
    if state_machine.state == "StateWebServiceForceAP" or state_machine.state == "StateWebService": # If waiting for connection, move to stop
        sound_player.play("WifiConnected")
        state_machine.transition("StateStop")
    oradio_log.debug(f"Wifi is connected acknowledged")

def on_wifi_not_connected():
    wifi_connected_event.clear()
#     if state_machine.state == "StateWebServiceForceAP" or state_machine.state == "StateWebService":
#         sound_player.play("WifiNotConnected")
    oradio_log.debug(f"Wifi is NOT connected acknowledged")

def on_wifi_error():
    wifi_connected_event.clear()
    if state_machine.state == "StateWebServiceForceAP" or state_machine.state == "StateWebService":
        sound_player.play("WifiNotConnected")
    oradio_log.debug(f"Wifi failed to connect acknowledged")


def on_webservice_active():
    global Web_Service_Active
    Web_Service_Active = True
    oradio_log.debug(f"WebService active is acknowledged")

def on_webservice_not_active():
    Web_Service_Active = False
    oradio_log.debug(f"WebService NOT active is acknowledged")
    
def on_webservice_playing_song():
    if state_machine.state == "StateStop": # if webservice put songs in queue and plays it
        state_machine.transition("StatePlay")   #  and if player is switched of, switch it on, otherwise keep state
    oradio_log.debug(f"WebService playing song acknowledged")
    
    
def on_spotify_connect_active():
    spotify_connected_active.set() # Signal that spotify_connected is active
    oradio_log.debug(f"Spotify active is acknowledged")
    
def on_spotify_connect_inactive():
    spotify_connected_active.clear() # Signal that spotify_connected is inactive
    oradio_log.debug(f"Spotify inactive is acknowledged")

# Both can switch the Oradio remotely, which is not in line with "in Control"
def on_spotify_connect_playing():
#     if  state_machine.state!="StateSpotifyConnect":
#         state_machine.transition("StateSpotifyConnect")
    oradio_log.debug(f"Spotify playingis acknowledged")
    
def on_spotify_connect_paused():
#     if  state_machine.state!="StateStop":
#         state_machine.transition("StateStop")
    oradio_log.debug(f"Spotify paused is acknowledged")
    
#------------------------------------------------------------------------

shared_queue = Queue() # Create a shared queue

threading.Thread(target=process_messages, args=(shared_queue,), daemon=True).start()  # start messages handler

# Instantiate the state machine
state_machine = StateMachine()

# Initialize the oradio_usb class
oradio_usb_service = usb_service(shared_queue)

# Check status usb
state_machine.update_usb_event()
if not usb_present_event.is_set(): # no USb present
    oradio_log.warning(f"USB is Absent")
    state_machine.transition("StateUSBAbsent")   # Go to StateUSBAbsent
else:
    state_machine.transition("StateStartUp") #Statemachine in start up mode

# Initialize TouchButtons and pass the state machine
touch_buttons = TouchButtons(state_machine)

# Initialize the volume_control, works stand alone, getting messages via the shared_queue
volume_control = VolumeControl(shared_queue)

#Initialize the web_service
oradio_web_service = web_service(shared_queue)

# Instantiate sound player
spotify_connect=SpotifyConnectPlaceholder(shared_queue)


import sys
import time
import traceback # by showing where in the code the error happened and what caused it
import faulthandler # capture and print low-level crashes

faulthandler.enable()

def main():
    try:
        oradio_log.debug("Oradio control main loop running")
        while True:
            time.sleep(1)  # Main loop
    except KeyboardInterrupt:
        oradio_log.debug("KeyboardInterrupt detected. Exiting...")
    except Exception as e:
        oradio_log.error(f"Unhandled exception: {e}")
        oradio_log.error(traceback.format_exc())
        sys.exit(1)  # Ensure systemd restarts the service
    finally:
        touch_buttons.cleanup()  # Ensure GPIO cleanup

if __name__ == "__main__":
    main()
