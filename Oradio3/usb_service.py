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
@summary: Class for USB detect, insert, and remove services
    :Note
    :Install
    :Documentation
        The OS is configured to auto-mount USB drives with label = ORADIO
        When mounting is complete a MONITOR is created
        Using a watchdog triggered by MONITOR handles the USB insert/removed behaviour
        https://pypi.org/project/watchdog/
"""
import os, json
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
import oradio_utils, wifi_utils
##### GLOBAL constants ####################
from oradio_const import *
##### LOCAL constants ####################
MONITOR = "usb_ready"

# MONITOR is used to signal mounting/unmounting is complete
class USBMonitor(PatternMatchingEventHandler):
    """
    Monitor changes to USB mount point
    Calls inserted/removed functions
    """
    def __init__(self, service, *args, **kwargs):
        super(USBMonitor, self).__init__(*args, **kwargs)
        self.service = service

    def on_created(self, event): # when file is created
        # do something, eg. call your function to process the image
        print(f"on_created: Got event for file {event.src_path}")
        self.service.usb_inserted()

    def on_deleted(self, event): # when file is deleted
        # do something, eg. call your function to process the image
        print(f"on_deleted: Got event for file {event.src_path}")
        self.service.usb_removed()

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
        self.msg_q = queue

        # Check if USB is mounted
        if os.path.isdir(USB_MOUNT_POINT):
            # Set USB state
            self.state = STATE_USB_PRESENT
            # Clear error
            self.error = None
            # Handle wifi credentials
            self.handle_usb_wifi_info()
        else:
            # Set USB state
            self.state = STATE_USB_ABSENT
            self.error = None

        # Set observer to handle USB inserted/removed events
        self.observer = Observer()
        event_handler = USBMonitor(self, patterns=[MONITOR])
        self.observer.schedule(event_handler, path = USB_MOUNT_PATH)
        self.observer.start()

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
        error = kwargs.get('error', self.error)

        # Create message
        message = {}
        message["type"]  = MESSAGE_USB_TYPE
        message["state"] = state

        # Optionally add error message
        if error:
            message["error"] = error

        # Put message in queue
        self.msg_q.put(message)

        # Log status
        oradio_utils.logging("info", f"usb message sent: {message}")

    def handle_usb_wifi_info(self):
        """
        Check if wifi settings are available on the USB drive root folder
        If exists, then try to connect using the wifi credentials from the file
        """
        # If USB is present look for and parse USB_WIFI_FILE
        if self.state == STATE_USB_PRESENT:

            # Check if wifi settings file exists in USB drive root
            if not os.path.isfile(USB_WIFI_FILE):
                oradio_utils.logging("info", f"'{USB_WIFI_FILE}' not found")
                return

            # Read and parse JSON file
            with open(USB_WIFI_FILE, "r") as f:
                try:
                    # Get JSON object as a dictionary
                    data = json.load(f)
                except:
                    oradio_utils.logging("warning", f"Error parsing '{USB_WIFI_FILE}'")
                    self.error = MESSAGE_USB_ERROR_FILE
                    return

            # Check if the SSID and PASSWORD keys are present
            if data and 'SSID' in data.keys() and 'PASSWORD' in data.keys():
                ssid = data['SSID']
                pswd = data['PASSWORD']
            else:
                oradio_utils.logging("warning", f"SSID and/or PASSWORD not found in '{USB_WIFI_FILE}'")
                self.error = MESSAGE_USB_ERROR_FILE
                return

            # Test if ssid and pswd not empty
            if len(ssid) == 0 or len(pswd) == 0:
                oradio_utils.logging("warning", f"SSID={ssid} and/or PASSWORD={pswd} cannot be empty")
                self.error = MESSAGE_USB_ERROR_FILE
                return

            # Log wifi info found
            oradio_utils.logging("info", f"USB Wifi info found: ssid={ssid}, password={pswd}")

            # Test if Oradio is currently connected to ssid
            if not ssid == wifi_utils.get_wifi_connection():
                # Currently not connected to ssid, try to connected
                if wifi_utils.wifi_autoconnect(ssid, pswd):
                    oradio_utils.logging("success", f"Connected to '{ssid}'")
                else:
                    oradio_utils.logging("error", f"Failed to connect to '{ssid}'")
                    self.error = MESSAGE_USB_ERROR_CONNECT
                    return
            else:
                oradio_utils.logging("info", f"Already connected to '{ssid}'")

        else:
            # USB is absent
            oradio_utils.logging("info", f"USB state '{self.state}': Ignore '{USB_WIFI_FILE}'")

    def usb_inserted(self):
        """
        Register USB drive inserted, check USB label, handle any wifi credentials
        Send state message
        """
        # Set USB state
        self.state = STATE_USB_PRESENT

        # Clear error
        self.error = None

        # Get wifi credentials
        self.handle_usb_wifi_info()

        # send message
        self.send_usb_message()

        # Log status
        oradio_utils.logging("info", "USB inserted")

    def usb_removed(self):
        """
        Register USB drive removed
        Send state message
        """
        # Set state and clear info
        self.state = STATE_USB_ABSENT
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
        if self.monitor:
            self.monitor.stop_monitoring()
            self.observer.stop()
            self.observer.join()
        else:
            oradio_utils.logging("warning", "USB monitor already stopped")

        # Log status
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
        oradio_utils.logging("info", f"Listening for USB messages")

        while True:

            # Wait for USB message
            message = message_queue.get(block=True, timeout=None)

            # Show message received
            oradio_utils.logging("info", f"Message received from USB: '{message}'")

            # Show message type
            msg_type = message["type"]
            oradio_utils.logging("info", f"USB type = '{msg_type}'")

            # Parse USB message
            if msg_type == MESSAGE_USB_TYPE:

                # Show USB state
                usb_state = message["state"]
                oradio_utils.logging("info", f"USB state = '{usb_state}'")

                # USB is present: Nothing to do?
                if usb_state == STATE_USB_PRESENT:
                    pass

                # USB is absent: Nothing to do?
                elif usb_state == STATE_USB_ABSENT:
                    pass

                # Unexpected 'state' message
                else:
                    oradio_utils.logging("error", f"Unsupported 'state' message: '{usb_state}'")

                # Check for error messages
                if 'error' in message.keys():

                    # Show error message
                    usb_error = message["error"]
                    oradio_utils.logging("info", f"USB error: '{usb_error}'")

                    # File USB_WIFI_FILE is not correct
                    if usb_error == MESSAGE_USB_ERROR_FILE:

                        # Show error
                        oradio_utils.logging("warning", f"USB drive file '{USB_WIFI_FILE}' is invalid")

                    # File USB_WIFI_FILE is not correct
                    elif usb_error == MESSAGE_USB_ERROR_CONNECT:

                        # Show error
                        oradio_utils.logging("warning", f"Failed to connect with wifi credentials in '{USB_WIFI_FILE}'")

                    # Unexpected 'error' message
                    else:
                        oradio_utils.logging("error", f"Unsupported 'error' message: '{usb_error}'")

            # Unexpected 'type' message
            else:
                oradio_utils.logging("error", f"Unsupported 'type' message: '{msg_type}'")

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Start USB monitor\n"
                       " 2-Trigger USB inserted\n"
                       " 3-Trigger USB removed\n"
                       " 4-Get USB wifi info\n"
                       " 5-stop USB monitoring\n"
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
                    usb_service.usb_inserted()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 3:
                if usb_service:
                    usb_service.usb_removed()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 4:
                if usb_service:
                    usb_service.handle_usb_wifi_info()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 5:
                usb_service.usb_monitor_stop()
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
