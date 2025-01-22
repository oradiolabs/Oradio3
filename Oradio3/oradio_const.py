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
ACCESS_POINT_NAME = "OradioAP"
# Wifi states
STATE_WIFI_IDLE           = "Wifi is not connected"
STATE_WIFI_INFRASTRUCTURE = "Connected to infrastructure"
STATE_WIFI_ACCESS_POINT   = "Configured as access point"

################## WEB SERVICE #############################
# CPU allocation as subset from number of cores = {0, 1, 2, 3}
CPU_MASK_WEB_SERVICE = {1, 2}     # CPU's allowed for the web service process
# Web server address
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8000
# Web service states
STATE_WEB_SERVICE_IDLE   = "web service is idle"
STATE_WEB_SERVICE_ACTIVE = "web service is running"
# Web server messages from server to service
MESSAGE_WEB_SERVER_TYPE          = "web service message"
MESSAGE_WEB_SERVER_CONNECT_WIFI  = "connect to wifi"
MESSAGE_WEB_SERVER_RESET_TIMEOUT = "reset web service timeout"
# Web service messages from service to parent
MESSAGE_WEB_SERVICE_TYPE         = "web service message"
MESSAGE_WEB_SERVICE_CONNECT_OK   = "wifi is connected"
MESSAGE_WEB_SERVICE_CONNECT_FAIL = "wifi not connected"
MESSAGE_WEB_SERVICE_ACCESS_POINT = "wifi is access point"

################## USB #############################
# Path where the USB drive is mounted
USB_MOUNT  = "/media/sda1"
# File name in USB root with wifi credentials
USB_WIFI_FILE = USB_MOUNT +"/Wifi_invoer.json"
# USB drive label for 'Oradio' operation
LABEL_USB_ORADIO = "ORADIO"
# USB states
STATE_USB_PRESENT = "USB drive present"
STATE_USB_ABSENT  = "USB drive absent"
STATE_USB_ERROR   = "USB drive error"
# USB messages
MESSAGE_USB_TYPE          = "USB message"
MESSAGE_USB_ERROR_LABEL   = "USB label is invalid"
MESSAGE_USB_ERROR_TIMEOUT = "USB did not mount in the expected time"
