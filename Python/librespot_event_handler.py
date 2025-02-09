#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Februari 3, 2025
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
        - pip3 install pydantic
    :Documentation
        - https://github.com/librespot-org/librespot/wiki/Events
"""

import os
import json
import socket

#### Oradio modules ####
from oradio_utils import json_schema_to_pydantic
from oradio_const import *

librespot_event_data = [{'player_event': 'None'},
                        {'track_id':'None'},
                        {'old_track_id':'None'},
                        {'track_duration':'None'},
                        {'position_ms':'None'}]
environment= os.environ
event_data = {}
if "PLAYER_EVENT" in environment:
    if environment['PLAYER_EVENT'] == 'changed':
        event_data['player_event']   = environment['PLAYER_EVENT']
        event_data['track_id']       = environment['TRACK_ID']
        event_data['old_track_id']   = environment['OLD_TRACK_ID']
    elif environment['PLAYER_EVENT'] == 'started':
        event_data['player_event']   = environment['PLAYER_EVENT']
        event_data['track_id']       = environment['TRACK_ID']
    elif environment['PLAYER_EVENT'] == 'stopped':
        event_data['player_event']   = environment['PLAYER_EVENT']
        event_data['track_id']       = environment['TRACK_ID']
    elif environment['PLAYER_EVENT'] == 'playing':
        event_data['player_event']   = environment['PLAYER_EVENT']
        event_data['track_id']       = environment['TRACK_ID']
        event_data['position_ms'] = environment['POSITION_MS']
    elif environment['PLAYER_EVENT'] == 'paused':
        event_data['player_event']   = environment['PLAYER_EVENT']
        event_data['track_id']       = environment['TRACK_ID']
        event_data['position_ms']    = environment['POSITION_MS']
    elif environment['PLAYER_EVENT'] == 'preloading':
        event_data['track_id']       = environment['TRACK_ID']
    elif environment['PLAYER_EVENT'] == 'volume_set':
        event_data['volume']         = environment['VOLUME']
    elif environment['PLAYER_EVENT'] == 'volume_changed':
        event_data['volume']         = environment['VOLUME']
    librespot_event_data=event_data
    
serialized_dict = json.dumps(librespot_event_data).encode('utf-8')

# Client
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
client_socket.sendall(serialized_dict)
client_socket.close()