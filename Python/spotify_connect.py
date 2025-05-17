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
        - sudo apt-get -y install curl 
        - curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
        - python -m pip install git+https://github.com/kokarare1212/librespot-python
        - sudo apt install avahi-utils
        - sudo apt install -y libdbus-1-dev libdbus-glib-1-dev 
                    python3-setuptools python3-wheel pkg-config meson        
        - python -m pip install pydbus
        - sudo apt install -y playerctl

        debugger:
        - python -m pip install pydevd
    :Documentation
        * D-bus : https://en.wikipedia.org/wiki/D-Bus
        * D-bus python: https://dbus.freedesktop.org/doc/dbus-python/tutorial.html
        * select - Waiting for I/O completion:https://docs.python.org/3/library/select.html
        * MPRIS Media Player: https://wiki.archlinux.org/title/MPRIS
"""

#pylint: disable=line-too-long, logging-fstring-interpolation
#pylint: disable=unused-wildcard-import, wildcard-import
import subprocess
from subprocess import PIPE, CalledProcessError
import socket
import selectors
import threading
import os
import time
import json
from dataclasses import dataclass
import dbus
import alsaaudio

#### Oradio modules  #####
import oradio_utils
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
#### Module test status #####
TEST_SUCCESS    = "test success"
TEST_ERROR      = 'test error'
#### JSON MODEL ###########
MESSAGE_MODEL = "Messages"
#### MPV_PLAYER COMMANDS ####
MPV_PLAYERCTL_PLAY  = "play"
MPV_PLAYERCTL_PAUSE = "pause"
MPV_PLAYERCTL_STOP  = "stop"
#### MPV_PLAYER STATES ####
MPV_PLAYERCTL_PLAYING_STATE = "Playing"
MPV_PLAYERCTL_STOPPED_STATE = "Stopped"
MPV_PLAYERCTL_PAUSED_STATE  = "Paused"
#### MPV PLAYER COMMAND status ########
MPV_PLAYERCTL_COMMAND_NOT_FOUND = "playerctl command not found"
MPV_PLAYERCTL_COMMAND_ERROR     = "playerctl command failed"

MPRIS_MPV_PLAYER            = "org.mpris.MediaPlayer2.mpv"
MPRIS_MEDIA_PLAYER          = "/org/mpris/MediaPlayer2"
MPRIS_MP2_PLAYER            = "org.mpris.MediaPlayer2.Player"
MPRIS_DBUS_PROPERTIES       = "org.freedesktop.DBus.Properties"
MPRIS_MEDIA_PLAYER_SEARCH   = "org/mpris/MediaPlayer2."

###### SPOTIFY Application events and states #############
SPOTIFY_APP_STATUS_PLAYING  = "Playing"
SPOTIFY_APP_STATUS_STOPPED  = "Stopped"
SPOTIFY_APP_STATUS_PAUSED   = "Paused"
SPOTIFY_APP_STATUS_CLOSED   = "Closed"
SPOTIFY_APP_STATUS_ACTIVE   = "Active"
SPOTIFY_APP_STATUS_CLIENT_CHANGED   = "Client changed"

###### SPOTIFY CONNECTION STATUS #####
SPOTIFY_CONNECT_CONNECTED       = "Spotify Connect is connected"
SPOTIFY_CONNECT_NOT_CONNECTED   = "Spotify Connect is NOT connected"

###### LIBRESPOT EVENTS #########################
LIBRESPOT_EVENT_PLAYING         = "playing"
LIBRESPOT_EVENT_PAUSED          = "paused"
LIBRESPOT_EVENT_CONNECTED       = "session_connected"
LIBRESPOT_EVENT_DISCONNECTED    = "session_disconnected"
LIBRESPOT_EVENT_CLIENT_CHANGED  = "session_client_changed"
LIBRESPOT_EVENT_CHANGED         = "changed"
LIBRESPOT_EVENT_STARTED         = "started"
LIBRESPOT_EVENT_STOPPED         = "stopped"
LIBRESPOT_EVENT_PRELOADING      = "preloading"
LIBRESPOT_EVENT_VOLUME          = "volume_set"
LIBRESPOT_EVENT_VOLUME_CHANGED  = "volume_changed"
LIBRESPOT_EVENT_NONE            = "no event"

MESSAGE_RECEIVED = "message received"
MESSAGE_TIMEOUT  = "message timeout"

@dataclass
class Spotify:
    '''
    Spotify class attributes
    '''
    app_status:str
    connect_state: str
    client_id:str

class SpotifyConnect():
    '''
    class to connect with Spotifyc Connect and playback control
    '''

    def __init__(self, msg_queue):
        '''
         setup an observer listening to socket for incoming messages
        '''
        self.msg_queue  = msg_queue

        # create a message object based on json schema
        # Load the JSON schema file
        with open(JSON_SCHEMAS_FILE, encoding="utf8") as f:
            schemas = json.load(f)
        # Dynamically create Pydantic models
        models = {name: oradio_utils.json_schema_to_pydantic(name, schema)
                  for name, schema in schemas.items()}
        # create Messages model
        messages = models[MESSAGE_MODEL]
        #create an instance for this model
        queue_messages = messages(type="none", state="none", error="none", data=[])
        ## define the message model for the put message in the queue
        self.queue_put_mesg         = queue_messages.model_dump()
        self.queue_put_mesg["type"] = MESSAGE_SPOTIFY_TYPE
        # set alsa volume for spotify to max, mpv controls muting
        self._amixer_spotify_sound(100)
        self.state , mpv_player = self._get_mpv_player()
        self.spotify = Spotify(app_status = SPOTIFY_APP_STATUS_CLOSED,
                               connect_state = SPOTIFY_CONNECT_NOT_CONNECTED,
                               client_id = "None")
        self.stop_event = threading.Event() # used to stop the observer loop
        self.stop_event.clear()
        if self.state == SPOTIFY_CONNECT_MPV_STATE_OK:
            self.state, self.player_iface= _setup_dbus_interface_to_control_mpv_player(mpv_player)
        else:
            self.player_iface = None

        self.sel = None

        playback_status = self._get_playback_status()
        if playback_status == MPV_PLAYERCTL_PLAYING_STATE:
            self.spotify.connect_state = SPOTIFY_CONNECT_CONNECTED
        print(f"connect_state={self.spotify.connect_state}, playback_status= {playback_status}")
        if self.state == SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE:
            #self.player_iface = player_iface
            # setup a observer (selector) for socket listening to incoming messages
            self.sel = selectors.DefaultSelector()
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
            self.server_socket.listen(5)
            self.server_socket.setblocking(False)
            oradio_log.info(f"event socket opened and listening on port {SPOTIFY_EVENT_SOCKET_PORT}")
            self.sel.register(self.server_socket, selectors.EVENT_READ, self._accept_connection)
            observer_thread = threading.Thread(target=self._observer_loop, daemon=True)
            observer_thread.start()
            self.state = SPOTIFY_CONNECT_MPV_STATE_OK
            oradio_log.info("MPV active and socket server Listening to spotify events ........")

    ################ Public  methods ############################################################

    def get_state(self):
        '''
        Return the actual state of the Spotify Connect servers and related events
        :return spotify_app_status = [ SPOTIFY_APP_STATUS_PLAYING | SPOTIFY_APP_STATUS_STOPPED |
                                SPOTIFY_APP_STATUS_PAUSED | SPOTIFY_APP_STATUS_DISCONNECTED |
                                SPOTIFY_APP_STATUS_CONNECTED | SPOTIFY_APP_STATUS_CLIENT_CHANGED]
        :return connect_state=[ SPOTIFY_CONNECT_CONNECTED | SPOTIFY_CONNECT_NOT_CONNECTED]
        '''

        return (self.spotify.app_status, self.spotify.connect_state)

    def pause(self):
        '''
        pausing the sound with playerctl command
        '''
        self.playerctl_command(MPV_PLAYERCTL_PAUSE)

    def play(self):
        '''
        playing the sound with playerctl command
        '''
        self.playerctl_command(MPV_PLAYERCTL_PLAY)

    def playerctl_command(self,command):
        '''
        Send command to playerctl via mpris player interface
        :param command = player command = [ MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]
        :return self.state = state of mpv mpris player-control = 
                    [MPV_PLAYERCTL_PLAYING_STATE | MPV_PLAYERCTL_STOPPED_STATE | MPV_PLAYERCTL_PAUSED_STATE]
        '''
        commands_list = [MPV_PLAYERCTL_PLAY, MPV_PLAYERCTL_PAUSE, MPV_PLAYERCTL_STOP]
        #Check if mpv is available on D-Bus
        _, _ = self._get_mpv_player()
        #bus_list_names = self.bus.list_names()
        player = self.bus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
        playback_status = self._get_playback_status()

        oradio_log.info(f"player-command = {command}, mpv-playback-status = {playback_status}, spotify.app_status={self.spotify.app_status} ")
        if command in commands_list:
            if command == MPV_PLAYERCTL_PLAY:
                # always try to play,  if already in playing mode, it will be rejected
                # Get Player interface
                player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")
                player_iface.Play()
                self.state = MPV_PLAYERCTL_PLAYING_STATE
            elif command == MPV_PLAYERCTL_PAUSE:
                if self.spotify.app_status!= SPOTIFY_APP_STATUS_PAUSED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")
                    player_iface.Pause()
                    self.state = MPV_PLAYERCTL_PAUSED_STATE
            elif command == MPV_PLAYERCTL_STOP:
                if self.spotify.app_status!= SPOTIFY_APP_STATUS_STOPPED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    player_iface = dbus.Interface(player, "org.mpris.MediaPlayer2.Player")
                    player_iface.Stop()
                    self.state = MPV_PLAYERCTL_STOPPED_STATE
            else:
                self.state = MPV_PLAYERCTL_COMMAND_NOT_FOUND
        else:
            self.status = MPV_PLAYERCTL_COMMAND_NOT_FOUND
            oradio_log.warning(f"command-status={status}")
        return self.state

    ################ Private methods ############################################################

    def _accept_connection(self,sock):
        '''
        Accept a connection.
        The socket must be bound to an address and listening for connections.
        The return value is a pair (conn, address) where conn is a new socket object 
        usable to send and receive data on the connection,
        and address is the address bound to the socket on the other end of the connection.
        '''
        conn, address = sock.accept()
        oradio_log.info(f"Socket-connection from {address}")
        conn.setblocking(False)
        self.sel.register(conn, selectors.EVENT_READ, self._read_message)

    def _process_librespot_events(self, event):
        '''
        The librespot event will be used to update the state of connect_state 
        and spotify_app_state.
        :param event [str] = librespot events = [LIBRESPOT_EVENT_PLAYING |
                                                LIBRESPOT_EVENT_PAUSED |
                                                LIBRESPOT_EVENT_CONNECTED |
                                                LIBRESPOT_EVENT_CLIENT_CHANGED |
                                                LIBRESPOT_EVENT_DISCONNECTED ]
        connect_state = [ SPOTIFY_CONNECT_NOT_CONNECTED | SPOTIFY_CONNECT_CONNECTED]
        spotify_app_state     = [ SPOTIFY_APP_STATUS_ACTIVE | SPOTIFY_APP_STATUS_CLOSED ]
        :return spotify_event_state = the processed event

        '''
        spotify_event_state = SPOTIFY_CONNECT_NO_EVENT
        match self.spotify.connect_state:
            case self.spotify.connect_state if self.spotify.connect_state == SPOTIFY_CONNECT_NOT_CONNECTED:
                match event:
                    case event if event == LIBRESPOT_EVENT_CONNECTED:
                        ## enter actions to enter new state
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_ACTIVE
                        spotify_connect_new_state = SPOTIFY_CONNECT_CONNECTED
                        spotify_event_state       = SPOTIFY_CONNECT_CONNECTED_EVENT
                    case _:
                        spotify_connect_new_state   = SPOTIFY_CONNECT_NOT_CONNECTED
                        spotify_app_new_status      = SPOTIFY_APP_STATUS_CLOSED

            case self.spotify.connect_state if self.spotify.connect_state == SPOTIFY_CONNECT_CONNECTED:
                match event:
                    case event if event == LIBRESPOT_EVENT_DISCONNECTED:
                        ## enter actions to enter new state
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_CLOSED
                        spotify_connect_new_state = SPOTIFY_CONNECT_NOT_CONNECTED
                        spotify_event_state       = SPOTIFY_CONNECT_DISCONNECTED_EVENT
                    case event if event == LIBRESPOT_EVENT_PLAYING:
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_ACTIVE
                        spotify_connect_new_state = SPOTIFY_CONNECT_CONNECTED
                        spotify_event_state       = SPOTIFY_CONNECT_PLAYING_EVENT
                    case event if event == LIBRESPOT_EVENT_PAUSED:
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_ACTIVE
                        spotify_connect_new_state = SPOTIFY_CONNECT_CONNECTED
                        spotify_event_state       = SPOTIFY_CONNECT_PAUSED_EVENT
                    case event if event == LIBRESPOT_EVENT_CONNECTED:
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_ACTIVE
                        spotify_connect_new_state = SPOTIFY_CONNECT_CONNECTED
                        spotify_event_state       = SPOTIFY_CONNECT_CONNECTED_EVENT
                    case _:
                        spotify_connect_new_state = SPOTIFY_CONNECT_CONNECTED
                        spotify_app_new_status    = SPOTIFY_APP_STATUS_ACTIVE
                        spotify_event_state       = SPOTIFY_CONNECT_NO_EVENT
            case _:
                oradio_log.warning(f"Invalid state {self.spotify.connect_state}")
                # corrective action is to set to SPOTIFY_CONNECT_NOT_CONNECTED
                spotify_connect_new_state = SPOTIFY_CONNECT_NOT_CONNECTED
                spotify_app_new_status    = SPOTIFY_APP_STATUS_CLOSED

        self.spotify.connect_state = spotify_connect_new_state
        self.spotify.app_status    = spotify_app_new_status
        return spotify_event_state


    def _read_message(self,conn):
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
            librespot_data = json.loads(rec_data.decode())#
            oradio_log.info(f"Data received from socket {librespot_data}")
            librespot_event = librespot_data["player_event"]
            if "client_id" in librespot_data:
                librespot_client_id = librespot_data["client_id"]
            else:
                librespot_client_id = "None"
            self.spotify.client_id = librespot_client_id
            spotify_event_state = self._process_librespot_events(librespot_event)
            print(f"Librespot EVENT={librespot_event}, spotify_event_state = {spotify_event_state}")
            if spotify_event_state != SPOTIFY_CONNECT_NO_EVENT:
                self.queue_put_mesg["type"]  = MESSAGE_SPOTIFY_TYPE
                self.queue_put_mesg["state"] = spotify_event_state
                self.queue_put_mesg["error"] = MESSAGE_NO_ERROR
                self.queue_put_mesg["data"]  = []
                oradio_log.debug(f"New message in queue={self.queue_put_mesg}")
                self.msg_queue.put(self.queue_put_mesg)
        else:
            # if recv() returns an empty bytes object, b'',
            # that signals that the client closed the connection and the loop is terminated.
            self.sel.unregister(conn)
            conn.close()

    def _observer_loop(self):
        '''
        Start an observer for the selected socket,\
         observer will initiate a callback upon an EVENT_READ
        '''
        oradio_log.info("The observer thread which listens to \
                            incoming message is running")
        while not self.stop_event.is_set():
            events = self.sel.select(timeout=None)
            # events has a list of events. A tuple of a key and event_mask
            # the event_mask shows which selectors were enabled (e.g., selectors.EVENT_READ, selectors.EVENT_WRITE).
            # the key is a selectorkey object and each key represents a socket that has some activity
            # (incoming connection, data received, etc.).
            for key, event_mask in events:
                # for each key a callback is done
                callback = key.data
                # the key holds the registered callback and socket
                callback(key.fileobj)

    def _close_dbus_session(self):
        self.bus.close()

    def _get_mpv_player(self):
        '''
        get the mpv player via the MPRIS dbus interface
        '''
        def get_mpris_players():
            found=False
            bus = dbus.SessionBus()  # Refresh the session bus
            players = [service for service in bus.list_names()\
                        if service.startswith("org.mpris.MediaPlayer2.")]
            if "org.mpris.MediaPlayer2.mpv" in players:
                self.state = SPOTIFY_CONNECT_MPV_STATE_OK
                self.bus = bus
                found = True

            return found, bus,players

        self.state = SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE
        mpv_player = "None"
        player_found, bus, mpris_players = get_mpris_players()
        oradio_log.info(f"first try : Available MPRIS Players: {mpris_players}")
        if not player_found:
            time.sleep(1)  # Wait 1 second before retrying
            player_found, bus, mpris_players = get_mpris_players()
            oradio_log.info(f"second try  : Available MPRIS Players: {mpris_players}")
        if not player_found:
            oradio_utils.run_shell_script("systemctl --user restart mpv")
            time.sleep(2)  # Wait 2 second before retrying            
            player_found, bus, mpris_players = get_mpris_players()
            oradio_log.info(f"try after mpv restart  : Available MPRIS Players: {mpris_players}")
        if self.state == SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE:
            oradio_log.error("Too many SessionBus retries, mpv not registered at SessionBus")
        else:
            # Check if mpv is in the list
            if MPRIS_MPV_PLAYER not in mpris_players:
                oradio_log.error("mpv is not found in MPRIS players!")
                self.state = SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND
                mpv_player = "None"
            else:
                oradio_log.info("mpv is found in MPRIS players!")
                self.state = SPOTIFY_CONNECT_MPV_STATE_OK
                # Get the mpv MPRIS2 interface
                mpv_player = self.bus.get_object(MPRIS_MPV_PLAYER, MPRIS_MEDIA_PLAYER)
        return(self.state, mpv_player)

    def _amixer_spotify_sound(self, volume):
        '''
        set sound level for VolumeSpotCon1
        :param volume [int]: 0...100
        '''
        mixer = alsaaudio.Mixer("VolumeSpotCon1")
        mixer.setvolume(volume)

    def _shutdown_server(self):
        '''
        Shutting down the socket server and stop the selector-observer
        '''
        oradio_log.info("Shutting down server...")
        self.stop_event.set()  # Stop the observer loop
        if self.sel is not None and self.server_socket:
            try:
                self.sel.unregister(self.server_socket)  # Unregister the server socket
            except (KeyError, ValueError) as err:
                oradio_log.warning(f"Socket not registered or already closed: {err}")
            try:
                self.server_socket.close()  # Close the socket
            except Exception as err:
                oradio_log.warning(f"Error closing server socket: {err}")
        oradio_log.info("Server closed.")

    def _get_playback_status(self):
        '''
        Retrieve the playback status of mpv via MPRIS2
        :return playback_status = [MPV_PLAYERCTL_PLAYING_STATE |
                                    MPV_PLAYERCTL_STOPPED_STATE |
                                    MPV_PLAYERCTL_PAUSED_STATE]
        '''
        try:
            player = self.bus.get_object("org.mpris.MediaPlayer2.mpv", "/org/mpris/MediaPlayer2")
            props = dbus.Interface(player, "org.freedesktop.DBus.Properties")
            props_status = props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
            playback_status = str(props_status)
        except dbus.exceptions.DBusException:
            playback_status = "mpv not running or MPRIS not available"
        return playback_status

    def _mpv_playerctl_command(self,command):
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
                    command_status = json.loads(response)
            except socket.error as err:
                oradio_log.error(f"Error upon sending remote control command\
                                     to MPV API-socket {err}")
                command_status = "Socket Error"
            return command_status
        # Get pause status
        pause_property = send_mpv_command({"command": ["get_property", "pause"]})
        pause_status = pause_property['data']
        if pause_status is False:
            playback_status = "Playing"
        else:
            playback_status = "Paused"

        oradio_log.info(f"player-command = {command},\
                          mpv-playback-status = {playback_status},\
                          spotify.app_status={self.spotify.app_status} ")
        if command in commands_list:
            if command == MPV_PLAYERCTL_PLAY:
                # always try to change to play. If it is already in play, it will be neglected
                send_mpv_command({"command": ["set_property", "pause", False]})
                self.state = MPV_PLAYERCTL_PLAYING_STATE
            elif command == MPV_PLAYERCTL_PAUSE:
                if self.spotify.app_status != SPOTIFY_APP_STATUS_PAUSED:
                    # do not send mpv command as mpv was killed by librespot, so no sound
                    send_mpv_command({"command": ["set_property", "pause", True]})
                    self.state = MPV_PLAYERCTL_PAUSED_STATE
            elif command == MPV_PLAYERCTL_STOP:
                if self.spotify.app_status!= SPOTIFY_APP_STATUS_STOPPED:
                    self.state = MPV_PLAYERCTL_STOPPED_STATE
                    send_mpv_command({"command": ["set_property", "pause", True]})
            else:
                self.state = MPV_PLAYERCTL_COMMAND_NOT_FOUND
        else:
            oradio_log.warning(f"command-status={MPV_PLAYERCTL_COMMAND_NOT_FOUND}")
        return self.state

def _setup_dbus_interface_to_control_mpv_player(player):
    '''
    setup an dbus interface for the mpv to control the playback
    :return status = [  SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE | 
                        SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE |
                        SPOTIFY_CONNECT_STATE_OK |
                        SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND]
    :return player_iface = pointer to the mpv mpris interface
    '''
    player_iface = None
    # check is mpv.service is active
    if oradio_utils.is_user_service_active("mpv"):
        oradio_log.info("mpv.service is ACTIVE")
        connect_status = SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE
    else:
        oradio_log.error("mpv.service is NOT running")
        connect_status = SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE
        return(status, player_iface)
    # Access properties using org.freedesktop.DBus.Properties
    props = dbus.Interface(player, MPRIS_DBUS_PROPERTIES)
    playback_status = props.Get(MPRIS_MP2_PLAYER, "PlaybackStatus")
    oradio_log.info(f"MPRIS dbus interface playback status {playback_status}")
    # call PlayPause safely
    player_iface = dbus.Interface(player, MPRIS_MP2_PLAYER)
    return (connect_status, player_iface)

if __name__ == "__main__":
    import importlib
    from threading import Event
    from multiprocessing import Queue
    from queue import Empty
    import select
    import argparse

#pylint: disable=protected-access,too-many-lines 
#pylint: disable=too-many-locals, too-many-branches, too-many-statements

    parser = argparse.ArgumentParser(description='Debug and Testing options')
    MESSAGE_DEBUG = 'DEBUG options are:  [ no | remote ]'
    parser.add_argument('-d', '--debug', type = str, nargs='?', const='no', help=MESSAGE_DEBUG )
    MESSAGE_TEST = 'TEST options are:  [ no | yes ]'
    parser.add_argument('-t', '--test', type = str, nargs='?', const='no', help=MESSAGE_TEST )

    args = parser.parse_args()
    system_debug = args.debug
    allowed_options = [None, "no","remote"]
    if not system_debug in allowed_options:
        parser.error(MESSAGE_DEBUG)
    print("Debug option =",system_debug)

    system_testing = args.test
    if system_debug != "remote":
        allowed_options = [None, "no","yes"]
        if not system_testing in allowed_options:
            parser.error(MESSAGE_TEST)
        print("Test option =",system_testing)

    RED_TXT     = "\033[91m"
    GREEN_TXT   = "\033[92m"
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"

    if system_debug == 'remote':
        print("remote debugging")
        # Allow remote debugging from any IP address on selected port
        os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
        import pydevd
        pydevd.settrace("192.168.178.52", port=5678)

    def _close_test_cleanly(spot_con):
        '''
        for module testing each test should leave with a clean environment
        '''
        spot_con._shutdown_server()
        spot_con._close_dbus_session()
        oradio_utils.run_shell_script("systemctl --user restart mpv")

    def _send_a_librespot_event(event):
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
            case event if event == LIBRESPOT_EVENT_CONNECTED:
                environment['PLAYER_EVENT'] = LIBRESPOT_EVENT_CONNECTED
                environment['USER_NAME']    = "ORADIO"
                environment['CONNECTION_ID'] = "123456"
            case event if event == LIBRESPOT_EVENT_DISCONNECTED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_DISCONNECTED
            case event if event == LIBRESPOT_EVENT_CHANGED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_CHANGED
                environment['TRACK_ID']='TRACK#1'
                environment['OLD_TRACK_ID']='TRACK#0'
            case event if event == LIBRESPOT_EVENT_STARTED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_STARTED
                environment['TRACK_ID']='TRACK#1'
            case event if event == LIBRESPOT_EVENT_STOPPED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_STOPPED
                environment['TRACK_ID']='TRACK#1'
            case event if event == LIBRESPOT_EVENT_PLAYING:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_PLAYING
                environment['TRACK_ID']='TRACK#1'
                environment['POSITION_MS']='1234'
            case event if event == LIBRESPOT_EVENT_PAUSED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_PAUSED
                environment['TRACK_ID']='TRACK#1'
                environment['POSITION_MS']='1234'
            case event if event == LIBRESPOT_EVENT_PRELOADING:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_PRELOADING
                environment['TRACK_ID']='TRACK#1'
            case event if event == LIBRESPOT_EVENT_VOLUME:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_VOLUME
                environment['VOLUME']='10'
            case event if event == LIBRESPOT_EVENT_VOLUME_CHANGED:
                environment['PLAYER_EVENT']= LIBRESPOT_EVENT_VOLUME_CHANGED
                environment['VOLUME']='10'
            case _:
                environment['PLAYER_EVENT']='EVENT_UNKNOWN'
        os.environ['PLAYER_EVENT']  = environment['PLAYER_EVENT']
        os.environ['TRACK_ID']      = environment['TRACK_ID']
        os.environ['OLD_TRACK_ID']  = environment['OLD_TRACK_ID']
        os.environ['POSITION_MS']   = environment['POSITION_MS']
        os.environ['VOLUME']        = environment['VOLUME']
        import librespot_event_handler
        importlib.reload(librespot_event_handler) # will run the event handler

    def _discover_oradio_sound_device():
        '''
        discovery of announced spotify-connect services with help of avahi-browse
        '''
        print(YELLOW_TXT+"================================================================"+END_TXT)
        print(YELLOW_TXT+"Check if Oradio is discovered as sound device. Stop with CTRL+C"+END_TXT)
        print(YELLOW_TXT+"================================================================"+END_TXT)

        script = ["avahi-browse","-d","local","_spotify-connect._tcp"]
        try:
            with subprocess.Popen(script,
                                  stdout=PIPE,
                                  bufsize=1,
                                  universal_newlines=True) as process:
                for line in process.stdout:
                    print(line, end='')  # Outputs the line immediately
                    if "mijnOradio" in line:
                        oradio_log.info("Oradio device discovered")
                        print(GREEN_TXT+"Oradio device discovered"+END_TXT)
                if process.returncode != 0:
                    raise CalledProcessError(process.returncode, script)
        except KeyboardInterrupt:
            process.terminate()

    def _monitor_librespot_events():
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
        event_loop = "run"
        print("Press ANY KEY to stop monitoring")
        while event_loop == "run": 
            model_status, msg_model = oradio_utils.create_json_model(MESSAGE_MODEL)
            queue_status, message = _wait_for_queue_messages(msg_queue, msg_model)
            if queue_status == MESSAGE_RECEIVED:
                if message["state"] == SPOTIFY_CONNECT_PLAYING_EVENT:
                    spot_con.play()
                elif message["state"] == SPOTIFY_CONNECT_PAUSED_EVENT:
                    spot_con.pause()
            ## Wait for input for 1 seconds
            ready, _, _ = select.select([sys.stdin], [], [], 1)
            if ready:
                user_input = sys.stdin.readline().strip()
                event_loop = "stop"
        _close_test_cleanly(spot_con)
        time.sleep(1)

    def _playback_control_without_spotify_app():
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
        event_loop = "run"
        while event_loop == "run":
            try:
                event_nr = int(input(event_selection))
            except TypeError:
                event_nr = -1
            match event_nr:
                case 0:
                    event_loop = 'stop'
                case 1:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)
                case 2:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)
                case _:
                    print("invalid selection, try again")
        _close_test_cleanly(spot_con)
        time.sleep(1)

    def _playback_control_with_mpv():
        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)
        _, connect_status = spot_con.get_state()
        if connect_status != SPOTIFY_CONNECT_CONNECTED:
            oradio_log.error("Spotify Connect Servers not running")
            return()
        # send a librespot event to the socket
        event_selection = (YELLOW_TXT+"select an event:\n"
                           "0-stop test\n"
                           "1-mpv play command \n"
                           "2-mpv pause command \n"
                           "select an event:"+END_TXT
                           )
        cmd_loop = "run"
        while cmd_loop == "run":
            try:
                key_pressed = input(event_selection)
                if key_pressed == '':
                    event_nr = 0
                else:
                    event_nr = int(key_pressed)
            except TypeError:
                event_nr = -1
            match event_nr:
                case 0:
                    spot_con._shutdown_server()
                    cmd_loop = 'stop'
                case 1:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PLAY)
                case 2:
                    spot_con.playerctl_command(MPV_PLAYERCTL_PAUSE)
                case _:
                    print("invalid selection, try again")
        print ("return to main test menu")
        _close_test_cleanly(spot_con)

    def _wait_for_queue_messages(queue, msg_model):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        :param msg_model = the json model used for get_msg
        """
        oradio_log.info("Listening for messages in queue")

        while True:
            # Wait for message
            try:
                get_msg = queue.get(block=True, timeout=1)
                # port message into json schema
                msg = msg_model(**get_msg)
                message = msg.model_dump()
                queue_status = MESSAGE_RECEIVED
            except Empty:
                queue_status = MESSAGE_TIMEOUT
                message = "None"
            oradio_log.info(f"Message received in queue: '{message}'")
            break
        return(queue_status, message)

    def _test_event_socket_and_queue():
        '''
        Test the event socket, its observer and the message queue for oradio_controls
        '''
        msg_queue = Queue()
        spot_con = SpotifyConnect( msg_queue)
        time.sleep(1)
        model_status, msg_model = oradio_utils.create_json_model(MESSAGE_MODEL)
        if model_status is MODEL_NAME_NOT_FOUND:
            print(f"JSON model for {MESSAGE_MODEL} not found ")
            test_status = TEST_ERROR
        else:
            test_status = TEST_SUCCESS
            print(YELLOW_TXT+"================================================================================"+END_TXT)
            print(YELLOW_TXT+"All the librespot events will be tested and checked if received at message queue"+END_TXT)
            print(YELLOW_TXT+"================================================================================="+END_TXT)
            librespot_connect_event_sequence = [ LIBRESPOT_EVENT_CONNECTED,
                                                LIBRESPOT_EVENT_PAUSED,
                                                LIBRESPOT_EVENT_PLAYING,
                                                LIBRESPOT_EVENT_CLIENT_CHANGED,
                                                LIBRESPOT_EVENT_STOPPED,
                                                -1 ]

            librespot_connect_queue_sequence = [ SPOTIFY_CONNECT_CONNECTED_EVENT,
                                                SPOTIFY_CONNECT_PAUSED_EVENT,
                                                SPOTIFY_CONNECT_PLAYING_EVENT,
                                                SPOTIFY_CONNECT_NO_EVENT,
                                                SPOTIFY_CONNECT_NO_EVENT,
                                                -1 ]

            librespot_disconnect_event_sequence = [ LIBRESPOT_EVENT_CONNECTED,
                                                   LIBRESPOT_EVENT_DISCONNECTED,
                                                   LIBRESPOT_EVENT_PLAYING,
                                                   LIBRESPOT_EVENT_PAUSED,
                                                   LIBRESPOT_EVENT_CONNECTED,
                                                   -1 ]
            librespot_disconnect_queue_sequence = [ SPOTIFY_CONNECT_CONNECTED_EVENT,
                                                   SPOTIFY_CONNECT_DISCONNECTED_EVENT,
                                                   SPOTIFY_CONNECT_NO_EVENT,
                                                   SPOTIFY_CONNECT_NO_EVENT,
                                                   SPOTIFY_CONNECT_CONNECTED_EVENT,
                                                   -1 ]
            error_counter = 0
            test_counter = 0
            ########### Check the connected related events  ###################
            print(YELLOW_TXT+"================================================"+END_TXT)
            print(YELLOW_TXT+"Testing librespot event for connected states"+END_TXT)
            print(YELLOW_TXT+"================================================"+END_TXT)
            event_index = 0
            while librespot_connect_event_sequence[event_index] != -1:
                # Clear all items
                while not msg_queue.empty():
                    msg_queue.get()

                event = librespot_connect_event_sequence[event_index]
                print( YELLOW_TXT+f"testing the {event} librespot event"+END_TXT)
                test_counter +=1
                _send_a_librespot_event(event)
                time.sleep(1)
                # wait for message in queue
                queue_event = librespot_connect_queue_sequence[event_index]
                queue_status, message = _wait_for_queue_messages(msg_queue, msg_model)
                print(queue_status, message)
                if queue_status == "MESSAGE_TIMEOUT":
                    if queue_event == SPOTIFY_CONNECT_NO_EVENT:
                        # timeout of 2 seconds, as this librespot event should not be passed into queue
                        # so if timeout, it is correct.
                        print(GREEN_TXT+f"Correct message event <{queue_status}> received in queue"+END_TXT)
                    else:
                        error_counter +=1
                        print(RED_TXT+f"Incorrect message event <{queue_status}> received in queue"+END_TXT)
                else:
                    if queue_event in message['state']:
                        print(GREEN_TXT+f"Correct message event\
                                             <{message['state']}> received in queue"+END_TXT)
                        time.sleep(1)
                    else:
                        error_counter +=1
                        print(RED_TXT+f"Incorrect message event <{message['state']}>\
                                                     received in queue"+END_TXT)
                        break
                event_index += 1

            print(YELLOW_TXT+"================================================="+END_TXT)
            print(YELLOW_TXT+"Testing librespot event for disconnected states"+END_TXT)
            print(YELLOW_TXT+"================================================"+END_TXT)

            event_index = 0
            while librespot_disconnect_event_sequence[event_index] != -1:
                # Clear all items
                while not msg_queue.empty():
                    msg_queue.get()

                event = librespot_disconnect_event_sequence[event_index]
                test_counter +=1
                print( YELLOW_TXT+f"testing the {event} librespot event"+END_TXT)
                _send_a_librespot_event(event)
                time.sleep(1)
                # wait for message in queue
                queue_event = librespot_disconnect_queue_sequence[event_index]

                queue_status, message = _wait_for_queue_messages(msg_queue, msg_model)
                print(queue_status, message)
                if queue_status == "MESSAGE_TIMEOUT":
                    if queue_event == SPOTIFY_CONNECT_NO_EVENT:
                        # timeout of 2 seconds, as this librespot event should not be passed into queue
                        # so if timeout, it is correct.
                        print(GREEN_TXT+f"Correct message event <{queue_status}> received in queue"+END_TXT)
                    else:
                        error_counter +=1
                        print(RED_TXT+f"Incorrect message event <{queue_status}> received in queue"+END_TXT)
                else:
                    if queue_event in message['state']:
                        print(GREEN_TXT+f"Correct message event <{message['state']}> received in queue"+END_TXT)
                        time.sleep(1)
                    else:
                        error_counter +=1
                        print(RED_TXT+f"Incorrect message event <{message['state']}> received in queue"+END_TXT)
                        break
                event_index += 1
            _close_test_cleanly(spot_con)
            if system_testing == "yes":
                if error_counter > 0:
                    test_status = AUTO_TEST_ERROR
                else:
                    test_status = AUTO_TEST_SUCCESS
                sys.exit(test_status)
            else:
                print ("return to main test menu")
                test_status = TEST_SUCCESS
        return test_status

    def _mpris_player_control_test():
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
        _, connect_status = spot_con.get_state()
        print ( "connect_status= ", connect_status)
        if connect_status == SPOTIFY_CONNECT_CONNECTED:
        # check player ctl: Play
            spot_con.player_iface.Play()
            print("Play command sent.")
            playback_status = spot_con._get_playback_status()
            if "Playing" in playback_status:
                print(GREEN_TXT+f"Correct Playback Status ={playback_status}"+END_TXT)
            else:
                print(RED_TXT+f"Wrong Playback Status ={playback_status}"+END_TXT)
            # check player ctl: Pause
            spot_con.player_iface.Pause()
            print("Pause command sent.")
            playback_status = spot_con._get_playback_status()
            if "Paused" in playback_status:
                print(GREEN_TXT+f"Correct Playback Status ={playback_status}"+END_TXT)
            else:
                print(RED_TXT+f"Wrong Playback Status = {playback_status}"+END_TXT)
        else:
            print(YELLOW_TXT+f"Error status = {connect_status}"+END_TXT)
        _close_test_cleanly(spot_con)

    def _spotify_get_status():

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
        _ = input("Press any key to stop monitoring")
        event.clear()
        get_status_thread.join()
        _close_test_cleanly(spot_con)

# Run the module test function
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Check if the Oradio sound device can be discovered on local mDNS \n"
                       " 2-Monitor librespot events \n"
                       " 3-Test event socket and queue \n"
                       " 4-MPRIS player control test, precondition: connect spotify to oradio\n"
                       " 5-Playback control without Spotify App \n"
                       " 6-Playback control with mpv control \n"
                       " 7-Get the status of Spotify_connect \n"
                       "select: "
                       )
    if system_testing == "yes":
        status = _test_event_socket_and_queue()
    else:
        loop = "run"
        while loop == "run":
            # Get user input
            try:
                function_nr = int(input(INPUT_SELECTION))
            except TypeError:
                function_nr = -1
            # Execute selected function
            match function_nr:
                case 0:
                    loop = "stop"
                    break
                case 1:
                    _discover_oradio_sound_device()
                case 2:
                    _monitor_librespot_events()
                case 3:
                    _test_event_socket_and_queue()
                case 4:
                    _mpris_player_control_test()
                case 5:
                    _playback_control_without_spotify_app()
                case 6:
                    _playback_control_with_mpv()
                case 7:
                    _spotify_get_status()
                case _:
                    print("\nPlease input a valid number\n")
        sys.exit("Done")
