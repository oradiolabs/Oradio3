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
from watchdog.events import FileSystemEventHandler

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from wifi_service import networkmanager_add
from oradio_utils import safe_put, run_shell_script
from messaging import CommandMessage, publish_command, ErrorMessage, publish_error

##### GLOBAL constants ####################
from oradio_const import (
    USB_MOUNT_POINT,
    STATE_USB_PRESENT,
    STATE_USB_ABSENT,
    MESSAGE_USB_SOURCE,
    MESSAGE_USB_ERROR_FILE,
    MESSAGE_USB_ERROR_SERVICE,
)

##### LOCAL constants ####################
USB_STATEPATH = "/run"              # Path to monitor if USB is mounted or not
USB_STATEFILE = "/run/usb_present"  # File to monitor if USB is mounted or not
USB_WIFI_FILE = path.join(USB_MOUNT_POINT, "Wifi_invoer.json")  # USB file with wifi credentials

@singleton
class USBObserver(FileSystemEventHandler):
    """
    Singleton watchdog event handler for USB marker file creation and deletion.
    Publishes USB present/absent state changes and file errors, if any
    """
    def __init__(self) -> None:
       # Determine initial USB state from mount point
        if path.ismount(USB_MOUNT_POINT):
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_PRESENT))
            # Import wifi networks from file on USB
            self._import_usb_wifi_networks()
        else:
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_ABSENT))

    def on_created(self, event) -> None:
        """
        Watchdog callback: called when USB_STATEFILE is created
        Updates state and publishes USB present state
        Args:
            event: called when USB_STATEFILE is created
        """
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB inserted")
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_PRESENT))

    def on_deleted(self, event) -> None:
        """
        Watchdog callback: called when USB_STATEFILE is deleted
        Updates state and publishes USB absent state
        Args:
            event: called when USB_STATEFILE is deleted
        """
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB removed")
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_ABSENT))

    @staticmethod
    def _validate_network(network: dict[str, object], index: int) -> str | None:
        """
        Ensure valid network credentials.
        Args:
            network: network fields
            index: Position in input file
        Returns:
            Error message or None when valid
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
        If found, validate and add to NetworkManager
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
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            return

        # Validate data is a list of networks
        if "networks" not in data or not isinstance(data["networks"], list):
            oradio_log.error("'networks' must be a list")
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            return

        # Validate and import each network entry
        all_valid = True
        for i, network in enumerate(data["networks"], start=1):
            if err_msg := self._validate_network(network, i):
                all_valid = False
                oradio_log.error(err_msg)
                publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            else:
                # Add wifi credentials to NetworkManager
                ssid = network["SSID"].strip()
                pswd = network["PASSWORD"]      # Spaces are allowed in passwords
                if networkmanager_add(ssid, pswd):
                    oradio_log.info("Network '%s' added to NetworkManager", ssid)
                else:
                    oradio_log.error("Failed to add '%s' to NetworkManager", ssid)
                    publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))

        # Remove file after successful parsing
        if all_valid:
            try:
                remove(USB_WIFI_FILE)
                oradio_log.info("'%s' removed", USB_WIFI_FILE)
            except (FileNotFoundError, PermissionError) as ex_err:
                oradio_log.error("Failed to remove '%s': %s", USB_WIFI_FILE, ex_err)
                publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
        else:
            oradio_log.error("'%s' has errors, is not removed", USB_WIFI_FILE)
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))

class USBService():
    def __init__(self):
        # Initialize and start observer tracking USB presence
        self.observer = Observer()
        self.observer.schedule(USBObserver(), path=USB_STATEPATH, recursive=False)
        self.observer.start()

        # Verify observer is active
        if not self.observer.is_alive():
            oradio_log.error("USB observer failed to start: no USB present/absent info available")
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_SERVICE))

        oradio_log.info("USB observer started")

    def get_state(self) -> str:
        """ Return USB state from mount point """
        if path.ismount(USB_MOUNT_POINT):
            return STATE_USB_PRESENT
        else:
            return STATE_USB_ABSENT

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from threading import Thread
    from multiprocessing import Queue
    from messaging import Topic, subscribe_commands, subscribe_errors, safe_get     # pylint: disable=ungrouped-imports
    from oradio_const import YELLOW, GREEN, NC

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def topic_handler(topic: Topic, queue: Queue) -> None:
        """Receive and print messages from a subscribed queue."""
        while True:
            message = safe_get(queue)
            print(f"[{topic}] - Message received: {message!r}")

    def interactive_menu() -> None:
        """ Show menu with test options """

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Get USB state\n"
            " 2-Simulate USB inserted\n"
            " 3-Simulate USB removed\n"
            "select: "
        )

        # Start USB monitor
        monitor = USBService()

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
                    print(f"\nUSB state: {monitor.get_state()}\n")
                case 2:
                    print("\nSimulate 'USB inserted' event...\n")
                    # Use shell command because monitor file ownership is root
                    cmd = f"sudo touch {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to create monitor, error: %s", cmd, response)
                case 3:
                    print("\nSimulate 'USB removed' event...\n")
                    # Need to use subprocess because monitor is owned by root
                    cmd = f"sudo rm -f {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to remove monitor, error: %s", cmd, response)
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Start command messages listener
    cmd_queue = subscribe_commands()
    Thread(target=topic_handler, args=(Topic.COMMAND, cmd_queue), daemon=True).start()

    # Start error messages listener
    err_queue = subscribe_errors()
    Thread(target=topic_handler, args=(Topic.ERROR, err_queue), daemon=True).start()

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
