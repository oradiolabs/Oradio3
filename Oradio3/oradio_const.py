'''

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
'''
################## WIFI UTILS #############################
# Access point
ACCESS_POINT_SSID = "OradioAP"
# WiFi states
STATE_WIFI_IDLE           = "WiFi is not connected"
STATE_WIFI_INFRASTRUCTURE = "Connected to infrastructure"
STATE_WIFI_ACCESS_POINT   = "Configured as access point"
# WiFi messages
MESSAGE_WIFI_TYPE            = "WiFi message"
MESSAGE_WIFI_FAIL_CONNECT    = "WiFi failed to connect"
MESSAGE_WIFI_FAIL_DISCONNECT = "WiFi failed to disconnect"
MESSAGE_WIFI_FAIL_START_AP   = "Failed to start access point"
MESSAGE_WIFI_FAIL_STOP_AP    = "Failed to stop access point"

################## WEB SERVICE #############################
# Web server address
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
# Web service states
STATE_WEB_SERVICE_IDLE   = "web service is idle"
STATE_WEB_SERVICE_ACTIVE = "web service is running"
# Web server messages from server to service
MESSAGE_WEB_SERVER_TYPE          = "web service message"
MESSAGE_WEB_SERVER_CONNECT_WIFI  = "connect to WiFi"
MESSAGE_WEB_SERVER_RESET_TIMEOUT = "reset web service timeout"
# Web service messages from service to parent
MESSAGE_WEB_SERVICE_TYPE = "web service message"

################## USB #############################
# Path where the USB drive is mounted
USB_MOUNT_PATH  = "/media"
USB_MOUNT_POINT = USB_MOUNT_PATH + "/oradio"
# File name in USB root with WiFi credentials
USB_WIFI_FILE = USB_MOUNT_POINT + "/wifi_invoer.json"
# USB states
STATE_USB_PRESENT = "USB drive present"
STATE_USB_ABSENT  = "USB drive absent"
# USB messages
MESSAGE_USB_TYPE       = "USB message"
MESSAGE_USB_ERROR_FILE = "USB file format error"
