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
from threading import Lock, RLock
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import safe_put, run_shell_script

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    USB_MOUNT_PATH,
    USB_MOUNT_POINT,
    MESSAGE_USB_TYPE,
    STATE_USB_PRESENT,
    STATE_USB_ABSENT,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
USB_MONITOR = "usb_ready"   # Name of file used to monitor if USB is mounted or not
TIMEOUT     = 10            # Seconds to wait

class USBObserver:
    """Custom singleton wrapper around Observer."""
    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, *args, **kwargs):
        if not self._initialized:
            self._observer = Observer(*args, **kwargs)
            self._initialized = True

    def __getattr__(self, name):
        """
        Automatically forward attribute/method lookups to the underlying Observer
        This is only called if the attribute isn't found on USBObserver itself
        """
        return getattr(self._observer, name)

class USBMonitor(PatternMatchingEventHandler):
    """
    Singleton that monitors USB mount/unmount events
    Subscribers can register two callbacks:
      - on_insert(): called when a USB is detected
      - on_remove(): called when a USB is removed
    """
    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    def __new__(cls, *args, **kwargs):
        """
        Create or return the singleton instance
        Uses a class-level lock to ensure thread safety during creation
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, **kwargs):
        """
        Initialize the singleton (only once)
        Sets up:
        - Subscriber list
        - Initial USB mount state
        """
        if getattr(self, "_initialized", False):
            return  # Already initialized

        # Initialize parent event handler
        super().__init__(**kwargs)

        # Lock for subscriber operations
        self._sub_lock = RLock()

        # List of subscriber tuples: (on_insert, on_remove)
        self._subscribers = []

        # Set initial USB mount state - singleton: read/write is thread-safe
        if os.path.ismount(USB_MOUNT_POINT):
            self._state = STATE_USB_PRESENT
        else:
            self._state = STATE_USB_ABSENT

        # Flag to stop initializing more than once
        self._initialized = True

    def get_state(self):
        """
        Return the current USB state
        :return: STATE_USB_PRESENT or STATE_USB_ABSENT
        """
        return self._state

    def subscribe(self, on_insert, on_remove):
        """
        Register subscriber callbacks
        Thread-safe: may be called from multiple threads
        """
        with self._sub_lock:
            self._subscribers.append((on_insert, on_remove))

    def unsubscribe(self, on_insert, on_remove):
        """
        Remove subscriber callbacks
        Thread-safe: may be called from multiple threads
        """
        with self._sub_lock:
            self._subscribers.remove((on_insert, on_remove))

    def on_created(self, event):
        """
        Watchdog callback: called when the USB mount point is created
        Updates state and triggers all on_insert callbacks
        """
        oradio_log.debug("Mount point %s created", event.src_path)
        # set state to PRESENT
        self._state = STATE_USB_PRESENT
        # Trigger insert callback for each subscriber
        for on_insert, _ in self._subscribers:
            on_insert()

    def on_deleted(self, event):
        """
        Watchdog callback: called when the USB mount point is removed
        Updates state and triggers all on_remove callbacks
        """
        oradio_log.debug("Mount point %s deleted", event.src_path)
        # set state to ABSENT
        self._state = STATE_USB_ABSENT
        # Trigger remove callback for each subscriber
        for _, on_remove in self._subscribers:
            on_remove()

class USBService:
    """
    Singleton subclass of Observer
    All USBService objects share the same observer thread
    Send messages on USB drive present/absent state changes
    """
    def __init__(self, queue):
        """"Setup observer and send current state"""
        # Get the shared observer
        self._observer = USBObserver()

        # Get the shared monitor
        self._monitor = USBMonitor(patterns=[USB_MONITOR])

        # Subscribe callbacks for this service
        self._monitor.subscribe(self._usb_inserted, self._usb_removed)

        # Only schedule path once to avoid duplicates
        if not getattr(self._observer, "_usb_scheduled", False):
            self._observer.schedule(self._monitor, path=USB_MOUNT_PATH, recursive=False)
            self._observer._usb_scheduled = True
            # Start the observer once globally
            if not self._observer.is_alive():
                self._observer.start()

        # Register queue for sending messages
        self._queue = queue

        # Send initial state
        self._send_message()

    def close(self):
        """Unsubscribe from USBMonitor"""
        self._monitor.unsubscribe(self._usb_inserted, self._usb_removed)

    def _usb_inserted(self):
        """Send state message USB drive inserted"""
        oradio_log.info("USB inserted")
        # send message
        self._send_message()

    def _usb_removed(self):
        """Send state message USB drive removed"""
        oradio_log.info("USB removed")
        # send message
        self._send_message()

    def _send_message(self):
        """Send USB service message"""
        # Create message
        message = {
            "type": MESSAGE_USB_TYPE,
            "state": self.get_state(),
            "error": MESSAGE_NO_ERROR
        }
        # Put message in queue
        oradio_log.debug("Send USB service message: %s", message)
        safe_put(self._queue, message)

    def get_state(self):
        """Getter for USB state"""
        return self._monitor.get_state()

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # Imports only relevant when stand-alone
    from multiprocessing import Process, Queue

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    def interactive_menu(queue):
        """Show menu with test options"""
        # Initialize: no services registered
        usb_services = []

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-quit\n"
            " 1-Add USBService instance\n"
            " 2-Remove USBService instance\n"
            " 3-Simulate USB inserted\n"
            " 4-Simulate USB removed\n"
            " 5-Get USB state\n"
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
                    break
                case 1:
                    print("\nAdd USBService to list\n")
                    usb_services.append(USBService(queue))
                    print(f"\nList has {len(usb_services)} instances\n")
                case 2:
                    print("\nDelete USBService from list\n")
                    if usb_services:
                        usb_services.pop().close()
                        print(f"List has {len(usb_services)} instances\n")
                    else:
                        print(f"{YELLOW}List has no USBService instances{NC}\n")
                case 3:
                    print("\nSimulate 'USB inserted' event...\n")
                    # Need to use subprocess because monitor is owned by root
                    cmd = f"sudo touch {USB_MOUNT_PATH}/{USB_MONITOR}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to create monitor, error: %s", cmd, response)
                case 4:
                    print("\nSimulate 'USB removed' event...\n")
                    # Need to use subprocess because monitor is owned by root
                    cmd = f"sudo rm -f {USB_MOUNT_PATH}/{USB_MONITOR}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to remove monitor, error: %s", cmd, response)
                case 5:
                    # As USBMonitor is a singleton we can use it direct
                    print(f"\nUSB state: {USBMonitor().get_state()}\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

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
