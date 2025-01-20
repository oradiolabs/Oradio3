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
################## NETWORKING #############################
# Access point
ACCESS_POINT_NAME = "OradioAP"
ACCESS_POINT_SSID = "OradioAP"
ACCESS_POINT_HOST = "108.156.60.1"  # wsj.com
# Web service
WEB_SERVICE_HOST    = "0.0.0.0"
WEB_SERVICE_PORT    = 8000
WEB_SERVICE_TIMEOUT = 600 # 10 minutes
# CPU allocation as subset from number of cores = {0, 1, 2, 3}
WEB_SERVICE_CPU_MASK = {1, 2}     # CPU's allowed for the web server process
# Messages
COMMAND_WIFI_TYPE          = "wifi"
COMMAND_WIFI_CONNECT       = "connect"
COMMAND_WIFI_TIMEOUT_RESET = "timeout_reset"

################## USB #############################
# USB drive label for 'Oradio' operation
USB_ORADIO = "ORADIO"
# USB states
USB_READY  = "USB drive present"
USB_ABSENT = "USB drive absent"
# USB messages
COMMAND_USB_TYPE          = "USB message"
COMMAND_USB_STATE_CHANGED = "USB state changed"
COMMAND_USB_ERROR_TIMEOUT = "USB did not mount in the expected time"
