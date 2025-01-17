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
# System is configured to automount USB drives to /media/sd[a..][1..9]
# As the Oradio has only 1 single USB port, the drive will be available on /media/sda1
USB_BASE   = "sda1"
USB_DEVICE = "/dev/" + USB_BASE
USB_MOUNT  = "/media/" + USB_BASE
# Wait for USB to be mounted
USB_POLL_TIMEOUT  = 5
USB_POLL_INTERVAL = 0.25
# File name in USB root with wifi credentials
USB_WIFI_FILE = USB_MOUNT +"/Wifi_invoer.json"
# USB drive must have this label for 'Oradio' operation
USB_ORADIO = "ORADIO"
