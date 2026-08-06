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
@summary:       Defines for Oradio scripts
"""
from pathlib import Path

##### SHARED WITH INSTALLER ###############################
# Values below are read from constants.env, which install_oradio3.sh
# also sources. Edit that file, not this one.
def _load_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")
    return values

# constants.py lives in Main/, so the project root is two levels up.
# This is the same directory install_oradio3.sh calls SCRIPT_PATH.
_ROOT = Path(__file__).resolve().parent.parent
_ENV = _load_env(_ROOT / "constants.env")

##### SYSTEM ##############################################

HOSTNAME = _ENV["ORADIO_HOSTNAME"]

# Paths, derived the same way the installer derives them
SPOTIFY_PATH = str(_ROOT / "Spotify")

# Colors
BLUE    = '\x1b[38;5;039m'
GREY    = '\x1b[38;5;248m'
WHITE   = '\x1b[38;5;255m'
YELLOW  = '\x1b[38;5;226m'
GREEN   = '\x1b[38;5;048m'
RED     = '\x1b[38;5;196m'
MAGENTA = '\x1b[38;5;201m'
NC      = '\x1b[0m'

# Messages consist of 3 elements: source, state and error
MESSAGE_NO_ERROR = "None"

##### LED definitions see UML «led_name» ##################
LED_PLAY    = "LedPlay"
LED_STOP    = "LedStop"
LED_PRESET1 = "LedPreset1"
LED_PRESET2 = "LedPreset2"
LED_PRESET3 = "LedPreset3"
LED_NAMES   = [LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3]

##### #BUTTON definitions see UML «button_name» ###########
BUTTON_PLAY        = "ButtonPlay"
BUTTON_STOP        = "ButtonStop"
BUTTON_PRESET1     = "ButtonPreset1"
BUTTON_PRESET2     = "ButtonPreset2"
BUTTON_PRESET3     = "ButtonPreset3"
BUTTON_NAMES       = [BUTTON_PLAY, BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3]
BUTTON_PRESSED     = "button pressed"
BUTTON_RELEASED    = "button released"
BUTTON_SHORT_PRESS = "Short press:"
BUTTON_LONG_PRESS  = "Long press:"

##### SYSTEM SOUND NAMES ##################################
SOUND_START        = "Start"
SOUND_STOP         = "Stop"
SOUND_PLAY         = "PLAY"
SOUND_CLICK        = "Click"
SOUND_NEXT         = "Next"
SOUND_PRESET1      = "Preset1"
SOUND_PRESET2      = "Preset2"
SOUND_PRESET3      = "Preset3"
SOUND_SPOTIFY      = "Spotify"
SOUND_USB          = "USBPresent"
SOUND_NO_USB       = "NoUSB"
SOUND_AP_START     = "OradioAPstarted"
SOUND_AP_STOP      = "OradioAPstopped"
SOUND_WIFI         = "WifiConnected"
SOUND_NO_WIFI      = "WifiNotConnected"
SOUND_NO_INTERNET  = "NoInternet"
SOUND_NEW_PRESET   = "NewPlaylistPreset"
SOUND_NEW_WEBRADIO = "NewPlaylistWebradio"

##### REMOTE SERVER #######################################
RMS_SERVER_URL = _ENV["RMS_SERVER_URL"]
RMS_SERVER_KEY = _ENV["RMS_SERVER_KEY"]

##### WIFI UTILS ##########################################
# Access point
ACCESS_POINT_HOST = "108.156.60.1"  # wsj.com
ACCESS_POINT_SSID = "OradioAP"

##### WEB SERVICE #########################################
# Web server address
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
# Requests from fastapi to web service
REQUEST_CONNECT = "connect to wifi network"
REQUEST_STOP    = "stop web service"

##### USB #################################################
USB_MOUNT_POINT = _ENV["USB_MOUNT_POINT"]
USB_MUSIC       = USB_MOUNT_POINT + "/Muziek"
USB_SYSTEM      = USB_MOUNT_POINT + "/Systeem"

##### AUDIO ###############################################
PRESETS_FILE = USB_SYSTEM + "/presets.json"

##### SPOTIFY CONFIG ######################################
SPOTIFY_MPV_PORT     = 8010
SPOTIFY_MPV_SOCKET   = SPOTIFY_PATH + "/mpv-socket"
SPOTIFY_ACTIVE_FLAG  = SPOTIFY_PATH + "/" + _ENV["SPOTIFY_ACTIVE_FLAG_NAME"]
SPOTIFY_PLAYING_FLAG = SPOTIFY_PATH + "/" + _ENV["SPOTIFY_PLAYING_FLAG_NAME"]

##### MPV_PLAYER COMMANDS #################################
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
MPRIS_MPV_PLAYER          = "org.mpris.MediaPlayer2.mpv"
MPRIS_MEDIA_PLAYER        = "/org/mpris/MediaPlayer2"
MPRIS_MP2_PLAYER          = "org.mpris.MediaPlayer2.Player"
MPRIS_DBUS_PROPERTIES     = "org.freedesktop.DBus.Properties"
MPRIS_MEDIA_PLAYER_SEARCH = "org/mpris/MediaPlayer2."

##### SPOTIFY events and states ###########################
SPOTIFY_APP_STATUS_PLAYING        = "Playing"
SPOTIFY_APP_STATUS_STOPPED        = "Stopped"
SPOTIFY_APP_STATUS_PAUSED         = "Paused"
SPOTIFY_APP_STATUS_DISCONNECTED   = "Disconnected"
SPOTIFY_APP_STATUS_CONNECTED      = "Connected"
SPOTIFY_APP_STATUS_CLIENT_CHANGED = "Client changed"

# Moved to messaging:
#SPOTIFY_CONNECT_CONNECTED_EVENT            = "Spotify Connect connected event"
#SPOTIFY_CONNECT_DISCONNECTED_EVENT         = "Spotify Connect disconnected event"
#SPOTIFY_CONNECT_PLAYING_EVENT              = "Spotify Connect playing event"
#SPOTIFY_CONNECT_PAUSED_EVENT               = "Spotify Connect paused event"

SPOTIFY_CONNECT_STOPPED_EVENT              = "Spotify Connect stopped event"
SPOTIFY_CONNECT_CLIENT_CHANGED_EVENT       = "Spotify Connect client changed event"
SPOTIFY_CONNECT_SERVERS_RUNNING            = "Spotify Connect local servers running"
SPOTIFY_CONNECT_SERVERS_NOT_RUNNING        = "Spotify Connect local servers NOT running"
SPOTIFY_CONNECT_MPV_SERVICE_NOT_ACTIVE     = "Spotify Connect MPV service not active"
SPOTIFY_CONNECT_MPV_SERVICE_IS_ACTIVE      = "Spotify Connect MPV service is active"
SPOTIFY_CONNECT_MPV_MPRIS_PLAYER_NOT_FOUND = "Spotify Connect MPV MPRIS player not found"
SPOTIFY_CONNECT_MPV_STATE_OK               = "Spotify Connect MPV State OK"

SPOTIFY_CONNECT_CONNECTED = "Spotify Connect is connected"
SPOTIFY_CONNECT_NOT_CONNECTED = "Spotify Connect is NOT connected"

##### Remote Debugger defines #############################
DEBUGGER_CONNECTED      = "Debugger connected"
DEBUGGER_NOT_CONNECTED  = "Debugger not connected"
DEBUGGER_DISABLED       = "debugger disabled"
DEBUGGER_ENABLED        = "debugger enabled"
