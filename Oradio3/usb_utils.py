"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for wifi connectivity services
    :Note
    :Install
    :Documentation
        https://pypi.org/project/usb-monitor/
"""
import os, json
from time import time, sleep
from usbmonitor import USBMonitor

##### oradio modules ####################
import oradio_utils
##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
# System is configured to automount USB drives to /media/sd[a..][1..9]
# As the Oradio has only 1 single USB port, the drive will be available on /media/sda1
USB_DEVICE = "/dev/sda1"
USB_MOUNT  = "/media/sda1"
# File name in USB root with wifi credentials
USB_WIFI_FILE = USB_MOUNT +"/Wifi_invoer.json"
# Wait for USB to be mounted
USB_POLL_TIMEOUT  = 10
USB_POLL_INTERVAL = 0.25

class oradio_usb():
    """
    States and functions related to USB drive handling
    - States: Present/absent, label, wifi credentials from USB_WIFI_FILE in USB root
    - Functions: Determine USB drive presnce, get USB drive label, handle USB drive insertion/removal
    Send messages on state changes
    """

    def __init__(self, queue):
        """
        Initialize USB state and info
        Start insert/remove monitor
        Report to parent process
        """
        # Initialize
        self.state     = USB_ABSENT
        self.msg_q     = queue
        self.label     = None
        self.wifi_info = None

        # Check if USB is mounted
        if os.path.isdir(USB_MOUNT):
            # Set USB state
            self.state = USB_READY
            # Get USB label
            self.get_usb_label()
            # Get wifi credentials
            self.get_usb_wifi_info()
        else:
            # Set USB state
            self.state = USB_ABSENT

        # Create monitor
        self.monitor = USBMonitor()
        # Start monitoring insert/remove events
        self.monitor.start_monitoring(on_connect=self.usb_inserted, on_disconnect=self.usb_removed)

        # Send initial state and info message
        self.send_usb_info()

        # Log progress
        oradio_utils.logging("info", "oradio_usb initialized")

    def send_usb_info(self):
        """
        Send USB state and info message
        """
        # Create message
        message = {}
        message["command_type"] = COMMAND_USB_TYPE
        message["command"]      = COMMAND_USB_STATE_CHANGED
        message["usb_state"]    = self.state
        message["usb_label"]    = self.label
        message["usb_wifi"]     = self.wifi_info

        # Put message in queue
        self.msg_q.put(message)

        # Log progress
        oradio_utils.logging("info", "usb info message sent")

    def send_usb_error(self):
        """
        Send USB timeout error
        """
        # Create message
        message = {}
        message["command_type"] = COMMAND_USB_TYPE
        message["command"]      = COMMAND_USB_ERROR_TIMEOUT

        # Put message in queue
        self.msg_q.put(message)

        # Log progress
        oradio_utils.logging("info", "usb error message sent")

    def get_usb_label(self):
        """
        Get USB drive label 
        """
        # Get USB label
        self.label = None
        cmd = f"eval $(/sbin/blkid -o udev {USB_DEVICE}) && echo ${{ID_FS_LABEL}}"
        result, label = oradio_utils.run_shell_script(cmd)
        if result:
            # Remove leading and trailing white spaces, including \n
            self.label = label.strip()
            # Empty label = No label
            if len(self.label) == 0:
                self.label = None

        # Log progress
        oradio_utils.logging("info", f"USB label: {self.label}")

        # Return for processing
        return self.label

    def get_usb_wifi_info(self):
        """
        Check if wifi settings are available on the USB drive root folder
        If exists, then try to connect using the wifi crendtials from the file
        """
        # Check if wifi settings file exists in USB drive root
        if not os.path.isfile(USB_WIFI_FILE):
            oradio_utils.logging("info", f"'{USB_WIFI_FILE}' not found")
            return

        # Read JSON file
        with open(USB_WIFI_FILE, "r") as f:
            # returns JSON object as a dictionary
            data = json.load(f)

        # Check if the SSID key is present
        if 'SSID' in data.keys():
            ssid = data['SSID']
        else:
            ssid = None
            oradio_utils.logging("error", f"SSID not found in '{USB_WIFI_FILE}'")

        # Check if the PASSWORD key is present
        if 'PASSWORD' in data.keys():
            pswd = data['PASSWORD']
        else:
            pswd = None
            oradio_utils.logging("error", f"PASSWORD not found in '{USB_WIFI_FILE}'")

        # Register wifi info
        self.wifi_info = {"SSID": ssid, "PASSWORD": pswd}

        # Log progress
        oradio_utils.logging("info", f"USB Wifi info: {self.wifi_info}")

        # Return for processing
        return self.wifi_info

    def usb_inserted(self, device_id, device_info):
        """
        Wait for USB drive to be mounted
        Register USB drive inserted, get USB label, check for any wifi credentials
        Send state changed message
        """
        if self.state == USB_READY:
            oradio_utils.logging("error", "Panic! the USB state cannot be ready as this function is only called when the USB drive is inserted")

        # The 'inserted' event goes to OS and here at the same time.
        # Therefore we poll (with timeout) for the OS to mount the USB drive
        timeout = time() + USB_POLL_TIMEOUT
        while time() < timeout:
            # Check if USB drive is available
            if os.path.isdir(USB_MOUNT):
                # Set USB state
                self.state = USB_READY
                # Get USB label
                self.get_usb_label()
                # Get wifi credentials
                self.get_usb_wifi_info()
                # send message
                self.send_usb_info()
                # All done
                return
            # Wait before next poll
            sleep(USB_POLL_INTERVAL)

        oradio_utils.logging("error", "Timeout: failed to mount USB drive")

        # Set USB state
        self.state = USB_ABSENT

        # send message
        self.send_usb_error()

        # Log progress
        oradio_utils.logging("info", "USB inserted")

    def usb_removed(self, device_id, device_info):
        """
        Register USB drive removed
        Send state changed message
        """
        if self.state == USB_ABSENT:
            oradio_utils.logging("warning", "The USB state should not be absent as this function is only called when the USB drive is removed. No corrective action required.")

        # Set state and info
        self.state     = USB_ABSENT
        self.label     = None
        self.wifi_info = None

        # send message
        self.send_usb_info()

        # Log progress
        oradio_utils.logging("info", "USB removed")

    def usb_monitor_stop(self):
        """
        Stop the monitor daemon
        :param monitor ==> identfies the monitor daemon
        """
        # Only stop if active
        if not self.monitor:
            oradio_utils.logging("warning", "USB monitor already stopped")
        else:
            self.monitor.stop_monitoring()

        # Log progress
        oradio_utils.logging("info", "USB monitor stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    # Initialize
    usb_service = None
    message_queue = Queue()

    def check_messages(message_queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param message_queue = the queue to check for
        """
        while True:
            message = message_queue.get(block=True, timeout=None)
            oradio_utils.logging("info", f"QUEUE-msg received, message ={message}")

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Start USB monitor\n"
                       " 2-Trigger USB inserted\n"
                       " 3-Trigger USB removed\n"
                       " 4-Send USB info\n"
                       " 5-Send USB error\n"
                       " 6-Get USB label\n"
                       " 7-Get USB wifi info\n"
                       " 8-stop USB monitoring\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                break
            case 1:
                if not usb_service:
                    usb_service = oradio_usb(message_queue)
            case 2:
                if usb_service:
                    usb_service.usb_inserted(None, None)
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 3:
                if usb_service:
                    usb_service.usb_removed(None, None)
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 4:
                if usb_service:
                    usb_service.send_usb_info()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 5:
                if usb_service:
                    usb_service.send_usb_error()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 6:
                if usb_service:
                    usb_service.get_usb_label()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 7:
                if usb_service:
                    usb_service.get_usb_wifi_info()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 8:
                if usb_service:
                    usb_service.usb_monitor_stop()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
