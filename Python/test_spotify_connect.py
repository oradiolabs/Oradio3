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
#### Oradio modules  #####
import oradio_utils

if __name__ == "__main__":
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"    
    
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
                    if "OradioLuidspreker" in line:
                        oradio_utils.logging("success","OradioLuidspreker discovered")
                if process.returncode != 0:
                    raise CalledProcessError(process.returncode, script)
        except KeyboardInterrupt:
            process.terminate()
        return()
    
    def play_spotify_on_speaker():    
    
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
