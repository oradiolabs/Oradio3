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
import os
import json
from threading import Thread, Event, Lock
from multiprocessing import Process, Queue, queues
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
from oradio_logging import oradio_log
from wifi_service import WifiService

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, NC,
    USB_MOUNT_PATH,
    USB_MOUNT_POINT,
    MESSAGE_USB_TYPE,
    STATE_USB_PRESENT,
    STATE_USB_ABSENT,
    MESSAGE_NO_ERROR,
    MESSAGE_USB_ERROR_FILE
)

##### LOCAL constants ####################
USB_MONITOR   = "usb_ready"                             # Name of file used to monitor if USB is mounted or not
USB_WIFI_FILE = USB_MOUNT_POINT + "/wifi_invoer.json"   # File name in USB root with wifi credentials
TIMEOUT       = 10                                      # Seconds to wait

# MONITOR is used to signal mounting/unmounting is complete
class USBMonitor(PatternMatchingEventHandler):
    """
    Monitor changes to USB mount point
    Calls inserted/removed functions
    """
    def __init__(self, service, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service

    def on_created(self, event): # when file is created
        # do something, eg. call your function to process the image
        oradio_log.info("Mount point %s created", event.src_path)
        self.service._usb_inserted()    # pylint: disable=protected-access

    def on_deleted(self, event): # when file is deleted
        # do something, eg. call your function to process the image
        oradio_log.info("Mount point %s deleted", event.src_path)
        self.service._usb_removed()    # pylint: disable=protected-access

class USBServiceMessageHandler:
    """Class to manage message handler running in a thread"""

    def __init__(self, rx_queue, tx_queue, label=""):
        """Class constructor: Store parameters and initialise message handler thread"""
        self.started_event = Event()
        self.stop_event = Event()
        self.message_listener = Thread(
            target=self._check_messages,
            args=(
                self.started_event,
                self.stop_event,
                rx_queue,
                tx_queue,
            )
        )
        # Register identifier for multiple instances
        self.label = label

    def start(self):
        """Start the message handler"""
        self.message_listener.start()
        self.started_event.wait()

    def stop(self):
        """Stop the message handler"""
        self.stop_event.set()  # Signal the thread to stop
        self.message_listener.join(timeout=TIMEOUT)  # Wait for it to exit or timeout

    def _check_messages(self, started_event, stop_event, rx_q, tx_q):
        """
        Check if a new message is put into the queue
        If so, read the message from queue, display it, and forward it
        :param queue = the queue to check for
        """
        # Notify the thread is running
        started_event.set()
        oradio_log.debug("%sListening for messages", self.label)
        while not stop_event.is_set():
            try:
                # Wait for message. Use timeout to allow checking stop_event
                message = rx_q.get(block=True, timeout=1)
                oradio_log.debug("%sReceived: %s", self.label, message)
#OMJ: This is the place to parse the incoming message before sending a message to control
                if tx_q:
                    # Put message in queue
                    oradio_log.debug("%sForwarding: %s", self.label, message)
                    tx_q.put(message)
            except queues.Empty:
                # Timeout occurred, loop again to check stop_event
                continue

class USBService():
    """
    States and functions related to USB drive handling
    - States: Present/absent, label, wifi credentials from USB_WIFI_FILE in USB root
    - Functions: Determine USB drive presence, get USB drive label, handle USB drive insertion/removal
    Send messages on state changes
    """

    def __init__(self, queue):
        """"
        Class constructor: Setup the class
        """
        # Register queue for sending message to controller
        self.tx_queue = queue

        # Initialize queue for receiving messeages
        self.rx_queue = Queue()

        # Create message handler
        self.message_handler = USBServiceMessageHandler(self.rx_queue, self.tx_queue, "USBService: ")
        self.message_handler.start() # Returns after thread has entered its target function

        # For thread-safe state read/write
        self.state = None
        self._state_lock = Lock()

        # Check if USB is mounted
        if os.path.ismount(USB_MOUNT_POINT):
            # Set USB state
            self.set_state(STATE_USB_PRESENT)
            # Handle wifi credentials
            error = self._handle_usb_wifi_credentials()
        else:
            # Set USB state
            self.set_state(STATE_USB_ABSENT)
            error = MESSAGE_NO_ERROR

        # Set observer to handle USB inserted/removed events
        self.observer = Observer()
        event_handler = USBMonitor(self, patterns=[USB_MONITOR])
        self.observer.schedule(event_handler, path = USB_MOUNT_PATH, recursive=False)
        self.observer.start()
        print(f"Observer for directory '{USB_MOUNT_POINT}' started")

        # Send initial state and error message
        self._send_message(error)

    def _send_message(self, error):
        """
        Private function
        Send USB service message
        :param error: Error message or code to include in the message
        """
        # Create message
        message = {
            "type": MESSAGE_USB_TYPE,
            "state": self.get_state(),
            "error": error
        }

        # Put message in queue
        oradio_log.debug("Send USB service message: %s", message)
        self.tx_queue.put(message)

    def get_state(self):
        """Thread-safe getter for USB state"""
        with self._state_lock:
            return self.state

    def set_state(self, new_state):
        """Thread-safe setter for USB state"""
        with self._state_lock:
            self.state = new_state

    def _handle_usb_wifi_credentials(self):
        """
        Check if wifi credentials are available on the USB drive root folder
        If exists, then try to connect using the wifi credentials from the file
        """
        # If USB is present look for and parse USB_WIFI_FILE
        if self.get_state() == STATE_USB_PRESENT:
            oradio_log.info("Checking for %s", USB_WIFI_FILE)

            # Check if wifi credentials file exists in USB drive root
            if not os.path.isfile(USB_WIFI_FILE):
                oradio_log.debug("'%s' not found", USB_WIFI_FILE)
                return MESSAGE_NO_ERROR

            # Read and parse JSON file
            with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                try:
                    # Get JSON object as a dictionary
                    data = json.load(file)
                except json.JSONDecodeError:
                    oradio_log.error("Error parsing '%s'", USB_WIFI_FILE)
                    return MESSAGE_USB_ERROR_FILE

            # Check if the SSID and PASSWORD keys are present
            if data and 'SSID' in data.keys() and 'PASSWORD' in data.keys():
                ssid = data['SSID']
                pswd = data['PASSWORD']
            else:
                oradio_log.error("SSID and/or PASSWORD not found in '%s'", USB_WIFI_FILE)
                return MESSAGE_USB_ERROR_FILE

            # Test if ssid is empty or >= 8 characters
            if 0 < len(pswd) < 8:
                oradio_log.error("Password must be empty for open network or at least 8 characters for secured network")
                return MESSAGE_USB_ERROR_FILE

            # Log wifi credentials found
            oradio_log.info("USB wifi credentials found: ssid=%s", ssid)

            # Connect to the wifi network
            WifiService(self.rx_queue).wifi_connect(ssid, pswd)

        else:
            # USB is absent
            oradio_log.info("USB state '%s': Ignore '%s'", self.get_state(), USB_WIFI_FILE)

        # No issues found
        return MESSAGE_NO_ERROR

    def _usb_inserted(self):
        """
        Register USB drive inserted, check USB label, handle any wifi credentials
        Send state message
        """
        oradio_log.info("USB inserted")

        # Set USB state
        self.set_state(STATE_USB_PRESENT)

        # Get wifi credentials
        error = self._handle_usb_wifi_credentials()

        # send message
        self._send_message(error)

    def _usb_removed(self):
        """
        Register USB drive removed
        Send state message
        """
        oradio_log.info("USB removed")

        # Set state and clear info
        self.set_state(STATE_USB_ABSENT)

        # send message
        self._send_message(MESSAGE_NO_ERROR)

    def stop(self):
        """
        Stop the monitor daemon
        Stop the message handler
        """
        # Only stop if active
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=TIMEOUT)
        else:
            oradio_log.warning("USB service already stopped")

        # Stop web service message handler
        self.message_handler.stop()

        # Log status
        oradio_log.info("USB service stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("\nMain: Listening for messages")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"{GREEN}Main: Message received: '{message}'{NC}\n")

    # Initialize
    message_queue = Queue()
    monitor = None  # pylint: disable=invalid-name

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
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
            function_nr = int(input(INPUT_SELECTION))  # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1  # pylint: disable=invalid-name

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
                    monitor = USBService(message_queue)
                else:
                    print("\nUSB service already running\n")
            case 2:
                if monitor:
                    print("\nSimulate 'USB inserted' event...\n")
                    monitor._usb_inserted()     # pylint: disable=protected-access
                else:
                    print("\nUSB service not running\n")
            case 3:
                if monitor:
                    print("\nSimulate 'USB removed' event...\n")
                    monitor._usb_removed()      # pylint: disable=protected-access
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
                    monitor._handle_usb_wifi_credentials()      # pylint: disable=protected-access
                else:
                    print("\nUSB service not running\n")
            case 6:
                if monitor:
                    print("\nStopping the USB service...\n")
                    monitor.stop()
                else:
                    print("\nUSB service not running\n")
            case _:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
