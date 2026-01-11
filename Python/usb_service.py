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
@summary: USB mount monitoring service using watchdog.
    This module detects USB insertion and removal by monitoring a marker file
    on an auto-mounted USB drive. When a USB drive labeled ORADIO is mounted,
    a monitor file is created; its creation and deletion signal USB state changes.
    - Singleton watchdog observer
    - Subscriber-based USB insert/remove callbacks
    - Optional import of WiFi credentials from USB
    Requirements:
    - OS auto-mounts USB drives with label 'ORADIO'
    - watchdog package (https://pypi.org/project/watchdog/)
"""
from os import path, remove
from threading import RLock
from json import load, JSONDecodeError
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from wifi_service import networkmanager_add
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
USB_MONITOR   = "usb_ready"     # Name of file used to monitor if USB is mounted or not
USB_WIFI_FILE = path.join(USB_MOUNT_POINT, "Wifi_invoer.json")  # USB file with wifi credentials

@singleton
class USBObserver:
    """Singleton wrapper around watchdog.Observer with exactly one monitor."""

    def __init__(self):
        """Initialize the USB observer."""
        # The underlying Observer thread
        self._observer = Observer()

        # Track if the single monitor has already been scheduled
        self._monitor_scheduled = False

    def schedule_monitor(self, monitor) -> None:
        """
        Schedule a filesystem event monitor on the observer if it has not been
        scheduled yet, and ensure the observer thread is running.

        Args:
            monitor (PatternMatchingEventHandler): Event handler instance
            to be scheduled on the observer for monitoring filesystem events.
        """
        if not self._monitor_scheduled:
            self._observer.schedule(monitor, path=USB_MOUNT_PATH, recursive=False)
            self._monitor_scheduled = True

        # Start observer thread if not alive
        if not self._observer.is_alive():
            self._observer.start()

    def __getattr__(self, name: str):
        """
        Delegate attribute access to the internal Observer.
        Called when an attribute is missing on USBObserver. This lets
        USBObserver act as a proxy, exposing all methods and attributes
        of the internal Observer (e.g., start, stop, join) without redefining them.

        Args:
            name (str): Name of the attribute being accessed.

        Returns:
            The attribute value from the internal Observer.
        """
        return getattr(self._observer, name)

@singleton
class USBMonitor(PatternMatchingEventHandler):
    """
    Singleton watchdog event handler for USB marker file creation and deletion.
    Allows subscribers to register insert/remove callbacks.
    """

    def __init__(self) -> None:
        """Initialize the USB monitor."""
        # Initialize parent event handler
        super().__init__(patterns=[USB_MONITOR])

        # Lock to protect subscriber list
        self._sub_lock = RLock()

        # List of (on_insert, on_remove) callbacks
        self._subscribers = []

        # Determine initial USB state from mount point
        if path.ismount(USB_MOUNT_POINT):
            self._state = STATE_USB_PRESENT

            # Import wifi networks from file on USB
            self._import_usb_wifi_networks()
        else:
            self._state = STATE_USB_ABSENT

    def get_state(self) -> str:
        """Return current USB mount state."""
        return self._state

    def subscribe(self, on_insert, on_remove) -> None:
        """
        Register callbacks for USB insert/remove events.

        Args:
            on_insert (Callable): Method to call when USB is inserted
            on_remove (Callable): Method to call when USB is removed
        """
        with self._sub_lock:
            self._subscribers.append((on_insert, on_remove))

    def unsubscribe(self, on_insert, on_remove) -> None:
        """
        Remove previously registered callbacks.

        Args:
            on_insert (Callable): Method to call when USB is inserted
            on_remove (Callable): Method to call when USB is removed
        """
        with self._sub_lock:
            self._subscribers.remove((on_insert, on_remove))

    def on_created(self, event) -> None:
        """
        Watchdog callback: called when the USB monitor is created
        Updates state and triggers all on_insert callbacks

        Args:
            event (FileSystemEvent): called when the USB monitor file is created.
        """
        oradio_log.info("USB inserted on %s", event.src_path)

        # set state to PRESENT
        self._state = STATE_USB_PRESENT

        # Trigger insert callback for each subscriber
        with self._sub_lock:
            for on_insert, _ in self._subscribers:
                on_insert()

        # Import wifi networks from file on USB
        self._import_usb_wifi_networks()

    def on_deleted(self, event) -> None:
        """
        Watchdog callback: called when the USB monitor is removed
        Updates state and triggers all on_remove callbacks

        Args:
            event (FileSystemEvent): called when the USB monitor file is deleted.
        """
        oradio_log.info("USB removed from %s", event.src_path)

        # set state to ABSENT
        self._state = STATE_USB_ABSENT

        with self._sub_lock:
            for _, on_remove in self._subscribers:
                on_remove()

    @staticmethod
    def _validate_network(network: dict[str, object], index: int) -> str | None:
        """
        Ensure valid network credentials.

        Args:
            network (dict): network fields
            index (int): Position in input file

        Returns:
            str | None: Error message or None when valid
        """
        # Start assuming no errors
        errors = []

        # Must be a dict
        if not isinstance(network, dict):
            errors.append(f"Network #{index} is not an object")
        else:
            # Required fields
            missing = {"SSID", "PASSWORD"} - network.keys()
            if missing:
                errors.append(f"Network #{index} missing fields: {', '.join(sorted(missing))}")

            # SSID validation
            ssid = network.get("SSID")
            if isinstance(ssid, str):
                if not ssid.strip():
                    errors.append(f"Network #{index} has empty SSID")
                elif len(ssid) > 32:
                    errors.append(f"Network #{index} SSID is too long")
            else:
                errors.append(f"Network #{index} has invalid SSID")

            # PASSWORD validation (empty allowed for open networks, otherwise min 8 chars)
            pswd = network.get("PASSWORD")
            if isinstance(pswd, str):
                if 0 < len(pswd) < 8:
                    errors.append(f"Network #{index} PASSWORD is too short")
            else:
                errors.append(f"Network #{index} has invalid PASSWORD")

        # Return errors found, or None if valid
        return "; ".join(errors) if errors else None

    def _import_usb_wifi_networks(self) -> None:
        """
        Check for file with wifi credentials on USB drive
        - If found, validate and add to NetworkManager
        """
        oradio_log.info("Checking %s for wifi credentials", USB_WIFI_FILE)

        # Check if wifi credentials file exists in USB drive root
        if not path.isfile(USB_WIFI_FILE):
            oradio_log.debug("'%s' not found", USB_WIFI_FILE)
            return

        try:
            # Read and parse JSON file
            with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                # Get JSON object as a dictionary
                data = load(file)
        except (JSONDecodeError, IOError) as ex_err:
            oradio_log.error("Failed to read or parse '%s': error: %s", USB_WIFI_FILE, ex_err)
            return

        # Validate data is a list of networks
        if "networks" not in data or not isinstance(data["networks"], list):
            oradio_log.error("'networks' must be a list")
            return

        # Validate and import each network entry
        all_valid = True
        for i, network in enumerate(data["networks"], start=1):
            if err_msg := self._validate_network(network, i):
                all_valid = False
                oradio_log.error(err_msg)
            else:
                # Add wifi credentials to NetworkManager
                ssid = network["SSID"].strip()
                pswd = network["PASSWORD"]      # Spaces are allowed in passwords
                if networkmanager_add(ssid, pswd):
                    oradio_log.info("Network '%s' added to NetworkManager", ssid)
                else:
                    oradio_log.error("Failed to add '%s' to NetworkManager", ssid)

        # Remove file after successful parsing
        if all_valid:
            try:
                remove(USB_WIFI_FILE)
                oradio_log.info("'%s' removed", USB_WIFI_FILE)
            except (FileNotFoundError, PermissionError) as ex_err:
                oradio_log.error("Failed to remove '%s': %s", USB_WIFI_FILE, ex_err)
        else:
            oradio_log.warning("'%s' has errors, is not removed", USB_WIFI_FILE)

class USBService:
    """
    Service that connects the singleton USBObserver + USBMonitor,
    subscribes to USB events, and forwards state messages to a queue.
    """

    def __init__(self, queue) -> None:
        """
        Initialize the USB service.

        Args:
            queue (Queue): the queue to send messages to
        """
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

    def _usb_inserted(self) -> None:
        """Callback invoked on USB insertion."""
        self._send_message()

    def _usb_removed(self) -> None:
        """Callback invoked on USB removal."""
        self._send_message()

    def _send_message(self) -> None:
        """Send current USB state message to the registered queue."""
        message = {
            "source": MESSAGE_USB_SOURCE,
            "state": self.get_state(),
            "error": MESSAGE_NO_ERROR,
        }
        oradio_log.debug("Send USBService message: %s", message)
        safe_put(self._queue, message)

    def get_state(self) -> str:
        """Return current USB mount state."""
        return self._monitor.get_state()

    def close(self) -> None:
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

    def _check_messages(queue) -> None:
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it

        Args:
            queue (Queue): the queue to read messages from
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu(queue) -> None:    # pylint: disable=too-many-branches
        """
        Show menu with test options.

        Args:
            queue (Queue): the queue to check for
        """
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
                    # Use shell command because monitor file ownership is root
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
