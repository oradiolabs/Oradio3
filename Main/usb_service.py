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
    Key features:
        - Singleton watchdog observer (only one instance per process)
        - USB insert/remove state published via the messaging bus
        - Optional import of WiFi credentials from a JSON file on the USB drive
    Requirements:
        - OS auto-mounts USB drives with label 'ORADIO'
        - watchdog package (https://pypi.org/project/watchdog/)
"""
from os import path, remove
from json import load, JSONDecodeError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from wifi_service import networkmanager_add
from oradio_utils import run_shell_script
from messaging import CommandMessage, publish_command, ErrorMessage, publish_error

##### GLOBAL constants ####################
from oradio_const import (
    USB_MOUNT_POINT,           # Filesystem path where the ORADIO USB drive is auto-mounted
    STATE_USB_PRESENT,         # State token indicating USB drive is connected
    STATE_USB_ABSENT,          # State token indicating USB drive is not connected
    MESSAGE_USB_SOURCE,        # Message source identifier for USB-related messages
    MESSAGE_USB_ERROR_FILE,    # Error code for file-related USB errors (e.g. bad WiFi JSON)
    MESSAGE_USB_ERROR_SERVICE, # Error code for service-level USB errors (e.g. observer failed)
)

##### LOCAL constants ####################
# Directory watched by the observer for the USB marker file
USB_STATEPATH = "/run"
# Marker file created by the OS when the ORADIO USB drive is mounted,
# and deleted when it is removed. Watchdog events on this file drive state changes.
USB_STATEFILE = "/run/usb_present"
# Expected location of the WiFi credentials file on the USB drive root
USB_WIFI_FILE = path.join(USB_MOUNT_POINT, "Wifi_invoer.json")

@singleton
class USBObserver(FileSystemEventHandler):
    """
    Singleton watchdog handler for USB marker file creation and deletion.
 
    Monitors USB_STATEFILE to detect USB drive insertion and removal.
    On state changes, publishes STATE_USB_PRESENT or STATE_USB_ABSENT
    via the command message bus. On initialisation, also attempts to import
    WiFi credentials from the USB drive if it is already mounted.
 
    The @singleton decorator ensures only one instance exists per process,
    preventing duplicate observer registrations.
    """
    def __init__(self) -> None:
        """
        Initialise USB state based on whether the drive is currently mounted.
 
        Publishes the correct initial state and, if the USB is already present,
        attempts to import any WiFi credentials found on the drive.
        """
        # Check mount point at startup to set the initial state correctly,
        # covering the case where the USB was already inserted before this
        # service started.
        if path.ismount(USB_MOUNT_POINT):
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_PRESENT))
            # USB already mounted: try to import any WiFi credentials on the drive
            self._import_usb_wifi_networks()
        else:
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_ABSENT))

    def on_created(self, event) -> None:
        """
        Handle watchdog callback when USB_STATEFILE is created.
 
        The OS creates this marker file when the ORADIO USB drive is mounted.
        Publishes STATE_USB_PRESENT to signal that the USB drive is available.
 
        Args:
            event: Watchdog FileCreatedEvent describing the created file.
        """
        # Ignore directory events and any files other than the specific marker file
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB inserted")
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_PRESENT))

    def on_deleted(self, event) -> None:
        """
        Handle watchdog callback when USB_STATEFILE is deleted.
 
        The OS deletes this marker file when the ORADIO USB drive is unmounted.
        Publishes STATE_USB_ABSENT to signal that the USB drive is no longer
        available.
 
        Args:
            event: Watchdog FileDeletedEvent describing the deleted file.
        """
        # Ignore directory events and any files other than the specific marker file
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB removed")
            publish_command(CommandMessage(MESSAGE_USB_SOURCE, STATE_USB_ABSENT))

    @staticmethod
    def _validate_network(network: dict[str, object], index: int) -> str | None:
        """
        Validate a single network entry from the WiFi credentials file.
 
        Checks that the entry is a dict, contains the required SSID and
        PASSWORD fields, and that those values meet length/type constraints.
 
        Args:
            network: Parsed network object from the JSON networks list.
            index:   1-based position of this entry in the file, used in error
                     messages to help the user locate the offending entry.
 
        Returns:
            A semicolon-separated string of error descriptions if any validation
            checks fail, or None if the entry is valid.
        """
        # Accumulate all validation errors before returning, so the caller
        # receives a complete picture of what is wrong with this entry.
        errors = []

        # The top-level entry must be a JSON object (Python dict)
        if not isinstance(network, dict):
            errors.append(f"Network #{index} is not an object")
        else:
            # Check that both required keys are present before accessing them
            missing = {"SSID", "PASSWORD"} - network.keys()
            if missing:
                errors.append(f"Network #{index} missing fields: {', '.join(sorted(missing))}")

            # SSID validation
            ssid = network.get("SSID")
            if isinstance(ssid, str):
                if not ssid.strip():
                    # An SSID of only whitespace is not usable
                    errors.append(f"Network #{index} has empty SSID")
                elif len(ssid) > 32:
                    # IEEE 802.11 limits SSID to 32 bytes
                    errors.append(f"Network #{index} SSID is too long")
            else:
                errors.append(f"Network #{index} has invalid SSID")

            # PASSWORD validation
            # An empty password is allowed (open/unprotected network).
            # Any non-empty WPA password must be at least 8 characters.
            pswd = network.get("PASSWORD")
            if isinstance(pswd, str):
                if 0 < len(pswd) < 8:
                    errors.append(f"Network #{index} PASSWORD is too short")
            else:
                errors.append(f"Network #{index} has invalid PASSWORD")

        # Return all errors as one string, or None to indicate success
        return "; ".join(errors) if errors else None

    def _import_usb_wifi_networks(self) -> None:
        """
        Import WiFi credentials from Wifi_invoer.json on the USB drive.
 
        Reads and validates the JSON credentials file. For each valid network
        entry the credentials are registered with NetworkManager. If all entries
        are valid the source file is deleted from the USB drive so it is not
        re-imported on the next insertion.
 
        The expected JSON structure is::
 
            {
                "networks": [
                    {"SSID": "MyNetwork", "PASSWORD": "secret123"},
                    {"SSID": "OpenNet",   "PASSWORD": ""}
                ]
            }
 
        On any read, parse, or validation error an MESSAGE_USB_ERROR_FILE
        error message is published. The file is *not* deleted when errors are
        found, allowing the user to correct and re-insert the drive.
        """
        oradio_log.info("Checking %s for wifi credentials", USB_WIFI_FILE)

        # Nothing to do if the credentials file is absent — this is normal
        if not path.isfile(USB_WIFI_FILE):
            oradio_log.debug("'%s' not found", USB_WIFI_FILE)
            return

        # Read and parse the JSON file
        try:
            with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                data = load(file)
        except (JSONDecodeError, IOError) as ex_err:
            # Covers malformed JSON and filesystem errors (permissions, I/O)
            oradio_log.error("Failed to read or parse '%s': error: %s", USB_WIFI_FILE, ex_err)
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            return

        # Validate top-level structure
        # The root object must contain a "networks" key whose value is a list
        if "networks" not in data or not isinstance(data["networks"], list):
            oradio_log.error("'networks' must be a list")
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            return

        # Validate and register each network entry
        # Track overall validity so we know whether it is safe to delete the file
        all_valid = True

        for i, network in enumerate(data["networks"], start=1):
            if err_msg := self._validate_network(network, i):
                # Entry failed validation; log and report, but continue to
                # surface all errors in this pass
                all_valid = False
                oradio_log.error(err_msg)
                publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
            else:
                # Strip surrounding whitespace from SSID (passwords allow internal spaces)
                ssid = network["SSID"].strip()
                pswd = network["PASSWORD"]      # Spaces are allowed in passwords

                # Attempt to register the credentials with NetworkManager
                if networkmanager_add(ssid, pswd):
                    oradio_log.info("Network '%s' added to NetworkManager", ssid)
                else:
                    oradio_log.error("Failed to add '%s' to NetworkManager", ssid)
                    publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))

        # Clean up the credentials file if everything was valid
        # Deleting the file prevents re-import on the next USB insertion and
        # removes potentially sensitive credentials from the removable drive.
        if all_valid:
            try:
                remove(USB_WIFI_FILE)
                oradio_log.info("'%s' removed", USB_WIFI_FILE)
            except (FileNotFoundError, PermissionError) as ex_err:
                oradio_log.error("Failed to remove '%s': %s", USB_WIFI_FILE, ex_err)
                publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))
        else:
            # Leave the file in place so the user can correct the errors
            oradio_log.error("'%s' has errors, is not removed", USB_WIFI_FILE)
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_FILE))

class USBService:
    """
    High-level USB monitoring service.
 
    Creates and starts a watchdog Observer that tracks the USB marker file
    via the singleton USBObserver handler. Provides a convenience method
    to query the current USB state by inspecting the mount point directly.
    """
    def __init__(self):
        """
        Initialise and start the watchdog observer for USB state monitoring.
 
        Schedules USBObserver on USB_STATEPATH (non-recursive) and
        starts the observer thread. Logs an error and publishes
        MESSAGE_USB_ERROR_SERVICE if the observer thread fails to start.
        """
        # Create the watchdog observer that will run in a background thread
        self.observer = Observer()

        # Register the singleton event handler on the directory containing the
        # USB marker file. recursive=False limits events to the top-level
        # directory only, avoiding unnecessary overhead.
        self.observer.schedule(USBObserver(), path=USB_STATEPATH, recursive=False)
        self.observer.start()

        # Confirm the observer thread actually came up; it can fail silently
        # (e.g. inotify limit reached) without raising an exception
        if not self.observer.is_alive():
            oradio_log.error("USB observer failed to start: no USB present/absent info available")
            publish_error(ErrorMessage(MESSAGE_USB_SOURCE, MESSAGE_USB_ERROR_SERVICE))

        oradio_log.info("USB observer started")

    def get_state(self) -> str:
        """
        Return the current USB drive state by inspecting the mount point.
 
        This is a direct filesystem check and reflects the real-time mount
        status, independent of any cached or published state.
 
        Returns:
            STATE_USB_PRESENT if the ORADIO USB drive is currently mounted,
            STATE_USB_ABSENT otherwise.
        """
        if path.ismount(USB_MOUNT_POINT):
            return STATE_USB_PRESENT
        return STATE_USB_ABSENT

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from threading import Thread                                                # pylint: disable=wrong-import-position
    from multiprocessing import Queue                                           # pylint: disable=wrong-import-position
    from messaging import Topic, subscribe_commands, subscribe_errors, safe_get # pylint: disable=ungrouped-imports,wrong-import-position
    from oradio_const import RED, YELLOW, NC                                    # pylint: disable=ungrouped-imports,wrong-import-position

    # Most stand-alone entry points share this pattern; pylint would flag it as duplicate code across modules.
    # pylint: disable=duplicate-code

    def topic_handler(topic: Topic, queue: Queue) -> None:
        """
        Print messages received on a subscribed message queue.
 
        Runs in a daemon thread; blocks on ``safe_get`` until a message arrives,
        then prints it and loops.
 
        Args:
            topic: The topic this handler is subscribed to (used for labelling).
            queue: The queue from which messages are consumed.
        """
        while True:
            message = safe_get(queue)
            print(f"[{topic}] - Message received: {message!r}")

    def interactive_menu() -> None:
       """
       Present an interactive menu for manual USB service testing.
 
        Starts the USB monitor and loops until the user selects quit (0).
        Options allow querying the current state and simulating insert/remove
        events by creating or deleting the marker file via sudo.
        """

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Get USB state\n"
            " 2-Simulate USB inserted\n"
            " 3-Simulate USB removed\n"
            "select: "
        )

        # Start the USB monitor (observer thread begins here)
        monitor = USBService()

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                # Non-integer input; fall through to the default case
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    print(f"\nUSB state: {monitor.get_state()}\n")
                case 2:
                    # The marker file is owned by root, so sudo is required
                    print("\nSimulate 'USB inserted' event...\n")
                    cmd = f"sudo touch {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to create monitor, error: %s", cmd, response)
                case 3:
                    # The marker file is owned by root, so sudo is required
                    print("\nSimulate 'USB removed' event...\n")
                    cmd = f"sudo rm -f {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <%s> to remove monitor, error: %s", cmd, response)
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Subscribe to command and error topics before starting the service so no messages are missed during initialisation
    cmd_queue = subscribe_commands()
    Thread(target=topic_handler, args=(Topic.COMMAND, cmd_queue), daemon=True).start()
    # Start error messages listener
    err_queue = subscribe_errors()
    Thread(target=topic_handler, args=(Topic.ERROR, err_queue), daemon=True).start()

    # Launch the interactive test menu (blocks until the user quits)
    interactive_menu()

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
