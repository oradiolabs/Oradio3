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
from threading import RLock
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from oradio_utils import safe_put, run_shell_script

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    USB_MOUNT_PATH,
    USB_MOUNT_POINT,
    MESSAGE_USB_SOURCE,
    STATE_USB_PRESENT,
    STATE_USB_ABSENT,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
USB_MONITOR = "usb_ready"   # Name of file used to monitor if USB is mounted or not

@singleton
class USBObserver:
    """Singleton wrapper around watchdog.Observer with exactly one monitor."""

    def __init__(self):
        # The underlying Observer thread
        self._observer = Observer()

        # Track if the single monitor has already been scheduled
        self._monitor_scheduled = False

    def schedule_monitor(self, monitor: PatternMatchingEventHandler):
        """
        Schedule the monitor exactly once on the observer.
        Starts the observer thread if not already running.
        """
        if not self._monitor_scheduled:
            self._observer.schedule(monitor, path=USB_MOUNT_PATH, recursive=False)
            self._monitor_scheduled = True

        # Start observer thread if not alive
        if not self._observer.is_alive():
            self._observer.start()

    def __getattr__(self, name):
        """
        Delegate attribute access to the underlying observer.
        Called only if attribute not found on USBObserver itself.
        """
        return getattr(self._observer, name)

@singleton
class USBMonitor(PatternMatchingEventHandler):
    """
    Singleton watchdog event handler for USB marker file.
    Allows subscribers to register insert/remove callbacks.
    """

    def __init__(self, patterns = None):
        # Initialize parent event handler (PatternMatchingEventHandler)
        super().__init__(patterns=[USB_MONITOR])

        # Lock to protect subscriber list
        self._sub_lock = RLock()

        # List of (on_insert, on_remove) callbacks
        self._subscribers = []

        # Determine initial USB state from mount point
        if os.path.ismount(USB_MOUNT_POINT):
            self._state = STATE_USB_PRESENT
        else:
            self._state = STATE_USB_ABSENT

    def get_state(self):
        """Return current USB mount state."""
        return self._state

    def subscribe(self, on_insert, on_remove):
        """Register callbacks for USB insert/remove events."""
        with self._sub_lock:
            self._subscribers.append((on_insert, on_remove))

    def unsubscribe(self, on_insert, on_remove):
        """Remove previously registered callbacks."""
        with self._sub_lock:
            self._subscribers.remove((on_insert, on_remove))

    def on_created(self, event):
        """
        Watchdog callback: called when the USB monitor is created
        Updates state and triggers all on_insert callbacks
        """
        oradio_log.info("USB inserted on %s", event.src_path)

        # set state to PRESENT
        self._state = STATE_USB_PRESENT

        # Trigger insert callback for each subscriber
        with self._sub_lock:
            for on_insert, _ in self._subscribers:
                on_insert()

    def on_deleted(self, event):
        """
        Watchdog callback: called when the USB monitor is removed
        Updates state and triggers all on_remove callbacks
        """
        oradio_log.info("USB removed from %s", event.src_path)

        # set state to ABSENT
        self._state = STATE_USB_ABSENT

        with self._sub_lock:
            for _, on_remove in self._subscribers:
                on_remove()

class USBService:
    """
    Service that connects the singleton USBObserver + USBMonitor,
    subscribes to USB events, and forwards state messages to a queue.
    """

    def __init__(self, queue):
        # Store queue for sending USB state messages asynchronously
        self._queue = queue

        # Get the shared observer singleton (handles event watching thread)
        self._observer = USBObserver()

        # Get the shared USB monitor singleton (monitors USB mount/unmount)
        self._monitor = USBMonitor()

        # Subscribe callbacks to USB insert/remove events
        self._monitor.subscribe(self._usb_inserted, self._usb_removed)

        # Schedule monitor exactly once
        self._observer.schedule_monitor(self._monitor)

        # Send initial state
        self._send_message()

    def _usb_inserted(self):
        """Callback invoked on USB insertion."""
        self._send_message()

    def _usb_removed(self):
        """Callback invoked on USB removal."""
        self._send_message()

    def _send_message(self):
        """Send current USB state message to the registered queue."""
        message = {
            "source": MESSAGE_USB_SOURCE,
            "state": self.get_state(),
            "error": MESSAGE_NO_ERROR,
        }
        oradio_log.debug("Send USBService message: %s", message)
        safe_put(self._queue, message)

    def get_state(self):
        """Return current USB mount state."""
        return self._monitor.get_state()

    def close(self):
        """Unsubscribe callbacks to clean up resources."""
        try:
            self._monitor.unsubscribe(self._usb_inserted, self._usb_removed)
        except ValueError:
            oradio_log.debug("Was already unsubscribed from USB service")
        else:
            oradio_log.info("Stopped listening to USB events")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from multiprocessing import Process, Queue

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu(queue):    # pylint: disable=too-many-branches
        """Show menu with test options"""
        # Initialize: no services registered
        usb_services = []

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
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
                    # Close each USB service instance
                    for usb_service in usb_services:
                        usb_service.close()
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
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Present menu with tests
    interactive_menu(message_queue)

    # Stop listening to messages
    message_listener.terminate()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
