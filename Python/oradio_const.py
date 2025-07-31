#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on December 23, 2024
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Defines for oradio scripts

25 jul update volume settings to prepare for speaker in production
"""

################## SYSTEM #############################
import os
import sys

# Colors
RED    ='\033[0m\033[1;31m'
YELLOW ='\033[0m\033[1;93m'
GREEN  ='\033[0m\033[1;32m'
NC     ='\033[0m'

# Make Oradio file locations relative
ORADIO_DIR      = sys.path[0]
ORADIO_LOG_DIR  = os.path.abspath(ORADIO_DIR + '/../logging')
SOUND_FILES_DIR = os.path.realpath(ORADIO_DIR + "/../system_sounds")

JSON_SCHEMAS_FILE = os.path.realpath(ORADIO_DIR + "/schemas.json")

# Messages consist of 3 elements: type, state and error
MESSAGE_NO_ERROR = "None"

################## WIFI UTILS #############################
# Access point
ACCESS_POINT_HOST = "108.156.60.1"  # wsj.com
ACCESS_POINT_SSID = "OradioAP"
# wifi states
STATE_WIFI_IDLE         = "Wifi is not connected"
STATE_WIFI_INTERNET     = "Wifi connected to internet"
STATE_WIFI_CONNECTED    = "Wifi connected without internet"
STATE_WIFI_ACCESS_POINT = "Wifi configured as access point"
# wifi messages
#OMJ: 'Type' is eigenlijk 'source'
MESSAGE_WIFI_TYPE            = "Wifi message"
MESSAGE_WIFI_FAIL_CONFIG     = "Failed to save credentials in NetworkManager"
MESSAGE_WIFI_FAIL_START_AP   = "Failed to start access point"
MESSAGE_WIFI_FAIL_CONNECT    = "Wifi failed to connect"
MESSAGE_WIFI_FAIL_STOP_AP    = "Failed to stop access point"
MESSAGE_WIFI_FAIL_DISCONNECT = "Wifi failed to disconnect"

################## WEB SERVICE #############################
# Web server address
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
# Web service states
STATE_WEB_SERVICE_IDLE   = "web service is idle"
STATE_WEB_SERVICE_ACTIVE = "web service is running"
# Web service messages from service to parent
#OMJ: 'Type' is eigenlijk 'source'
MESSAGE_WEB_SERVICE_TYPE         = "web service message"
MESSAGE_WEB_SERVICE_PL1_CHANGED  = "PL1 playlist changed"
MESSAGE_WEB_SERVICE_PL2_CHANGED  = "PL2 playlist changed"
MESSAGE_WEB_SERVICE_PL3_CHANGED  = "PL3 playlist changed"
MESSAGE_WEB_SERVICE_PL_WEBRADIO  = "playlist is web radio"
MESSAGE_WEB_SERVICE_PLAYING_SONG = "web service plays a song"
MESSAGE_WEB_SERVICE_FAIL_START   = "web service failed to start"
MESSAGE_WEB_SERVICE_FAIL_STOP    = "web service failed to stop"

################## USB #############################
# Paths
USB_MOUNT_PATH  = "/media"
USB_MOUNT_POINT = USB_MOUNT_PATH + "/oradio"
USB_MUSIC       = USB_MOUNT_POINT + "/Muziek"
USB_SYSTEM      = USB_MOUNT_POINT + "/Systeem"
# USB states
STATE_USB_PRESENT = "USB drive present"
STATE_USB_ABSENT  = "USB drive absent"
# USB messages
#OMJ: 'Type' is eigenlijk 'source'
MESSAGE_USB_TYPE       = "USB message"
MESSAGE_USB_ERROR_FILE = "USB file format error"

################## AUDIO #############################
#OMJ: the constant is named path, but points to a file?
PRESET_FILE_PATH = USB_SYSTEM + "/presets.json"

################## VOLUME #############################
# Raw volume units
VOLUME_MINIMUM = 105
VOLUME_MAXIMUM = 215
# Volume messages
#OMJ: 'Type' is eigenlijk 'source'
MESSAGE_TYPE_VOLUME   = "Vol Control message"
#OMJ: is MESSAGE_VOLUME_CHANGED niet een betere constante?
MESSAGE_STATE_CHANGED = "Volume changed"

################## SYSTEM SOUNDS #############################
SOUND_FILES_DIR = "/home/pi/Oradio3/system_sounds"

############# SPOTIFY CONFIG #####################################
MESSAGE_SPOTIFY_TYPE        = "Spotify message"
SPOTIFY_EVENT_SOCKET_PORT   = 8010
MPV_SOCKET = "/home/pi/spotify/mpv-socket"

# MPV_PLAYER COMMANDS ####
MPV_PLAYERCTL_PLAY  = "play"
MPV_PLAYERCTL_PAUSE = "pause"
MPV_PLAYERCTL_STOP  = "stop"
#MPV_PLAYER STATES ####
MPV_PLAYERCTL_PLAYING_STATE = "Playing"
MPV_PLAYERCTL_STOPPED_STATE = "Stopped"
MPV_PLAYERCTL_PAUSED_STATE  = "Paused"

MPV_PLAYERCTL_COMMAND_NOT_FOUND = "playerctl command not found"
MPV_PLAYERCTL_COMMAND_ERROR     = "playerctl command failed"

# MPRIS Medaio Player identifier (D-Bus service names, according the  naming convention for the MPRIS2 specification.
# It is not a physical file or program, but a logical D-Bus service name
# that media players register under when they support MPRIS.
MPRIS_MPV_PLAYER            = "org.mpris.MediaPlayer2.mpv"
MPRIS_MEDIA_PLAYER          = "/org/mpris/MediaPlayer2"
MPRIS_MP2_PLAYER            = "org.mpris.MediaPlayer2.Player"
MPRIS_DBUS_PROPERTIES       = "org.freedesktop.DBus.Properties"
MPRIS_MEDIA_PLAYER_SEARCH   = "org/mpris/MediaPlayer2."

# SPOTIFY events and states
SPOTIFY_APP_STATUS_PLAYING      = "Playing"
SPOTIFY_APP_STATUS_STOPPED      = "Stopped"
SPOTIFY_APP_STATUS_PAUSED       = "Paused"
SPOTIFY_APP_STATUS_DISCONNECTED = "Disconnected"
SPOTIFY_APP_STATUS_CONNECTED    = "Connected"
SPOTIFY_APP_STATUS_CLIENT_CHANGED = "Client changed"

SPOTIFY_CONNECT_PLAYING_EVENT           = "Spotify Connect playing event"
SPOTIFY_CONNECT_PAUSED_EVENT            = "Spotify Connect paused event"
SPOTIFY_CONNECT_STOPPED_EVENT           = "Spotify Connect stopped event"
SPOTIFY_CONNECT_CONNECTED_EVENT         = "Spotify Connect connected event"
SPOTIFY_CONNECT_DISCONNECTED_EVENT      = "Spotify Connect disconnected event"
SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT    = "Spotify Connect client changed event"
SPOTIFY_CONNECT_SERVERS_RUNNING         = "Spotify Connect local servers running"
SPOTIFY_CONNECT_SERVERS_NOT_RUNNING     = "Spotify Connect local servers NOT running"
SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE  = "Spotify Connect MPV service not active"
SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE   = "Spotify Connect MPV service is active"
SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND = "Spotify Connect MPV MPRIS player not found"
SPOTIFY_CONNECT_MPV_STATE_OK               = "Spotify Connect MPV State OK"

SPOTIFY_CONNECT_CONNECTED = "Spotify Connect is connected"
SPOTIFY_CONNECT_NOT_CONNECTED = "Spotify Connect is NOT connected"

##### JSON SCHEMA ########
MODEL_NAME_NOT_FOUND = "Unknown model name, not found in schemas.json"
MODEL_NAME_FOUND     = "model name found"
