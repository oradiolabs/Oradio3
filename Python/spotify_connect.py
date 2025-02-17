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
import subprocess
from subprocess import Popen, PIPE, CalledProcessError
import socket
import selectors
import threading
import pydantic
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

import mpv
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
            message = self.queue_message
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
                message["error"] = None
                self.msg_queue.put(message)
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

    def __init__(self,port,callback, msg_queue):
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
        
        self.queue_message         = msg.model_dump()
        self.queue_message["type"] = MESSAGE_SPOTIFY_TYPE
        
        self.mpv_player = mpv.MPV(config=True)
        self.mpv_player.play('/home/pi/spotify/librespot-pipe')
        oradio_utils.logging("info","mpv player started and waiting for playback")
        
        self.sel = selectors.DefaultSelector()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind(("localhost", port))
        self.server_socket.listen(5)
        self.server_socket.setblocking(False)
        oradio_utils.logging("info","event socket opened and listening on port {prt}".format(prt=port))
        
        self.stop_event = threading.Event() # used to stop the observer loop
        
        self.sel.register(self.server_socket, selectors.EVENT_READ, self.accept_connection)

        # Run the observer in a separate thread
        observer_thread = threading.Thread(target=self.observer_loop, daemon=True)
        observer_thread.start()     
        
         
        print("Server Listening ........")

    def shutdown_server(self):
        '''
        Shutting down the socket server and stop the selector-observer
        '''
        print("Shutting down server...")
        self.stop_event.set()  # Stop the observer loop
        self.sel.unregister(self.server_socket)  # Unregister the server socket
        self.server_socket.close()  # Close the socket
        print("Server closed.")

    def playerctl_command(self,command):
        '''
        Send command to playerctl via subprocess
        '''
        commands_list = [MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]
        if command in command_list:
                 
            run_command = ['playerctl',command]
            try:
                status = subprocess.run(run_command,
                                        capture_output=True,
                                        text=True,
                                        check=True  # Raises CalledProcessError if command fails
                                        )
                print("subprocess run status=",status)
            except subprocess.CalledProcessError:
                # Error: Playerctl command failed
                status = PLAYERCTL_COMMAND_ERROR
                oradio_utils.logging("error","fFailure-status={status)")
            except Exception as err:
                status = PLAYERCTL_COMMAND_ERROR
                oradio_utils.logging("error","fError-status={status), error={err}")
        else:
            status = COMMAND_NOT_FOUND
            oradio_utils.logging("warning","fcommand-status={status})")            
        return (status)
    
'''        
librespot --name "Raspberry Pi" --bitrate 320 --backend pipe --device /tmp/librespot-pipe --verbose

# Load the JSON schema file
with open("/home/pi/Oradio3/Python/schemas.json") as f:
    schemas = json.load(f)
# Dynamically create Pydantic models
models = {name: json_schema_to_pydantic(name, schema) for name, schema in schemas.items()}

# create Messages model
Messages = models["Messages"]
#create an instance for this model
msg = Messages(type="none", state="none", error="none", data=[])

message = msg.model_dump()
message["type"] = MESSAGE_SPOTIFY_TYPE

serialized_dict = json.dumps(message).encode('utf-8')
'''

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
        script = ["avahi-browse","-d","local","_spotify-connect._tcp"]
        print(YELLOW_TXT+"Check if OradioLuidspreker is discovered and stop test with CTRL+C"+END_TXT)
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
        spot_con = SpotifyConnect(SPOTIFY_EVENT_SOCKET_PORT,spotify_callback)                    
        time.sleep(1)
        keyboard_input = input("Press any key to stop monitoring")        
        return()
    
    
    def simulate_as_oradio_control(): 
        '''
        simulate a oradio_control, using a playlist from Spotify
        '''   
        print("Open a Spotify app and connect to a sound device called Oradio ")
        print("Check if spotify events are there")
        print("Increase volume on Spotify App")

        pass
#        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#        server_socket.bind(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
#        server_socket.listen(1)        
#        print(YELLOW_TXT+"Socket open and listening. Stop test with CTRL+C"+END_TXT)
#        try:
#            while(True):
#                client_socket, address = server_socket.accept()
#                data = client_socket.recv(1024)
#                print(data)
#        except KeyboardInterrupt:
#            client_socket.close()
#            server_socket.close()

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        oradio_utils.logging("info", "Listening for messages in queue")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            oradio_utils.logging("info", f"Message received in queue: '{message}'")
            break
        return(message)
            
    def test_event_socket_and_queue():
        '''
        Test the event socket, its observer and the message queue for oradio_controls
        ''' 
        msg_queue = Queue()
        spot_con = SpotifyConnect(SPOTIFY_EVENT_SOCKET_PORT,spotify_callback, msg_queue)                    
        time.sleep(1)
        # send a librespot event to the socket
        event_selection = ("select an event:\n"
                           "0-stop sending events\n"
                           "1-playing event \n"
                           "2-stopped event \n"
                           "3-paused event \n"
                           "select an event:"
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
                message = check_messages(msg_queue)
                if event in message['state']:
                    oradio_utils.logging("success","Correct message event <{msg}> received in queue".format(msg=message['state']))
                time.sleep(1)            
        return()
            
    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Check if Oradio Speaker can be discovered on local mDns \n"
                       " 2-Monitor librespot events \n"
                       " 3-Test event socket and queue \n"
                       " 4-Simulate as Oradio_controls \n"
                       " 5-xxxxx\n"
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
                pass
            case _:
                print("\nPlease input a valid number\n")
