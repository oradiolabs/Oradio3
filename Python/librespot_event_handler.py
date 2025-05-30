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
from oradio_const import *

librespot_event_data = [{'player_event': 'None'},   # Name of the Event
                        {'track_id':'None'},        # Spotify track ID of the new Track
                        {'old_track_id':'None'},    # Spotify ID of the previous Track
                        {'track_duration':'None'},  # Duration in ms
                        {'position_ms':'None'},     # Position in ms
                        {'user_name':'None'},       # Session User Name (really an ID not a display name)
                        {'connection_id':'None'},   # Session Connection ID
                        {'client_id':'None'},       # ID of the Client
                        {'client_name':'None'},     # Name of the Client
                        {'client_brand_name':'None'}, # Brand Name of the Client
                        {'client_model_name':'None'}]  # Model Name of the Client
environment= os.environ
event_data = {}
if "PLAYER_EVENT" in environment:
    librespot_event = environment['PLAYER_EVENT']
    event_data['player_event'] = environment['PLAYER_EVENT']
    match (librespot_event):
        case 'changed':
            event_data['track_id']     = environment['TRACK_ID']
            event_data['old_track_id'] = environment['OLD_TRACK_ID']
        case 'started':
            event_data['track_id']     = environment['TRACK_ID']
        case 'stopped':
            event_data['track_id']     = environment['TRACK_ID']
        case 'playing':
            event_data['track_id']     = environment['TRACK_ID']
            event_data['position_ms']  = environment['POSITION_MS']
        case 'paused':
            event_data['track_id']     = environment['TRACK_ID']
            event_data['position_ms']  = environment['POSITION_MS']
        case 'preloading':
            event_data['track_id']     = environment['TRACK_ID']
        case 'session_connected':
            event_data['user_name']     = environment['USER_NAME']
            event_data['connection_id'] = environment['CONNECTION_ID']
        case 'session_disconnected':
            event_data['user_name']     = environment['USER_NAME']
            event_data['connection_id'] = environment['CONNECTION_ID']
        case 'session_client_changed':
            event_data['client_id']         = environment['CLIENT_ID']
            event_data['client_name']       = environment['CLIENT_NAME']
            event_data['client_brand_name'] = environment['CLIENT_BRAND_NAME']
            event_data['client_model_name'] = environment['CLIENT_MODEL_NAME']
        case 'volume_set':
            event_data['volume'] = environment['VOLUME']
        case 'volume_changed':
            event_data['volume'] = environment['VOLUME']
        case _:
            pass
    librespot_event_data=event_data
serialized_dict = json.dumps(librespot_event_data).encode('utf-8')

# Client
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect(("localhost", SPOTIFY_EVENT_SOCKET_PORT))
client_socket.sendall(serialized_dict)
client_socket.close()
