#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Februari 1, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: test program for Spotify Connect
    :Note
    :Install
        - sudo apt-get -y install curl && curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
        - python -m pip install git+https://github.com/kokarare1212/librespot-python
        - sudo apt install avahi-utils
    :Documentation
        - D-bus : https://en.wikipedia.org/wiki/D-Bus
        - D-bus python: https://dbus.freedesktop.org/doc/dbus-python/tutorial.html
        
"""
#############################################################################################
# The select method is for managing multiple socket connections simultaneously. 
# It allows a program to monitor multiple file descriptors (like sockets) and 
# determine which are ready for reading, writing, or have encountered an exceptional condition. 
# This functionality is essential for building efficient I/O-bound applications that 
# serve multiple clients at the same time without blocking.
# The core of the select method revolves around three lists that you provide:
#    That’s a list of sockets that you want to monitor for incoming data.
#    This list contains sockets that you are monitoring for the ability to send data.
#    Sockets that you wish to monitor for exceptional conditions, like errors.
# The method works in a loop, returning when one or more sockets are ready. 
# This allows the server to handle client requests without needing to create a new thread or process for each client. 
# Instead, you can use a single thread to manage multiple connections, which is more efficient in terms of resource usage.
#################################################################################################################################


import subprocess
from subprocess import Popen, PIPE, CalledProcessError
import socket
import selectors
import threading
import pydantic
import os
import select
import time

import dbus
import json

#### Oradio modules  #####
import oradio_utils
from oradio_const import *
from oradio_logging import oradio_log

class SpotifyConnect():

    def accept_connection(self,sock):
        '''
        Accept a connection. 
        The socket must be bound to an address and listening for connections. 
        The return value is a pair (conn, address) where conn is a new socket object usable to send and receive data on the connection, 
        and address is the address bound to the socket on the other end of the connection.        
        '''
        conn, address = sock.accept()
        oradio_log.info("Socket-connection from {addr}".format(addr=address))
        conn.setblocking(False)
        self.sel.register(conn, selectors.EVENT_READ, self.read_message)
    
    def read_message(self,conn):
        '''
        Read message from socket.
        As soon as a connection with the client has been established (after calling the accept method). 
        To read the message a call to the recv method of the client_socket object. 
        This method receives the specified number of bytes from the client - in our case 1024. 
        1024 bytes is just a common convention for the size of the payload, 
        Since the data received from the client into the request variable is in raw binary form, 
        we transformed it from a sequence of bytes into a string using the decode function.
        :param socket = socket pointer
        '''
        #rec_data = conn.recv(1024) # max buffer size is 1024
        rec_data = conn.recv(1024) # max buffer size is 1024        
        if rec_data:
            librespot_data = json.loads(rec_data.decode())  #
            oradio_log.info(f"Data received from socket {librespot_data}")
            librespot_event = librespot_data["player_event"]
            if "client_id" in librespot_data:
                librespot_client_id = librespot_data["client_id"]
            else:
                librespot_client_id = "None"
            message = {}
            match librespot_event:        
                case 'playing':
                    message["state"] = SPOTIFY_CONNECT_PLAYING_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_PLAYING
                    self.spotify_connected_state = SPOTIFY_CONNECT_CONNECTED
                    self.spotify_client_id = librespot_client_id                                    
                case 'paused':
                    message["state"] = SPOTIFY_CONNECT_PAUSED_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_PAUSED                    
                    self.spotify_connected_state = SPOTIFY_CONNECT_CONNECTED
                case 'stopped':
                    message["state"] = SPOTIFY_CONNECT_STOPPED_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_STOPPED
                case 'session_connected':
                    message["state"] = SPOTIFY_CONNECT_CONNECTED_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_CONNECTED
                    self.spotify_connected_state = SPOTIFY_CONNECT_CONNECTED  
                    self.spotify_client_id = librespot_client_id   
                case 'session_disconnected':
                    message["state"] = SPOTIFY_CONNECT_DISCONNECTED_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_DISCONNECTED
                    self.spotify_connected_state = SPOTIFY_CONNECT_NOT_CONNECTED                    
                case 'session_client_changed':
                    message["state"] = SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT
                    self.spotify_app_status = SPOTIFY_APP_STATUS_CLIENT_CHANGED
                    self.spotify_client_id = librespot_client_id                
                case _:
                    message["state"] = None
            if message["state"] != None:
                message["error"] = "None"
                self.state = message["state"]
                # construct the message based on the schema for Messages
                self.queue_put_mesg["type"]     = MESSAGE_SPOTIFY_TYPE
                self.queue_put_mesg["state"]    = self.state
                self.queue_put_mesg["error"]    = message["error"]                            
                self.queue_put_mesg["data"]     = []
                oradio_log.debug(f"New message in queue={self.queue_put_mesg}")
                self.msg_queue.put(self.queue_put_mesg)
        else:
            # if recv() returns an empty bytes object, b'', 
            # that signals that the client closed the connection and the loop is terminated.            
            self.sel.unregister(conn)
            conn.close()

    def observer_loop(self):
        '''
        Start an observer for the selected socket, observer will initiate a callback upon an EVENT_READ
        '''
        oradio_log.info("The observer thread which listens to incoming message is running")
        while not self.stop_event.is_set():
            events = self.sel.select(timeout=None)
            # events has a list of events. A tuple of a key and event_mask
            # the event_mask shows which selectors were enabled(e.g., selectors.EVENT_READ, selectors.EVENT_WRITE).
            # the key is a selectorkey object and Each key represents a socket that has some activity 
            # (incoming connection, data received, etc.).
            for key, event_mask in events:
                # for each key a callback is done
                callback = key.data
                # the key holds the registered callback and socket
                callback(key.fileobj)

    def get_mpv_player(self):
        
        def get_mpris_players():
            bus = dbus.SessionBus()  # Refresh the session bus
            players = [service for service in bus.list_names() if service.startswith("org.mpris.MediaPlayer2.")]
            return bus,players
    
        self.state = SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE
        mpv_player = "None"        
        # temporary solution: need to figure out why this register process for mpv-SessionBis take some time
        # seems to be that time between activation of mpv and register to SessionBus may some "undefined" time
        for i in range(8):  # Retry up to 8 times
            bus, mpris_players = get_mpris_players()
            oradio_log.info(f"Available MPRIS Players: {mpris_players}")            
            if "org.mpris.MediaPlayer2.mpv" in mpris_players:
                self.state = SPOTIFY_CONNECT_MPV_STATE_OK
                self.bus = bus
                break
            time.sleep(1)  # Wait 1 second before retrying
        if self.state == SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE:
            oradio_log.error("Too many SessionBus retries, mpv not registered at SessionBus")
            return(self.state, mpv_player)
        # Check if mpv is in the list
        if MPRIS_MPV_PLAYER not in mpris_players:
            oradio_log.error("mpv is not found in MPRIS players!")
            self.state = SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND
            mpv_player = "None"
            return(self.state, mpv_player)                   
        else:
            oradio_log.info("mpv is found in MPRIS players!")
            self.state = SPOTIFY_CONNECT_MPV_STATE_OK         
            # Get the mpv MPRIS2 interface
            mpv_player = self.bus.get_object(MPRIS_MPV_PLAYER, MPRIS_MEDIA_PLAYER)
        return(self.state, mpv_player)


    def __init__(self, msg_queue):
        '''
         setup an observer listening to socket for incoming messages
        '''
        self.msg_queue  = msg_queue
        
        # create a message object based on json schema 
        # Load the JSON schema file
        with open(JSON_SCHEMAS_FILE) as f:
            schemas = json.load(f)
        # Dynamically create Pydantic models
        models = {name: oradio_utils.json_schema_to_pydantic(name, schema) for name, schema in schemas.items()}
        
        # create Messages model
        Messages = models["Messages"]
        #create an instance for this model
        self.messages = Messages(type="none", state="none", error="none", data=[])

        ## define the message model for the put message in the queue         
        self.queue_put_mesg         = self.messages.model_dump()
        self.queue_put_mesg["type"] = MESSAGE_SPOTIFY_TYPE

        # restart mpv.service again: Need to investigate this as it is annoying
        try:
            # Run systemctl to restart mpv.service
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "mpv"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            oradio_log.info("MPV service restarted ")
        except Exception as err:
            oradio_log.error("Restart MPV service error =",err)

        
        state, mpv_player = self.get_mpv_player()

        self.spotify_app_status = SPOTIFY_APP_STATUS_DISCONNECTED
        self.spotify_connected_state = SPOTIFY_CONNECT_NOT_CONNECTED
        self.spotify_client_id = "None"
        
        if self.state == SPOTIFY_CONNECT_MPV_STATE_OK:
            status, player_iface = setup_dbus_interface_to_control_mpv_player(mpv_player)
            self.state  = status

        if self.state == SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE:
            self.player_iface = player_iface
            # setup a observer (selector) for socket listening to incoming messages
            self.sel = selectors.DefaultSelector()
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
            self.server_socket.listen(5)
            self.server_socket.setblocking(False)
            oradio_log.info("event socket opened and listening on port {prt}".format(prt=SPOTIFY_EVENT_SOCKET_PORT))
            
            self.stop_event = threading.Event() # used to stop the observer loop
            
            self.sel.register(self.server_socket, selectors.EVENT_READ, self.accept_connection)
    
            # Run the observer in a separate thread
            observer_thread = threading.Thread(target=self.observer_loop, daemon=True)
            observer_thread.start()     
            self.state = SPOTIFY_CONNECT_MPV_STATE_OK
            oradio_log.info("MPV active and socket server Listening to spotify events ........")

    def shutdown_server(self):
        '''
        Shutting down the socket server and stop the selector-observer
        '''
        oradio_log.info("Shutting down server...")
        self.stop_event.set()  # Stop the observer loop
        self.sel.unregister(self.server_socket)  # Unregister the server socket
        self.server_socket.close()  # Close the socket
        oradio_log.info("Server closed.")

    def get_state(self):
        '''
        Return the actual state of the Spotify Connect servers and related events
        :return spotify_app_status = [ SPOTIFY_APP_STATUS_PLAYING | SPOTIFY_APP_STATUS_STOPPED | SPOTIFY_APP_STATUS_PAUSED | 
                                        SPOTIFY_APP_STATUS_DISCONNECTED | SPOTIFY_APP_STATUS_CONNECTED | SPOTIFY_APP_STATUS_CLIENT_CHANGED]
        :return spotify_connected_state = [ SPOTIFY_CONNECT_CONNECTED | SPOTIFY_CONNECT_NOT_CONNECTED] 
        '''
        
        return (self.spotify_app_status, self.spotify_connected_state)

    def get_playback_status(self):
        '''
        Retrieve the playback status of mpv via MPRIS2
        :return playback_status = [MPV_PLAYERCTL_PLAYING_STATE | MPV_PLAYERCTL_STOPPED_STATE | MPV_PLAYERCTL_PAUSED_STATE]
        
        '''
        try:
            # Get mpv's MPRIS2 object
            player = self.bus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
                # Get properties interface
            props = dbus.Interface(player, "org.freedesktop.DBus.Properties")
                # Retrieve the PlaybackStatus property
            playback_status = props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
            return str(playback_status)
    
        except dbus.exceptions.DBusException:
            return "mpv not running or MPRIS not available"    
    
    import subprocess

    def playerctl_command(self,command):
        '''
        Send command to playerctl via mpris player interface
        :param command = player command = [ MPV_PLAYERCTL_PLAY, 
                                            MPV_PLAYERCTL_PAUSE, 
                                            MPV_PLAYERCTL_STOP]
        :return self.state = state of mpv mpris player-control = [MPV_PLAYERCTL_PLAYING_STATE | 
                                                                  MPV_PLAYERCTL_STOPPED_STATE |
                                                                  MPV_PLAYERCTL_PAUSED_STATE]
        '''
        commands_list = [MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]
        """Check if mpv is available on D-Bus."""
        state, mpv_player = self.get_mpv_player()
        #bus_list_names = self.bus.list_names()
        player = self.bus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
        playback_status = self.get_playback_status()

        oradio_log.info(f"player-command = {command}, mpv-playback-status = {playback_status}, spotify_app_status={self.spotify_app_status} ")
        if command in commands_list:
            if command == MPV_PLAYERCTL_PLAY:
                    # always try to play,  if already in playing mode, it will be rejected
                    # Get Player interface
                    player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")                                                      
                    player_iface.Play()
                    self.state = MPV_PLAYERCTL_PLAYING_STATE
            elif command == MPV_PLAYERCTL_PAUSE:
                if self.spotify_app_status != SPOTIFY_APP_STATUS_PAUSED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")                                                      
                    player_iface.Pause()
                    self.state = MPV_PLAYERCTL_PAUSED_STATE                
            elif command == MPV_PLAYERCTL_STOP:                
                if self.spotify_app_status != SPOTIFY_APP_STATUS_STOPPED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")                                                      
                    player_iface.Stop()
                    self.state = MPV_PLAYERCTL_STOPPED_STATE                    
            else:
                self.state = MPV_PLAYERCTL_COMMAND_NOT_FOUND
        else:
            self.status = MPV_PLAYERCTL_COMMAND_NOT_FOUND
            oradio_log.warning(f"command-status={status}")            
        return (self.state)


    def mpv_playerctl_command(self,command):
        '''
        Send command to mpv remote player interface via socket interface
        :param command = player command = [ MPV_PLAYERCTL_PLAY, 
                                            MPV_PLAYERCTL_PAUSE, 
                                            MPV_PLAYERCTL_STOP]
        :return self.state = state of mpv mpris player-control = [MPV_PLAYERCTL_PLAYING_STATE | 
                                                                  MPV_PLAYERCTL_STOPPED_STATE |
                                                                  MPV_PLAYERCTL_PAUSED_STATE]
        Possible remote API interface commands for MPV
            echo '{ "command": ["get_property", "pause"] }' | socat - /home/pi/spotify/mpv-socket
            echo '{ "command": ["set_property", "pause", true] }' | socat - /home/pi/spotify/mpv-socket
            echo '{ "command": ["set_property", "pause", false] }' | socat - /home/pi/spotify/mpv-socket
        '''
        commands_list = [MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]

        # use the mpv socket interface to remotely control the playback
        def send_mpv_command(command):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.connect(MPV_SOCKET)
                    sock.sendall(json.dumps(command).encode() + b"\n")
                    response = sock.recv(1024).decode()
                    return json.loads(response)
            except Exception as err:
                oradio_log.error(f"Error upon sending remote control command to MPV API-socket {err}")

        # Get pause status
        pause_property = send_mpv_command({"command": ["get_property", "pause"]})
        pause_status = pause_property['data']
        if pause_status == False:
            playback_status = "Playing"
        else:
            playback_status = "Paused"             

        oradio_log.info(f"player-command = {command}, mpv-playback-status = {playback_status}, spotify_app_status={self.spotify_app_status} ")
        if command in commands_list:
            if command == MPV_PLAYERCTL_PLAY:
                    # always try to change to play. If it is already in play, it will be neglected
                    send_mpv_command({"command": ["set_property", "pause", False]})
                    self.state = MPV_PLAYERCTL_PLAYING_STATE
            elif command == MPV_PLAYERCTL_PAUSE:
                if self.spotify_app_status != SPOTIFY_APP_STATUS_PAUSED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    send_mpv_command({"command": ["set_property", "pause", True]})                    
                    self.state = MPV_PLAYERCTL_PAUSED_STATE
            elif command == MPV_PLAYERCTL_STOP:                
                if self.spotify_app_status != SPOTIFY_APP_STATUS_STOPPED:
                    self.state = MPV_PLAYERCTL_STOPPED_STATE
                    send_mpv_command({"command": ["set_property", "pause", True]})                    
            else:
                self.state = MPV_PLAYERCTL_COMMAND_NOT_FOUND
        else:
            status = MPV_PLAYERCTL_COMMAND_NOT_FOUND
            oradio_log.warning(f"command-status={status}")           
        return (self.state)

def setup_dbus_interface_to_control_mpv_player(player):
    '''
    setup an dbus interface for the mpv to control the playback
    :return status = [  SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE | 
                        SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE |
                        SPOTIFY_CONNECT_STATE_OK |
                        SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND]
    :return player_iface = pointer to the mpv mpris interface
    '''
    player_iface = None
    bus = None
    # check is mpv.service is active
    if oradio_utils.is_service_active("mpv"):
        oradio_log.info("mpv.service is ACTIVE")
        status = SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE
    else:
        oradio_log.error("mpv.service is NOT running")
        status = SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE
        return(status, bus, player_iface)        

    # Access properties using org.freedesktop.DBus.Properties
    props = dbus.Interface(player, MPRIS_DBUS_PROPERTIES)
    playback_status = props.Get(MPRIS_MP2_PLAYER, "PlaybackStatus")
    oradio_log.info(f"MPRIS dbus interface playback status {playback_status}")

    # call PlayPause safely
    player_iface = dbus.Interface(player, MPRIS_MP2_PLAYER)
    
    return (status, player_iface)

if __name__ == "__main__":
    import os
    import time
    import importlib
    from multiprocessing import Queue    
    
    RED_TXT     = "\033[91m"
    GREEN_TXT   = "\033[92m"
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"
    
    ## stop a running Oradio_controls as it may interfere with this test ##
    print("kill Oradio_controls, to prevent interferences with this test module ")
    script = "sudo systemctl stop autostart.service"
#    oradio_utils.run_shell_script(script)


    def spotify_callback():
        pass
    
    def send_a_librespot_event(event):
        '''
        create a librespot event
        :param event = name of event
        '''
        environment= os.environ
        environment['PLAYER_EVENT'] ='None'        
        environment['TRACK_ID']     ='None'
        environment['OLD_TRACK_ID'] ='None'                  
        environment['POSITION_MS']  ='None'      
        environment['VOLUME']       ='None'
        match event:        
            case 'changed':
                environment['PLAYER_EVENT']='changed'
                environment['TRACK_ID']='TRACK#1'
                environment['OLD_TRACK_ID']='TRACK#0'
            case 'started':
                environment['PLAYER_EVENT']='started'
                environment['TRACK_ID']='TRACK#1'
            case 'stopped':
                environment['PLAYER_EVENT']='stopped'
                environment['TRACK_ID']='TRACK#1'
            case 'playing':
                environment['PLAYER_EVENT']='playing'
                environment['TRACK_ID']='TRACK#1'
                environment['POSITION_MS']='1234'                
            case 'paused':
                environment['PLAYER_EVENT']='paused'
                environment['TRACK_ID']='TRACK#1'
                environment['POSITION_MS']='1234'                
            case 'preloading':
                environment['PLAYER_EVENT']='preloading'
                environment['TRACK_ID']='TRACK#1'
            case 'volume_set':                
                environment['PLAYER_EVENT']='volume_set'
                environment['VOLUME']='10'
            case 'volume_changed':                
                environment['PLAYER_EVENT']='volume_changed'
                environment['VOLUME']='10'
            case _:
                environment['PLAYER_EVENT']='EVENT_UNKNOWN'
        os.environ['PLAYER_EVENT']  = environment['PLAYER_EVENT']
        os.environ['TRACK_ID']      = environment['TRACK_ID']
        os.environ['OLD_TRACK_ID']  = environment['OLD_TRACK_ID']                        
        os.environ['POSITION_MS']   = environment['POSITION_MS']
        os.environ['VOLUME']        = environment['VOLUME']
        import librespot_event_handler
        importlib.reload(librespot_event_handler) # will run de event handler
        return 
    
    def discover_oradio_sound_device():
        '''
        discovery of announced spotify-connect services with help of avahi-browse
        '''
        print(YELLOW_TXT+"==================================================================="+END_TXT)
        print(YELLOW_TXT+"Check if Oradio as sound device is discovered. Stop test with CTRL+C"+END_TXT)
        print(YELLOW_TXT+"==================================================================="+END_TXT)        

        script = ["avahi-browse","-d","local","_spotify-connect._tcp"]
        try:
            with subprocess.Popen(script, stdout=PIPE, bufsize=1, universal_newlines=True) as process:
                for line in process.stdout:
                    print(line, end='')  # Outputs the line immediately
                    if "Oradio" in line:
                        oradio_log.info("Oradio device discovered")
                        print(GREEN_TXT+"Oradio device discovered"+END_TXT)                        
                if process.returncode != 0:
                    raise CalledProcessError(process.returncode, script)
        except KeyboardInterrupt:
            process.terminate()
        return()
    
    def monitor_librespot_events():
        '''
        Monitoring librespot events
        '''
        print(YELLOW_TXT+"==============================================================="+END_TXT)
        print(YELLOW_TXT+"Open a Spotify app and connect to a sound device called Oradio"+END_TXT)
        print(YELLOW_TXT+"Check if spotify events are logged, e.g. play, pause, volume"+END_TXT)
        print(YELLOW_TXT+"==============================================================="+END_TXT)        
        msg_queue = Queue()
        spot_con = SpotifyConnect(msg_queue)
        #spot_con.playerctl_command(MPV_PLAYERCTL_PLAY  )                 
        time.sleep(1)
        keyboard_input = input("Press any key to stop monitoring")
        spot_con.shutdown_server()     
        time.sleep(1)           
        return()

    def playback_control_without_spotify_app(): 
        '''
        Playback control of playlist when spotify has started playback and then app is closed
        '''   
        print(YELLOW_TXT+"==============================================================="+END_TXT)
        print(YELLOW_TXT+"Open a Spotify app and connect to a sound device called Oradio"+END_TXT)
        print(YELLOW_TXT+"Play a playlist"+END_TXT)
        print(YELLOW_TXT+"Close the App"+END_TXT)
        print(YELLOW_TXT+"Use play/pause to control the playback"+END_TXT)
        print(YELLOW_TXT+"==============================================================="+END_TXT)        

        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)
        if spot_con.get_state() != SPOTIFY_CONNECT_MPV_STATE_OK:
            oradio_log.error("Spotify Connect Servers not running")
            return()
        
        event_selection = (YELLOW_TXT+"select an optiont:\n"
                           "0-stop playback test\n"
                           "1-play \n"
                           "2-pause \n"
                           "select an event:"+END_TXT
                           )
        loop = "run"
        while loop == "run":
            try:
                event_nr = int(input(event_selection))
            except:
                event_nr = -1
            match event_nr:
                case 0:
                    loop = 'stop'
                case 1:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)                    
                case 2:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                    
                case _:
                    print("invalid selection, try again")
                    event = None
        spot_con.shutdown_server()
        time.sleep(1)           
   
    def playback_control_with_mpv():
        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)
        if spot_con.get_state() != SPOTIFY_CONNECT_MPV_STATE_OK:
            oradio_log.error("Spotify Connect Servers not running")
            return()
        # send a librespot event to the socket
        event_selection = (YELLOW_TXT+"select an event:\n"
                           "0-stop test\n"
                           "1-mpv play command \n"
                           "2-mpv pause command \n"
                           "select an event:"+END_TXT
                           )
        loop = "run"
        while loop == "run":
            try:
                event_nr = int(input(event_selection))
            except:
                event_nr = -1
            match event_nr:
                case 0:
                    spot_con.shutdown_server()
                    event = None
                    loop = 'stop'
                case 1:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)
                case 2:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)
                case _:
                    print("invalid selection, try again")
                    event = None
            print ("return to main test menu")
        return()
        
    def simulate_as_oradio_control(): 
        '''
        simulate a oradio_control, using a playlist from Spotify
        '''   
        print(YELLOW_TXT+"==============================================================="+END_TXT)
        print(YELLOW_TXT+"Open a Spotify app and connect to a sound device called Oradio"+END_TXT)
        print(YELLOW_TXT+"Play a playlist"+END_TXT)
        print(YELLOW_TXT+"Stop test with CTRL+C"+END_TXT)        
        print(YELLOW_TXT+"==============================================================="+END_TXT)        

        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)
        if spot_con.get_state() != SPOTIFY_CONNECT_MPV_STATE_OK:
            oradio_log.error("Spotify Connect Servers not running")
            return()
        
        # create a JSON model for the queueu messages
        status, msg_model = oradio_utils.create_json_model("Messages")
        # to be done check status
        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)   
        while True:
            # wait for a spotify event in the queue
            message = wait_for_queue_messages(msg_queue, msg_model)
            if (message["type"] == MESSAGE_SPOTIFY_TYPE and 
                message["error"] == "None"):
                spotify_state = message["state"]
                print("Message = ", message)
                match spotify_state:
                    ### In Python's structural pattern matching (match-case), 
                    ### unquoted names in a case statement are treated as variables, not constants.
                    ### It binds any value to this variable instead of checking against a predefined constant.
                    ### The if condition ensures it is compared to SPOTIFY_CONNECT_XXXXXX_EVENT.
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_PLAYING_EVENT:
                        print(YELLOW_TXT+"======================================================================"+END_TXT)
                        print(YELLOW_TXT+"Press ENTER to simulate a button press at the Oradio to start playback"+END_TXT)
                        print(YELLOW_TXT+"====================================================================="+END_TXT)
                        time.sleep(3)
                        keyboard_input = input("Press Enter")
                        spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_CONNECTED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)                        
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_STOPPED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_STOP)                        
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_PAUSED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                        
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_DISCONNECTED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_STOP)                        
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                        
                    case _:
                        # do nothing
                        pass
            else:
                oradio_log.info("Not a Spotify event message")
        spot_con.shutdown_server()
        time.sleep(1)           

    def wait_for_queue_messages(queue, msg_model):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        :param msg_model = the json model used for get_msg
        """
        oradio_log.info("Listening for messages in queue")

        while True:
            # Wait for message
            get_msg = queue.get(block=True, timeout=None)
            # port message into json schema
            msg = msg_model(**get_msg)
            message = msg.model_dump()
            
            # Show message received
            oradio_log.info(f"Message received in queue: '{message}'")
            break
        return(message)
            
    def test_event_socket_and_queue():
        '''
        Test the event socket, its observer and the message queue for oradio_controls
        ''' 
        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)                    
        time.sleep(1)
        status, msg_model = oradio_utils.create_json_model("Messages")
        # to be done==> check status !!!
         
        # send a librespot event to the socket
        event_selection = (YELLOW_TXT+"select an event:\n"
                           "0-stop sending events\n"
                           "1-playing event \n"
                           "2-stopped event \n"
                           "3-paused event \n"
                           "select an event:"+END_TXT
                           )
        loop = "run"
        while loop == "run":
            # Clear all items
            while not msg_queue.empty():
                msg_queue.get()
                
            try:
                event_nr = int(input(event_selection))
            except:
                event_nr = -1
            print(event_nr)
            match event_nr:
                case 0:
                    spot_con.shutdown_server()
                    event = None
                    loop = 'stop'
                case 1:
                    event = 'playing'
                case 2:
                    event = 'stopped'                    
                case 3:
                    event = 'paused'                    
                case _:
                    print("invalid selection, try again")
                    event = None
            if event != None:
                send_a_librespot_event(event)                    
                time.sleep(1)
                # wait for message in queue
                message = wait_for_queue_messages(msg_queue, msg_model)
                print(message)
                if event in message['state']:
                    print(GREEN_TXT+f"Correct message event <{message['state']}> received in queue"+END_TXT)                    
                time.sleep(1)
            else:
                loop = 'stop'
                time.sleep(1)                
            print ("return to main test menu")
        return()


    def mpris_player_control_test():
        '''
        Check is the MPRIS player control is working
        '''
        ###### available methods ####################################################################
        # PlayPause()   Toggles between play and pause.
        # Play()        Starts playback (if paused or stopped).
        # Pause()       Pauses playback.
        # Stop()        Stops playback completely.
        # Next()        Skips to the next track (if applicable).
        # Previous()    Skips to the previous track (if applicable).
        # Seek(offset)  Seeks forward/backward by offset microseconds.
        # SetPosition(obj_path, position)    Moves playback to a specific position (in microseconds).
        ############################################################################################

        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)                    
        time.sleep(1)
        status = spot_con.get_state()
        if status == SPOTIFY_CONNECT_MPV_STATE_OK:
        # check player ctl: Play
            spot_con.player_iface.Play()
            print("Play command sent.")
            status = spot_con.get_playback_status()
            if "Playing" in status:
                print(GREEN_TXT+f"Correct Playback Status ={status}"+END_TXT)
            else:
                print(RED_TXT+f"Wrong Playback Status ={status}"+END_TXT)            
            # check player ctl: Pause
            spot_con.player_iface.Pause()        
            print("Pause command sent.")
            status = spot_con.get_playback_status()
            if "Paused" in status:
                print(GREEN_TXT+f"Correct Playback Status ={status}"+END_TXT)
            else:
                print(RED_TXT+f"Wrong Playback Status = {status}"+END_TXT)         
        else:
            print(RED_TXT+f"Error status = {status}"+END_TXT)   
        spot_con.shutdown_server()
        time.sleep(1)
        return

    def spotify_get_status():
        from threading import Event
        msg_queue = Queue()        
        spot_con = SpotifyConnect( msg_queue)
        event = Event()
                   
        def show_status(event):
            while event.is_set():
                playback_status, connected_state = spot_con.get_state()        
                print(f"Playback status={playback_status}, connected_state = {connected_state} ")
                time.sleep(2)
        event.set()     
        get_status_thread = threading.Thread(target=show_status, args=(event,) )
        get_status_thread.start()
        stop = input("Press any key to stop monitoring")
        event.clear()
        get_status_thread.join()
        spot_con.shutdown_server()             
        return

# Run the test function

                
    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Check if the Oradio sound device can be discovered on local mDNS \n"
                       " 2-Monitor librespot events \n"
                       " 3-Test event socket and queue \n"
                       " 4-MPRIS player control test\n"                       
                       " 5-Simulate as Oradio_controls \n"
                       " 6-Playback control without Spotify App \n"
                       " 7-Playback control with mpv control \n"    
                       " 8-Get the status of Spotify_connect \n"                                          
                       "select: "
                       )
 
    # User command loop
    
#    $ mpv --no-video --demuxer=rawaudio --demuxer-rawaudio-format=s16le --demuxer-rawaudio-rate=44100 --demuxer-rawaudio-channels=2 /spotify/librespot-pipe

    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                break
            case 1:
                discover_oradio_sound_device()
            case 2:
                monitor_librespot_events()
            case 3:
                test_event_socket_and_queue()
            case 4:
                mpris_player_control_test()
            case 5:
                simulate_as_oradio_control()
            case 6:
                playback_control_without_spotify_app()
            case 7:
                playback_control_with_mpv()
            case 8:
                spotify_get_status()


            case _:
                print("\nPlease input a valid number\n")
