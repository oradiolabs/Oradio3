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
import oradio_utils, wifi_utils
##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
# System is configured to automount USB drives to /media/sd[a..][1..9]
# As the Oradio has only 1 single USB port, the drive will be available on /media/sda1
USB_DEVICE = "/dev/sda1"
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
        self.state = STATE_USB_ABSENT
        self.msg_q = queue
        self.label = None
        self.error = None

        # Check if USB is mounted
        if os.path.isdir(USB_MOUNT):
            # Set USB state
            self.state = STATE_USB_PRESENT
            # Get USB label
            self.get_usb_label()
            # Handle wifi credentials
            self.handle_usb_wifi_info()
        else:
            # Set USB state
            self.state = STATE_USB_ABSENT

        # Create monitor
        self.monitor = USBMonitor()
        # Start monitoring insert/remove events
        self.monitor.start_monitoring(on_connect=self.usb_inserted, on_disconnect=self.usb_removed)

        # Send initial state and info message
        self.send_usb_message()

        # Log status
        oradio_utils.logging("info", "oradio_usb initialized")

    def send_usb_message(self, *args, **kwargs):
        """
        Send USB message, depending on the state
        """
        # Check if state, label or error parameters provided to override
        state = kwargs.get('state', self.state)
        label = kwargs.get('label', self.label)
        error = kwargs.get('error', self.error)

        # Create message
        message = {}
        message["type"]  = MESSAGE_USB_TYPE
        message["state"] = state

        # Additional info
        if state == STATE_USB_ERROR:
            message["error"] = error
            if error == MESSAGE_USB_ERROR_LABEL:
                message["label"] = label

        # Put message in queue
        self.msg_q.put(message)

        # Log status
        oradio_utils.logging("info", f"usb message sent: {message}")

    def get_usb_label(self):
        """
        Get USB drive label
        Set error state if label does not match oradio label
        """
        # Get USB label
        self.label = None
        cmd = f"eval $(/sbin/blkid -o udev {USB_DEVICE}) && echo ${{ID_FS_LABEL}}"
        result, label = oradio_utils.run_shell_script(cmd)
        if result:
            # Remove leading and trailing white spaces, including \n
            self.label = label.strip()

        # Only accept USB drives with oradio label
        if self.label != LABEL_USB_ORADIO:
            self.state = STATE_USB_ERROR
            self.error = MESSAGE_USB_ERROR_LABEL

        # Log status
        oradio_utils.logging("info", f"USB label: '{self.label}'")

    def handle_usb_wifi_info(self):
        """
        Check if wifi settings are available on the USB drive root folder
        If exists, then try to connect using the wifi credentials from the file
        """
        # Initialize
        ssid = None
        pswd = None

        # Skip if USB drive is absent or has error
        if self.state == STATE_USB_PRESENT:

            # Check if wifi settings file exists in USB drive root
            if not os.path.isfile(USB_WIFI_FILE):
                oradio_utils.logging("info", f"'{USB_WIFI_FILE}' not found")
                return

            # Read JSON file
            with open(USB_WIFI_FILE, "r") as f:
                # returns JSON object as a dictionary
                data = json.load(f)

            # Check if the SSID and PASSWORD keys are present
            if 'SSID' in data.keys() and 'PASSWORD' in data.keys():
                ssid = data['SSID']
                pswd = data['PASSWORD']
            else:
                oradio_utils.logging("error", f"SSID and/or PASSWORD not found in '{USB_WIFI_FILE}'")

            # Log status
            oradio_utils.logging("info", f"USB Wifi info found: ssid={ssid}, password={pswd}")

            # Test if Oradio is currently connected to ssid
            if not ssid == wifi_utils.get_wifi_connection():
                # Currently not connected to ssid, try to connected
                if wifi_utils.wifi_autoconnect(ssid, pswd):
                    oradio_utils.logging("success", f"Connected to '{ssid}'")
                else:
                    oradio_utils.logging("error", f"Failed to connect to '{ssid}'")
            else:
                oradio_utils.logging("info", f"Already connected to '{ssid}'")

        else:
            # Log status
            oradio_utils.logging("info", f"USB state: '{self.state}' ==> Ignore '{USB_WIFI_FILE}'")

    def usb_inserted(self, device_id, device_info):
        """
        Wait for USB drive to be mounted
        Register USB drive inserted, check USB label, handle any wifi credentials
        Send state message
        """
        # The 'inserted' event goes to OS and here at the same time.
        # Therefore we poll (with timeout) for the OS to mount the USB drive
        timeout = time() + USB_POLL_TIMEOUT
        while time() < timeout:
            # Check if USB drive is available
            if os.path.isdir(USB_MOUNT):
                # Set USB state
                self.state = STATE_USB_PRESENT
                # Get USB label
                self.get_usb_label()
                # Get wifi credentials
                self.handle_usb_wifi_info()
                # send message
                self.send_usb_message()
                # Log status
                oradio_utils.logging("info", "USB inserted")
                # All done
                return
            # Wait before next poll
            sleep(USB_POLL_INTERVAL)


        # Set USB error state and error message
        self.state = STATE_USB_ERROR
        self.error = MESSAGE_USB_ERROR_TIMEOUT

        # send message
        self.send_usb_message()

        # Log status
        oradio_utils.logging("error", "Timeout: failed to mount USB drive")

    def usb_removed(self, device_id, device_info):
        """
        Register USB drive removed
        Send state message
        """
        # Set state and info
        self.state = STATE_USB_ABSENT
        self.label = None
        self.error = None

        # send message
        self.send_usb_message()

        # Log status
        oradio_utils.logging("info", "USB removed")

    def usb_monitor_stop(self):
        """
        Stop the monitor daemon
        """
        # Only stop if active
        if not self.monitor:
            oradio_utils.logging("warning", "USB monitor already stopped")
        else:
            self.monitor.stop_monitoring()

        # Log status
        oradio_utils.logging("info", "USB monitor stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    # Initialize
    usb_handler = None
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
                       " 4-Send USB message\n"
                       " 5-Get USB label\n"
                       " 6-Get USB wifi info\n"
                       " 7-stop USB monitoring\n"
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
                if not usb_handler:
                    usb_handler = oradio_usb(message_queue)
            case 2:
                if usb_handler:
                    usb_handler.usb_inserted(None, None)
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 3:
                if usb_handler:
                    usb_handler.usb_removed(None, None)
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 4:
                state = input("Enter state (present | absent | error | other): ")
                label = input("Enter label (oradio | other): ")
                error = input("Enter error (label | timeout | other): ")
                if state and error and label and usb_handler:
                    usb_handler.send_usb_message(state=state, label=label, error=error)
                else:
                    oradio_utils.logging("warning", "state, label and/or error not provided, or USB monitor not running")
            case 5:
                if usb_handler:
                    usb_handler.get_usb_label()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 6:
                if usb_handler:
                    usb_handler.handle_usb_wifi_info()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 7:
                if usb_handler:
                    usb_handler.usb_monitor_stop()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
