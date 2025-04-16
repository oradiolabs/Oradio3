#!/usr/bin/env python3
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
from oradio_logging import oradio_log
from wifi_service import wifi_service

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

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
        oradio_log.info("%s created", event.src_path)
        self.service.usb_inserted()

    def on_deleted(self, event): # when file is deleted
        # do something, eg. call your function to process the image
        oradio_log.info("%s deleted", event.src_path)
        self.service.usb_removed()

class usb_service():
    """
    States and functions related to USB drive handling
    - States: Present/absent, label, wifi credentials from USB_WIFI_FILE in USB root
    - Functions: Determine USB drive presence, get USB drive label, handle USB drive insertion/removal
    Send messages on state changes
    """
    def __init__(self, queue):
        """
        Initialize USB state and error
        Start insert/remove monitor
        Report to parent process
        """
        # Initialize
        self.msg_q = queue

        # Clear error
        self.error = None

        # Check if USB is mounted
        if os.path.ismount(USB_MOUNT_POINT):
            # Set USB state
            self.state = STATE_USB_PRESENT
            # Handle wifi credentials
            self.handle_usb_wifi_credentials()
        else:
            # Set USB state
            self.state = STATE_USB_ABSENT

        # Set observer to handle USB inserted/removed events
        self.observer = Observer()
        event_handler = USBMonitor(self, patterns=[USB_MONITOR])
        self.observer.schedule(event_handler, path = USB_MOUNT_PATH)
        self.observer.start()

        # Send initial state and error message
        self.send_usb_message()

    def send_usb_message(self):
        """
        Send USB message
        """
        # Create message
#OMJ: Het type klopt niet? Het is geen web service state message, eerder iets als info. Maar voor control is wel een state...
        message = {"type": MESSAGE_USB_TYPE, "state": self.state}

        # Optionally add error message
        if self.error:
            message["error"] = self.error
        else:
            message["error"] = MESSAGE_NO_ERROR

        # Put message in queue
        oradio_log.debug("Send USB service message: %s", message)
        self.msg_q.put(message)

    def get_state(self):
        """
        Return usb service status
        """
        return self.state

    def handle_usb_wifi_credentials(self):
        """
        Check if wifi credentials are available on the USB drive root folder
        If exists, then try to connect using the wifi credentials from the file
        """
        # If USB is present look for and parse USB_WIFI_FILE
        if self.state == STATE_USB_PRESENT:

            # Check if wifi credentials file exists in USB drive root
            if not os.path.isfile(USB_WIFI_FILE):
                oradio_log.debug("'%s' not found", USB_WIFI_FILE)
                return

            # Read and parse JSON file
            with open(USB_WIFI_FILE, "r") as f:
                try:
                    # Get JSON object as a dictionary
                    data = json.load(f)
                except:
                    oradio_log.error("Error parsing '%s'", USB_WIFI_FILE)
                    self.error = MESSAGE_USB_ERROR_FILE
                    return

            # Check if the SSID and PASSWORD keys are present
            if data and 'SSID' in data.keys() and 'PASSWORD' in data.keys():
                ssid = data['SSID']
                pswd = data['PASSWORD']
            else:
                oradio_log.error("SSID and/or PASSWORD not found in '%s'", USB_WIFI_FILE)
                self.error = MESSAGE_USB_ERROR_FILE
                return

            # Test if ssid not empty
            if len(ssid) == 0:
                oradio_log.error("SSID=%s cannot be empty", ssid)
                self.error = MESSAGE_USB_ERROR_FILE
                return

            # Test if pswd is 8 or more characters
            if len(pswd) < 9:
                oradio_log.error("PASSWORD=%s cannot be less than 8 characters", pswd)
                self.error = MESSAGE_USB_ERROR_FILE
                return

            # Log wifi credentials found
            oradio_log.info("USB wifi credentials found: ssid=%s, password=%s", ssid, pswd)

            # Connect to the wifi network
            wifi_service(self.msg_q).wifi_connect(ssid, pswd)

        else:
            # USB is absent
            oradio_log.info("USB state '%s': Ignore '%s'", self.state, USB_WIFI_FILE)

    def usb_inserted(self):
        """
        Register USB drive inserted, check USB label, handle any wifi credentials
        Send state message
        """
        oradio_log.info("USB inserted")

        # Set USB state
        self.state = STATE_USB_PRESENT

        # Clear error
        self.error = None

        # Get wifi credentials
        self.handle_usb_wifi_credentials()

        # send message
        self.send_usb_message()

    def usb_removed(self):
        """
        Register USB drive removed
        Send state message
        """
        oradio_log.info("USB removed")

        # Set state and clear info
        self.state = STATE_USB_ABSENT
        self.error = None

        # send message
        self.send_usb_message()

    def stop(self):
        """
        Stop the monitor daemon
        """
        # Only stop if active
        if self.observer:
            self.observer.stop()
            self.observer.join()
        else:
            oradio_log.warning("USB service already stopped")

        # Log status
        oradio_log.info("USB service stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("Listening for messages\n")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\nMessage received: '{message}'\n")

    # Initialize
    monitor = None
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Start USB service\n"
                       " 2-Trigger USB inserted\n"
                       " 3-Trigger USB removed\n"
                       " 4-Get USB state\n"
                       " 5-Get USB wifi credentials\n"
                       " 6-stop USB service\n"
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
                print("\nExiting test program...\n")
                if monitor:
                    monitor.stop()
                break
            case 1:
                if not monitor:
                    print("\nStarting the USB service...\n")
                    monitor = usb_service(message_queue)
                else:
                    print("\nUSB service already running\n")
            case 2:
                if monitor:
                    print("\nSimulate 'USB inserted' event...\n")
                    monitor.usb_inserted()
                else:
                    print("\nUSB service not running\n")
            case 3:
                if monitor:
                    print("\nSimulate 'USB removed' event...\n")
                    monitor.usb_removed()
                else:
                    print("\nUSB service not running\n")
            case 4:
                if monitor:
                    print(f"\nUSB state: {monitor.get_state()}\n")
                else:
                    print("\nUSB service not running\n")
            case 5:
                if monitor:
                    print("\nGet USB wifi credentials...\n")
                    monitor.handle_usb_wifi_credentials()
                else:
                    print("\nUSB service not running\n")
            case 6:
                if monitor:
                    print("\nStopping the USB service...\n")
                    monitor.stop()
                else:
                    print("\nUSB service not running\n")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
