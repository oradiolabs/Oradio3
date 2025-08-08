#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on January 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
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
from threading import Lock
from multiprocessing import Process, Queue
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import safe_put

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, NC,
    USB_MOUNT_PATH,
    USB_MOUNT_POINT,
    MESSAGE_USB_TYPE,
    STATE_USB_PRESENT,
    STATE_USB_ABSENT,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
USB_MONITOR = "usb_ready"                             # Name of file used to monitor if USB is mounted or not
TIMEOUT     = 10                                      # Seconds to wait

class USBService():
    """
    Determine USB drive present/absentce
    Send messages on state changes
    """
    class USBMonitor(PatternMatchingEventHandler):
        """
        Monitor signals when USB mounting/unmounting is ready
        Calls inserted/removed functions
        """
        def __init__(self, inserted, removed, *args, **kwargs):
            """Class contructor, including parent class PatternMatchingEventHandler"""
            super().__init__(*args, **kwargs)
            self.inserted = inserted
            self.removed = removed

        def on_created(self, event):
            """When file is created"""
            oradio_log.debug("Mount point %s created", event.src_path)
            self.inserted()

        def on_deleted(self, event):
            """When file is deleted"""
            oradio_log.debug("Mount point %s deleted", event.src_path)
            self.removed()

    def __init__(self, queue):
        """"Class constructor, setup observer and send current state"""
        # For thread-safe state read/write
        self.state = None
        self._state_lock = Lock()

        # Register queue for sending message to controller
        self.queue = queue

        # Set initial USB mount state
        if os.path.ismount(USB_MOUNT_POINT):
            self._set_state(STATE_USB_PRESENT)
        else:
            self._set_state(STATE_USB_ABSENT)

        # Set observer to handle USB inserted/removed events
        self.observer = Observer()
        # Pass private functions as arguments to avoid pylint protected-access warnings
        event_handler = self.USBMonitor(self._usb_inserted, self._usb_removed, patterns=[USB_MONITOR])
        self.observer.schedule(event_handler, path = USB_MOUNT_PATH, recursive=False)
        self.observer.start()

        # Send initial state and error message
        self._send_message(MESSAGE_NO_ERROR)

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
        safe_put(self.queue, message)

    def _set_state(self, new_state):
        """Thread-safe setter for USB state"""
        with self._state_lock:
            self.state = new_state

    def get_state(self):
        """Thread-safe getter for USB state"""
        with self._state_lock:
            return self.state

    def _usb_inserted(self):
        """
        Register USB drive inserted, check USB label, handle any wifi credentials
        Send state message
        """
        oradio_log.info("USB inserted")
        # Set USB state
        self._set_state(STATE_USB_PRESENT)
        # send message
        self._send_message(MESSAGE_NO_ERROR)

    def _usb_removed(self):
        """
        Register USB drive removed
        Send state message
        """
        oradio_log.info("USB removed")
        # Set state and clear info
        self._set_state(STATE_USB_ABSENT)
        # send message
        self._send_message(MESSAGE_NO_ERROR)

    def _stop(self):
        """Stop the monitor"""
        # Only stop if active
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=TIMEOUT)
        else:
            oradio_log.warning("USB service already stopped")
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
        print("\nMain: Listening for messages\n")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Main: Message received: '{message}'{NC}\n")

    # Pylint PEP8 limit of max 12 branches is ok to be disabled for test menu
    def interactive_menu(queue=None):  # pylint: disable=too-many-branches
        """Show menu with test options"""
        # Initialize
        usb = USBService(queue)

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-quit\n"
            " 1-Trigger USB inserted\n"
            " 2-Trigger USB removed\n"
            " 3-Get USB state\n"
            "select: "
        )

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1
            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    # Calling private function for testing is ok
                    usb._stop() # pylint: disable=protected-access
                    break
                case 1:
                    print("\nSimulate 'USB inserted' event...\n")
                    # Calling private function for testing is ok
                    usb._usb_inserted() # pylint: disable=protected-access
                case 2:
                    print("\nSimulate 'USB removed' event...\n")
                    # Calling private function for testing is ok
                    usb._usb_removed() # pylint: disable=protected-access
                case 3:
                    print(f"\nUSB state: {usb.get_state()}\n")
                case _:
                    print(f"{YELLOW}Please input a valid number{NC}\n")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Present menu with tests
    interactive_menu(message_queue)

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
