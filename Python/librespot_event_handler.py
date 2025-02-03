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
    :Documentation
        - https://github.com/librespot-org/librespot/wiki/Events
"""

import os
import json

# Non-blocking Events
#file_ptr = open(ORADIO_LIBRESPOT_CMD_FILE,"w").close() # clear contents of file

librespot_cmd = {}
librespot_cmd['player_event']   = "None"
librespot_cmd['track_id']       = "None"
librespot_cmd['old_track_id']   = "None"
librespot_cmd['track_duration'] = "None"
librespot_cmd['position_ms']    = "None"

environment= os.environ

if environment['PLAYER_EVENT'] == 'changed':
    librespot_cmd['player_event']   = environment['PLAYER_EVENT']
    librespot_cmd['track_id']       = environment['TRACK_ID']
    librespot_cmd['old_track_id']   = environment['OLD_TRACK_ID']
elif environment['PLAYER_EVENT'] == 'started':
    librespot_cmd['player_event']   = environment['PLAYER_EVENT']
    librespot_cmd['track_id']       = environment['TRACK_ID']
elif environment['PLAYER_EVENT'] == 'stopped':
    librespot_cmd['player_event']   = environment['PLAYER_EVENT']
    librespot_cmd['track_id']       = environment['TRACK_ID']
elif environment['PLAYER_EVENT'] == 'playing':
    librespot_cmd['player_event']   = environment['PLAYER_EVENT']
    librespot_cmd['track_id']       = environment['TRACK_ID']
    librespot_cmd['position_ms'] = environment['POSITION_MS']
elif environment['PLAYER_EVENT'] == 'paused':
    librespot_cmd['player_event']   = environment['PLAYER_EVENT']
    librespot_cmd['track_id']       = environment['TRACK_ID']
    librespot_cmd['position_ms']    = environment['POSITION_MS']
elif environment['PLAYER_EVENT'] == 'preloading':
    librespot_cmd['track_id']       = environment['TRACK_ID']
elif environment['PLAYER_EVENT'] == 'volume_set':
    librespot_cmd['volume']         = environment['VOLUME']

print("librespot_event_handler, command = ", librespot_cmd)