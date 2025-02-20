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

#from pydbus import SessionBus
from dbus import SessionBus
import json

#### Oradio modules  #####
import oradio_utils
from oradio_const import *

class SpotifyConnect():

    def accept_connection(self,sock):
        '''
        Accept a connection. 
        The socket must be bound to an address and listening for connections. 
        The return value is a pair (conn, address) where conn is a new socket object usable to send and receive data on the connection, 
        and address is the address bound to the socket on the other end of the connection.        
        '''
        conn, address = sock.accept()
        oradio_utils.logging("info", "Socket-connection from {addr}".format(addr=address))
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
        rec_data = conn.recv(1024) # max buffer size is 1024
        if rec_data:
            librespot_data = json.loads(rec_data.decode())  #
            oradio_utils.logging("info", "Data received from socket {sdat}".format(sdat = librespot_data ))
            librespot_event = librespot_data["player_event"]
            message = {}
            match librespot_event:        
                case 'playing':
                    message["state"] = SPOTIFY_CONNECT_PLAYING_EVENT
                case 'paused':
                    message["state"] = SPOTIFY_CONNECT_PAUSED_EVENT
                case 'stopped':
                    message["state"] = SPOTIFY_CONNECT_STOPPED_EVENT
                case 'session_connected':
                    message["state"] = SPOTIFY_CONNECT_CONNECTED_EVENT
                case 'session_disconnected':
                    message["state"] = SPOTIFY_CONNECT_DISCONNECTED_EVENT
                case 'session_client_changed':
                    message["state"] = SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT
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
        oradio_utils.logging("info","The observer thread which listens to incoming message is running")
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

    def __init__(self,callback, msg_queue):
        '''
         setup an observer listening to socket for incoming messages
        '''
        self.callback   = callback
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
        msg = Messages(type="none", state="none", error="none", data=[])

        ## define the message model for the put message in the queue         
        self.queue_put_mesg         = msg.model_dump()
        self.queue_put_mesg["type"] = MESSAGE_SPOTIFY_TYPE
        
        # the mpv.service should be running
        self.dbus=SessionBus()
        time.sleep(10)
        mpv_service_found = self.dbus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
        if mpv_service_found:
            self.state = SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE
            self.player = self.dbus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")            
            oradio_utils.logging("info","mpv player initialized and started and waiting for playback")            
        else:
            self.state = SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE
        ## initialize a mpv player
        #self.mpv_player = mpv.MPV(config=True)
        #self.mpv_player.play('/home/pi/spotify/librespot-pipe')
        #if self.mpv_player.idle_active:
        #    print("MPV is idle (not playing any file).")
        #else:
        #    print("MPV is active.")
#        run_command = 'mpv /home/pi/spotify/librespot-pipe'
#        subprocess.Popen(run_command, shell=True)

        
        if self.state == SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE:
            self.sel = selectors.DefaultSelector()
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
            self.server_socket.listen(5)
            self.server_socket.setblocking(False)
            oradio_utils.logging("info","event socket opened and listening on port {prt}".format(prt=SPOTIFY_EVENT_SOCKET_PORT))
            
            self.stop_event = threading.Event() # used to stop the observer loop
            
            self.sel.register(self.server_socket, selectors.EVENT_READ, self.accept_connection)
    
            # Run the observer in a separate thread
            observer_thread = threading.Thread(target=self.observer_loop, daemon=True)
            observer_thread.start()     
            self.state = SPOTIFY_CONNECT_SERVERS_RUNNING
            oradio_utils.logging("info","MPV active and socket server Listening to spotify events ........")


    def shutdown_server(self):
        '''
        Shutting down the socket server and stop the selector-observer
        '''
        print("Shutting down server...")
        self.stop_event.set()  # Stop the observer loop
        self.sel.unregister(self.server_socket)  # Unregister the server socket
        self.server_socket.close()  # Close the socket
        print("Server closed.")

    def get_state(self):
        '''
        Return the actual state of the Spotify Connect servers and related events
        :return self.state = current state
        '''
        return(self.state)
    
    def playerctl_command(self,command):
        '''
        Send command to playerctl via subprocess
        '''
        commands_list = [MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]
        print("player-command =",command)
        if command in commands_list:
            print("player-command =",command)            
            if command == MPV_PLAYERCTL_PLAY:
                self.player.Play()
                self.state = MPV_PLAYERCTL_PLAYING_STATE
            elif command == MPV_PLAYERCTL_PAUSE:
                self.player.Pause()
                self.state = MPV_PLAYERCTL_PAUSED_STATE
            elif command == MPV_PLAYERCTL_STOP:                
                self.player.Stop()
                self.state = MPV_PLAYERCTL_STOPPED_STATE
            else:
                self.state = MPV_PLAYERCTL_COMMAND_NOT_FOUND
        else:
            status = MPV_PLAYERCTL_COMMAND_NOT_FOUND
            oradio_utils.logging("warning","command-status={sts}".format(sts=status))            
        return (status)

if __name__ == "__main__":
    import os
    import time
    import importlib
    from multiprocessing import Queue    
    
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"    
    
    ## stop a running Oradio_controls as it may interfere with this test ##
    print("kill Oradio_controls, to prevent interferences with this test module ")
    script = "sudo pkill -9 -f oradio_control.py"
#    oradio_utils.run_shell_script(script)

    def create_message_model():
        # create a message object based on json schema 
        # Load the JSON schema file
        with open(JSON_SCHEMAS_FILE) as f:
            schemas = json.load(f)
        # Dynamically create Pydantic models
        models = {name: oradio_utils.json_schema_to_pydantic(name, schema) for name, schema in schemas.items()}
        # create Messages model
        Messages = models["Messages"]
        return(Messages)
         
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
        print("new event =",event)        
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
    
    def discover_oradio_speaker():
        '''
        discovery of announced spotify-connect services with help of avahi-browse
        '''
        print(YELLOW_TXT+"==============================================================="+END_TXT)
        print(YELLOW_TXT+"Check if OradioLuidspreker is discovered and stop test with CTRL+C"+END_TXT)
        print(YELLOW_TXT+"==============================================================="+END_TXT)        

        script = ["avahi-browse","-d","local","_spotify-connect._tcp"]
        try:
            with subprocess.Popen(script, stdout=PIPE, bufsize=1, universal_newlines=True) as process:
                for line in process.stdout:
                    print(line, end='')  # Outputs the line immediately
                    if "Oradio" in line:
                        oradio_utils.logging("success","Oradio device discovered")
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
        spot_con = SpotifyConnect(spotify_callback, msg_queue)                    
        time.sleep(1)
        keyboard_input = input("Press any key to stop monitoring")        
        return()
    
    
    def simulate_as_oradio_control(): 
        '''
        simulate a oradio_control, using a playlist from Spotify
        '''   
        print(YELLOW_TXT+"==============================================================="+END_TXT)
        print(YELLOW_TXT+"Open a Spotify app and connect to a sound device called Oradio"+END_TXT)
        print(YELLOW_TXT+"Play a playlist"+END_TXT)
        print(YELLOW_TXT+"==============================================================="+END_TXT)        

        msg_queue = Queue()
        spot_con = SpotifyConnect(spotify_callback, msg_queue)
        if spot_con.get_state() != SPOTIFY_CONNECT_SERVERS_RUNNING:
            oradio_utils.logging("error","Spotify Connect Servers not running")
            return()
        
        # create a JSON model for the queueu messages
        msg_model = create_message_model()

        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)   
        while True:
            # wait for a spotify event in the queue
            message = wait_for_queue_messages(msg_queue, msg_model)
            if (message["type"] == MESSAGE_SPOTIFY_TYPE and 
                message["error"] == "None"):
                spotify_state = message["state"]
                match spotify_state:
                    ### In Python's structural pattern matching (match-case), 
                    ### unquoted names in a case statement are treated as variables, not constants.
                    ### It binds any value to this variable instead of checking against a predefined constant.
                    ### The if condition ensures it is compared to SPOTIFY_CONNECT_XXXXXX_EVENT.
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_PLAYING_EVENT:
                        print(YELLOW_TXT+"======================================================================"+END_TXT)
                        print(YELLOW_TXT+"Press ENTER to simulate a button press of the ON-button at the Oradio"+END_TXT)
                        print(YELLOW_TXT+"====================================================================="+END_TXT)
                        keyboard_input = input("Press Enter as ON-button")
                        spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)                                
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_STOPPED_EVENT:
                        print(YELLOW_TXT+"======================================================================"+END_TXT)
                        print(YELLOW_TXT+"Press ENTER to simulate a button press of the OFF-button at the Oradio"+END_TXT)
                        print(YELLOW_TXT+"====================================================================="+END_TXT)
                        keyboard_input = input("Press Enter as OFF-button")
                        spot_con.playerctl_command(MPV_PLAYERCTL_STOP)                                           
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_PAUSED_EVENT:
                        print(YELLOW_TXT+"======================================================================"+END_TXT)
                        print(YELLOW_TXT+"Press ENTER to simulate a button press of the OFF-button at the Oradio"+END_TXT)
                        print(YELLOW_TXT+"====================================================================="+END_TXT)
                        keyboard_input = input("Press Enter as OFF-button")
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                        
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_CONNECTED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                    
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_DISCONNECTED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                    
                    case spotify_state if spotify_state == SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)                    
                    case _:
                        spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)
            else:
                oradio_utils.logging("info","Not a Spotify event message")


    def wait_for_queue_messages(queue, msg_model):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        :param msg_model = the json model used for get_msg
        """
        oradio_utils.logging("info", "Listening for messages in queue")

        while True:
            # Wait for message
            get_msg = queue.get(block=True, timeout=None)
            # port message into json schema
            msg = msg_model(**get_msg)
            message = msg.model_dump()
            
            # Show message received
            oradio_utils.logging("info", f"Message received in queue: '{message}'")
            break
        return(message)
            
    def test_event_socket_and_queue():
        '''
        Test the event socket, its observer and the message queue for oradio_controls
        ''' 
        msg_queue = Queue()
        spot_con = SpotifyConnect(spotify_callback, msg_queue)                    
        time.sleep(1)
        msg_model = create_message_model()        
        # send a librespot event to the socket
        event_selection = (YELLOW_TXT+"select an event:\n"
                           "0-stop sending events\n"
                           "1-playing event \n"
                           "2-stopped event \n"
                           "3-paused event \n"
                           "select an event:"+END_TXT
                           )
        while True:
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
                    break
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
                    oradio_utils.logging("success","Correct message event <{msg}> received in queue".format(msg=message['state']))
                time.sleep(1)            
        return()

    def mpris_player_control_test():
        from dbus import SessionBus
        ## initialize a mpv player
#        mpv_player = mpv.MPV(config=True)
#        mpv_player.play('/home/pi/spotify/librespot-pipe')
#        if mpv_player.idle_active:
#            print("MPV is idle (not playing any file).")
#        else:
#            print("MPV is active.")
        # Connect to the session bus
        bus = SessionBus()
        
        # Get the mpv MPRIS2 interface
        player = bus.get("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
        
        # Print playback status
        print("Playback Status:", player.PlaybackStatus)
        
        # Toggle play/pause
        player.PlayPause()
        
        # Seek forward by 10 seconds
        player.Seek(10 * 10**6)  # MPRIS uses microseconds
        
        # Get current metadata (like title, artist, etc.)
        metadata = player.Metadata
        print("Now Playing:", metadata.get("xesam:title", "Unknown"))
        
        # Stop playback
        # player.Stop()


                
    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Check if Oradio Speaker can be discovered on local mDns \n"
                       " 2-Monitor librespot events \n"
                       " 3-Test event socket and queue \n"
                       " 4-Simulate as Oradio_controls \n"
                       " 5-MPRIS player control test\n"
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
                discover_oradio_speaker()
            case 2:
                monitor_librespot_events()
            case 3:
                test_event_socket_and_queue()
            case 4:
                simulate_as_oradio_control()
            case 5:
                mpris_player_control_test()
            case _:
                print("\nPlease input a valid number\n")
