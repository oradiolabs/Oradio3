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
@summary:       USB mount monitoring service using watchdog.
    Detects USB insertion and removal by monitoring a marker file on an
    auto-mounted USB drive. When a drive labelled ORADIO is mounted by the OS,
    a marker file is created; its creation and deletion drive state changes.
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

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from wifi_service import networkmanager_add
from messaging import (
    Errors,
    Commands,
    ErrorMessage,
    CommandMessage,
    USB_SOURCE,
    USB_PRESENT,
    USB_ABSENT,
    USB_ERROR_FILE,
    USB_ERROR_SERVICE,
)

##### GLOBAL constants ####################################
# Filesystem path where the ORADIO USB drive is auto-mounted by the OS
from constants import USB_MOUNT_POINT

##### LOCAL constants #####################################
# Directory watched by the observer for filesystem events
USB_STATEPATH = "/run"

# Marker file managed by udev to signal USB drive presence:
#   created  → ORADIO USB drive has been mounted
#   deleted  → ORADIO USB drive has been unmounted
USB_STATEFILE = "/run/usb_present"

# Expected location of the WiFi credentials file on the USB drive root
USB_WIFI_FILE = path.join(USB_MOUNT_POINT, "Wifi_invoer.json")

@singleton
class USBObserver(FileSystemEventHandler):
    """
    Singleton watchdog handler for USB marker file creation and deletion.

    Monitors USB_STATEFILE to detect USB drive insertion and removal.
    On state changes, publishes USB_PRESENT or USB_ABSENT via the
    command message bus. On initialisation it also checks whether the drive is
    already mounted, and if so attempts to import WiFi credentials.

    The @singleton decorator ensures only one instance exists per process,
    preventing duplicate observer registrations and duplicate WiFi imports.
    """
    def __init__(self) -> None:
        """Initialise the parent handler and set USB state based on whether
        the drive is currently mounted.

        Checks the mount point at startup to handle the case where the USB
        drive was already inserted before this service started. Publishes the
        correct initial USB_PRESENT or USB_ABSENT state, and triggers a
        WiFi credential import if the drive is already mounted.

        Subsequent insertions are handled by on_created, which also triggers
        a WiFi credential import each time the marker file is recreated.
        """
        super().__init__()
        if path.ismount(USB_MOUNT_POINT):
            Commands.publish(CommandMessage(USB_SOURCE, USB_PRESENT))
            # Drive is already mounted: attempt to import any WiFi credentials
            self._import_usb_wifi_networks()
        else:
            Commands.publish(CommandMessage(USB_SOURCE, USB_ABSENT))

##### Helpers #############################################

    @staticmethod
    def _validate_network(network: dict[str, object], index: int) -> str | None:
        """
        Validate a single network entry from the WiFi credentials file.

        Checks that the entry is a dict, contains the required SSID and
        PASSWORD fields, and that those values meet length and type
        constraints.

        All validation errors are accumulated before returning so the caller
        receives a complete picture of what is wrong with the entry in one pass.

        Args:
            network: Parsed network object from the JSON networks list.
            index:   1-based position of this entry in the file, included in
                     error messages to help the user locate the offending entry.

        Returns:
            A semicolon-separated string of error descriptions if any checks
            fail, or None if the entry is valid.
        """
        errors = []

        # The top-level entry must be a JSON object (Python dict)
        if not isinstance(network, dict):
            errors.append(f"Network #{index} is not an object")
        else:
            # Identify and report any missing required keys before checking values
            missing = {"SSID", "PASSWORD"} - network.keys()
            if missing:
                errors.append(f"Network #{index} missing fields: {', '.join(sorted(missing))}")

            # SSID validation
            ssid = network.get("SSID")
            if isinstance(ssid, str):
                if not ssid.strip():
                    # A whitespace-only SSID is not a usable network name
                    errors.append(f"Network #{index} has empty SSID")
                elif len(ssid) > 32:
                    # IEEE 802.11 caps SSID length at 32 bytes
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

        # Return all errors joined as one string, or None to signal success
        return "; ".join(errors) if errors else None

    @staticmethod
    def _import_usb_wifi_networks() -> None:
        """
        Import WiFi credentials from Wifi_invoer.json on the USB drive.

        Reads and validates the JSON credentials file. For each valid network
        entry the credentials are registered with NetworkManager. If every
        entry passes validation, the source file is deleted from the USB drive
        to prevent re-import on the next insertion and to remove potentially
        sensitive credentials from the removable drive.

        The file is left in place when validation errors are found, allowing
        the user to correct the data and re-insert the drive.

        Expected JSON structure::
            {
                "networks": [
                    {"SSID": "MyNetwork", "PASSWORD": "secret123"},
                    {"SSID": "OpenNet",   "PASSWORD": ""}
                ]
            }

        Publishes USB_ERROR_FILE on any read, parse, or validation error.
        """
        oradio_log.info("Checking %s for wifi credentials", USB_WIFI_FILE)

        # Nothing to do if the credentials file is absent — this is normal
        if not path.isfile(USB_WIFI_FILE):
            oradio_log.debug("'%s' not found", USB_WIFI_FILE)
            return

        # Read and parse the JSON credentials file
        try:
            with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                data = load(file)
        except (JSONDecodeError, IOError) as ex_err:
            # Covers malformed JSON and filesystem errors (permissions, I/O)
            oradio_log.error("Failed to read or parse '%s': error: %s", USB_WIFI_FILE, ex_err)
            Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))
            return

        # The root object must contain a "networks" key whose value is a list
        if "networks" not in data or not isinstance(data["networks"], list):
            oradio_log.error("'networks' must be a list")
            Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))
            return

        # Validate every entry first; track whether all pass so we know if it
        # is safe to delete the file after processing
        all_valid = True

        for i, network in enumerate(data["networks"], start=1):
            if err_msg := USBObserver._validate_network(network, i):
                # Entry failed structural validation; continue to surface all
                # errors in this pass rather than stopping at the first failure
                all_valid = False
                oradio_log.error(err_msg)
                Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))
            else:
                # Strip surrounding whitespace from SSID; passwords are left
                # untouched because internal spaces are valid in WPA keys
                ssid = network["SSID"].strip()
                pswd = network["PASSWORD"]

                # Attempt to register the credentials with NetworkManager
                if networkmanager_add(ssid, pswd):
                    oradio_log.info("Network '%s' added to NetworkManager", ssid)
                else:
                    all_valid = False
                    oradio_log.error("Failed to add '%s' to NetworkManager", ssid)
                    Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))

        if all_valid:
            # Remove the credentials file to prevent re-import and to avoid
            # leaving sensitive data on the removable drive
            try:
                remove(USB_WIFI_FILE)
                oradio_log.info("'%s' removed", USB_WIFI_FILE)
            except (FileNotFoundError, PermissionError) as ex_err:
                oradio_log.error("Failed to remove '%s': %s", USB_WIFI_FILE, ex_err)
                Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))
        else:
            # Leave the file in place so the user can correct the errors
            oradio_log.error("'%s' has errors, is not removed", USB_WIFI_FILE)
            Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_FILE))

##### Public API ##########################################

    def on_created(self, event) -> None:
        """
        Handle watchdog callback when USB_STATEFILE is created.

        The OS creates this marker file when the ORADIO USB drive is mounted.
        Publishes USB_PRESENT to signal that the USB drive is available, then
        attempts to import any WiFi credentials found on the drive.

        Args:
            event: Watchdog FileCreatedEvent describing the created file.
        """
        # Ignore directory events and any files other than the specific marker file
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB inserted")
            Commands.publish(CommandMessage(USB_SOURCE, USB_PRESENT))
            self._import_usb_wifi_networks()

    def on_deleted(self, event) -> None:
        """
        Handle watchdog callback when USB_STATEFILE is deleted.

        The OS deletes this marker file when the ORADIO USB drive is unmounted.
        Publishes USB_ABSENT to signal that the USB drive is no longer
        available.

        Args:
            event: Watchdog FileDeletedEvent describing the deleted file.
        """
        # Ignore directory events and any files other than the specific marker file
        if not event.is_directory and event.src_path == USB_STATEFILE:
            oradio_log.debug("USB removed")
            Commands.publish(CommandMessage(USB_SOURCE, USB_ABSENT))

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
        starts the observer in a background thread. Logs an error and publishes
        USB_ERROR_SERVICE if the observer thread fails to start.
        """
        self.observer = Observer()

        # Schedule the singleton handler on the directory that contains the
        # USB marker file. recursive=False limits events to the top-level
        # directory, avoiding unnecessary inotify overhead from subdirectories.
        self.observer.schedule(USBObserver(), path=USB_STATEPATH, recursive=False)

        try:
            self.observer.start()
            oradio_log.info("USB observer started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("USB observer failed to start: %s", ex_err)
            Errors.publish(ErrorMessage(USB_SOURCE, USB_ERROR_SERVICE))

    def get_state(self) -> str:
        """
        Return the current USB drive state by inspecting the mount point.

        This is a direct filesystem check and reflects the real-time mount
        status, independent of any cached or published state.

        Returns:
            USB_PRESENT if the ORADIO USB drive is currently mounted,
            USB_ABSENT otherwise.
        """
        if path.ismount(USB_MOUNT_POINT):
            return USB_PRESENT
        return USB_ABSENT

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from constants import RED, YELLOW, NC           # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports
    from utilities import run_shell_script, input_prompt

    # Most stand-alone entry points share this pattern; pylint would flag it as duplicate code across modules.
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """
        Run an interactive self-test menu for the USB monitoring service.

        Starts the USB monitor, then loops until the user selects quit (0).
        Options allow querying the current mount state and simulating insert
        or remove events by creating or deleting the marker file via sudo,
        which is required because the marker file is owned by root.
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

        # Instantiate the service; the watchdog observer thread starts here
        monitor = USBService()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print(f"\nUSB state: {monitor.get_state()}\n")
                case 2:
                    # The marker file is owned by root, so sudo is required
                    print("\nSimulate 'USB inserted' event...\n")
                    cmd = f"sudo touch {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <{cmd}> to create monitor, error: {response}")
                case 3:
                    # The marker file is owned by root, so sudo is required
                    print("\nSimulate 'USB removed' event...\n")
                    cmd = f"sudo rm -f {USB_STATEFILE}"
                    result, response = run_shell_script(cmd)
                    if not result:
                        print(f"{RED}Error during <{cmd}> to remove monitor, error: {response}")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to command and error topics so published messages are printed to console
    cmd_handler = DebugMessageHandler(Commands.subscribe())
    err_handler = DebugMessageHandler(Errors.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Commands.unsubscribe(cmd_handler.get_queue())
    Errors.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    cmd_handler.stop()
    err_handler.stop()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
