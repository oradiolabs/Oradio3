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
"""
################## WIFI UTILS #############################
# Access point
ACCESS_POINT_SSID = "OradioAP"
# wifi states
STATE_WIFI_IDLE           = "Wifi is not connected"
STATE_WIFI_INFRASTRUCTURE = "Connected to infrastructure"
STATE_WIFI_ACCESS_POINT   = "Configured as access point"
# wifi messages
MESSAGE_WIFI_TYPE            = "Wifi message"
MESSAGE_WIFI_FAIL_CONNECT    = "Wifi failed to connect"
MESSAGE_WIFI_FAIL_DISCONNECT = "Wifi failed to disconnect"
MESSAGE_WIFI_FAIL_AP_START   = "Failed to start access point"
MESSAGE_WIFI_FAIL_AP_STOP    = "Failed to stop access point"

################## WEB SERVICE #############################
# Web server address
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
# Web service states
STATE_WEB_SERVICE_IDLE   = "web service is idle"
STATE_WEB_SERVICE_ACTIVE = "web service is running"
# Web service messages from service to parent
MESSAGE_WEB_SERVICE_TYPE = "web service message"

################## USB #############################
# Paths
USB_MOUNT_PATH  = "/media"
USB_MOUNT_POINT = USB_MOUNT_PATH + "/oradio"
USB_MUSIC       = USB_MOUNT_POINT + "/Muziek"
USB_SYSTEM      = USB_MOUNT_POINT + "/Systeem"
# Name of file used to monitor if USB is mounted or not
USB_MONITOR = "usb_ready"
# File name in USB root with wifi credentials
USB_WIFI_FILE = USB_MOUNT_POINT + "/wifi_invoer.json"
# USB states
STATE_USB_PRESENT = "USB drive present"
STATE_USB_ABSENT  = "USB drive absent"
# USB messages
MESSAGE_USB_TYPE       = "USB message"
MESSAGE_USB_ERROR_FILE = "USB file format error"

################## AUDIO #############################
PRESET_FILE_PATH = USB_SYSTEM + "/presets.json"

################## VOLUME #############################
# Raw volume units
VOLUME_MINIMUM = 70
VOLUME_MAXIMUM = 180
# Volume messages
MESSAGE_TYPE_VOLUME   = "Vol Control message"
MESSAGE_STATE_CHANGED = "Volume changed"

################## SYSTEM SOUNDS #############################
SOUND_FILES_DIR = "/home/pi/Oradio3/system_sounds"
