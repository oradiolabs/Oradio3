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
#### Oradio modules  #####
import oradio_utils
from oradio_const import *

class SpotifyConnect():
    def __init__(self):
        # setup an observer listening to socket for incoming messages


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


if __name__ == "__main__":
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"    
    
    ## stop a running Oradio_controls as it may interfere with this test ##
    print("kill Oradio_controls, to prevent interferences with this test module ")
    script = "sudo pkill -9 -f oradio_control.py"
#    oradio_utils.run_shell_script(script)
    
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
                    if "Oradio-luidspreker" in line:
                        oradio_utils.logging("success","Oradio-luidspreker discovered")
                if process.returncode != 0:
                    raise CalledProcessError(process.returncode, script)
        except KeyboardInterrupt:
            process.terminate()
        return()
    
    def play_spotify_on_speaker(): 
        '''
        Play a playlist via the spotify connect app
        '''   
        print("Open a Spotify app and connect to a sound device called Oradio-luidspreker ")
        print("Check if spotify events are there")
        print("Increase volume on Spotify App")
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
        server_socket.listen(1)        
        print(YELLOW_TXT+"Socket open and listening. Stop test with CTRL+C"+END_TXT)
        try:
            while(True):
                client_socket, address = server_socket.accept()
                data = client_socket.recv(1024)
                print(data)
        except KeyboardInterrupt:
            client_socket.close()
            server_socket.close()
    
    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Check if Oradio Speaker can be discovered on local mDns \n"
                       " 2-Play spotify on the Oradio Speaker\n"
                       " 3-xxxxx\n"
                       " 4-xxxxx\n"
                       " 5-xxxxx\n"
                       "select: "
                       )
 
    # User command loop
    
    $ mpv --no-video --demuxer=rawaudio --demuxer-rawaudio-format=s16le --demuxer-rawaudio-rate=44100 --demuxer-rawaudio-channels=2 /spotify/librespot-pipe

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
                play_spotify_on_speaker()
            case 3:
                pass
            case 4:
                pass
            case 5:
                pass
            case _:
                print("\nPlease input a valid number\n")
