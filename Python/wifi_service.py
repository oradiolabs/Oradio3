#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on December 23, 2024
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       4
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for wifi connectivity services
    :Documentation
        https://networkmanager.dev/
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
        https://blogs.gnome.org/dcbw/2016/05/16/networkmanager-and-wifi-scans/
    :Notes
        Not used:
            NM can do a connectivity check. See https://wiki.archlinux.org/title/NetworkManager section 4.4
        Not supported:
            Connecting to captive portal.
            Connecting to VPN
        TODO:
            Use NetworkManager setting up a wifi hotspot that sets up a local private net, with DHCP and IP forwarding
            Use: nmcli dev wifi hotspot ifname wlp4s0 ssid test password "test1234"
"""
from re import search
from os import path, remove
from threading import Thread
from json import load, JSONDecodeError
#Review Onno: waarom multiprocessing Queue, niet threading Queue? Multiprocessing is 'duurder'
#Review Onno: waarom multiprocessing Process, niet threading Thread? Process is 'duurder'
from multiprocessing import Process, Queue, Lock
from subprocess import CalledProcessError
import nmcli
import nmcli._exception as nmcli_exc
from dbus import SystemBus, Interface
from dbus.mainloop.glib import DBusGMainLoop
from dbus.exceptions import DBusException
from gi.repository import GLib

##### oradio modules ####################
from singleton import singleton
from oradio_utils import run_shell_script, safe_put
from usb_service import USBService
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    USB_MOUNT_POINT,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    STATE_USB_PRESENT,
    MESSAGE_WIFI_SOURCE,
    STATE_WIFI_IDLE,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_WIFI_FILE_ERROR,
    MESSAGE_WIFI_FAIL_CONFIG,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
WIFI_MONITOR    = "Wifi_invoer.json"                    # Name of file used to monitor for wifi credentials
USB_WIFI_FILE   = USB_MOUNT_POINT + "/Wifi_invoer.json" # USB file with wifi credentials
DEBOUNCE_TIME   = 2                                     # Wait time in seconds before accepting disconnect
NM_DISCONNECTED = 30
NM_CONNECTED    = 100
NM_FAILED       = 120
# Timeout for thread to respond (seconds)
THREAD_TIMEOUT = 3

# Dynamic tuple generation (less maintenance-heavy if exceptions change)
nmcli_exceptions = tuple(
    exc for exc in vars(nmcli_exc).values()
    if isinstance(exc, type) and issubclass(exc, Exception)
)

# Global singleton variables
_saved_network = {"network": ""}    # Track last connected wifi network
_saved_lock = Lock()                # Thread-safe read/write _saved_network

def set_saved_network(network: str) -> None:
    """
    Set the last active wifi network in a thread-safe manner.

    Args:
        network (str): The SSID of the network to save.
    """
    with _saved_lock:
        _saved_network["network"] = str(network) if network else ""

def get_saved_network() -> str:
    """
    Get the last active wifi network in a thread-safe manner.

    Returns:
        str: The SSID of the last saved network.
    """
    with _saved_lock:
        return _saved_network["network"]

# Global process-safe lock for USB handling
_usb_wifi_lock = Lock()

def validate_network(network: dict, index: int) -> bool:
    """
    Ensure valid network credentials.

    Args:
        network: network fields
        index: Position in input file

    Returns:
        bool: True if valid, False otherwise
    """
    # Must be a dict
    if not isinstance(network, dict):
        oradio_log.error("Network #%d is not an object", index)
        return False

    # Required fields
    missing = {"SSID", "PASSWORD"} - network.keys()
    if missing:
        oradio_log.error("Network #%d missing fields", index, missing)
        return False

    # SSID validation
    ssid = network.get("SSID")
    if not isinstance(ssid, str) or not ssid.strip() or len(ssid) > 32:
        if not isinstance(ssid, str) or not ssid.strip():
            oradio_log.error("Network #%d has invalid SSID", index)
        else:
            oradio_log.error("Network #%d SSID is too long", index)
        return False

    # PASSWORD validation (empty allowed)
    password = network.get("PASSWORD")
    if not isinstance(password, str) or (0 < len(password) < 8):
        if not isinstance(password, str):
            oradio_log.error("Network #%d has invalid PASSWORD", index)
        else:
            oradio_log.error("Network #%d PASSWORD is too short", index)
        return False

    # No errors found
    return True

@singleton
class WifiEventListener:
    """
    Singleton class to listen to wifi state changes via NetworkManager D-Bus signals.
    - Connects to the system D-Bus, finds the wifi device managed by NetworkManager, and listens
      for the 'StateChanged' signal on the wireless device interface to track wifi connection state changes
    - Runs a GLib main loop in a background thread to handle asynchronous signals without blocking the main application
    """

    def __init__(self):
        """
        Initialize the listener by setting up the D-Bus main loop integration,
        connecting to the system bus, finding the wifi device, and subscribing
        to the 'StateChanged' signal
        """
        # Initialize
        self._loop = None
        self._wifi_path = None

        # List of subscriber queues to send wifi state messages
        self._subscribers = []

        try:
            # Setup GLib main loop for dbus-python signal handling
            DBusGMainLoop(set_as_default=True)

            # Connect to system D-Bus
            self.bus = SystemBus()

            # Access NetworkManager object
            nm_object = self.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            nm_iface = Interface(nm_object, "org.freedesktop.NetworkManager")

            # Find the wifi device (DeviceType == 2)
            for device in nm_iface.GetDevices():
                dev = self.bus.get_object("org.freedesktop.NetworkManager", device)
                dev_props = Interface(dev, "org.freedesktop.DBus.Properties")
                dev_type = dev_props.Get("org.freedesktop.NetworkManager.Device", "DeviceType")
                if dev_type == 2:
                    self._wifi_path = device
                    break

        except DBusException as ex_err:
            oradio_log.error("Failed to connect to NetworkManager D-Bus: %s", ex_err.get_dbus_message())
            return
        except OSError as ex_err:
            oradio_log.error("D-Bus connection error: %s", ex_err)
            return

        if not self._wifi_path:
            oradio_log.error("No wifi device found")
            return

        # Subscribe to 'StateChanged' signal on the wifi device interface
        self.bus.add_signal_receiver(
            self._wifi_state_changed,
            dbus_interface = "org.freedesktop.NetworkManager.Device",
            signal_name = "StateChanged",
            path = self._wifi_path
        )

        # Create GLib main loop and start the listener thread
        self._loop = GLib.MainLoop()
        Thread(target=self._loop.run, daemon=True).start()

    def _wifi_state_changed(self, new_state, _old_state, _reason) -> None:
        """
        Callback for wifi state changes. Notifies subscribers.

        Args:
            new_state (int): New state of the wifi device.
            _old_state (int): The previous device state (unused, underscore avoids pylint warning)
            _reason (int): Reason for state change (unused, underscore avoids pylint warning)
        """

        # Only act on final meaningful states
        if new_state not in (NM_DISCONNECTED, NM_CONNECTED, NM_FAILED):
            return

        # Get callback state, default to idle
        state_map = {
            NM_DISCONNECTED: STATE_WIFI_IDLE,
            NM_CONNECTED: STATE_WIFI_CONNECTED,
            NM_FAILED: STATE_WIFI_IDLE
        }
        state = state_map.get(new_state, STATE_WIFI_IDLE)

        # Get callback error, default to no error
        error_map = {
            NM_DISCONNECTED: MESSAGE_NO_ERROR,
            NM_CONNECTED: MESSAGE_NO_ERROR,
            NM_FAILED: MESSAGE_WIFI_FAIL_CONNECT
        }
        error = error_map.get(new_state, MESSAGE_NO_ERROR)

        # Check for Access Point
        if new_state == NM_CONNECTED:
            active = get_wifi_connection()
            if active == ACCESS_POINT_SSID:
                state = STATE_WIFI_ACCESS_POINT

        # Prepare callback message
        message = {"source": MESSAGE_WIFI_SOURCE, "state": state, "error": error}

        # Send message to queue of each subscriber
        oradio_log.debug("Send wifi service message: %s", message)
        for queue in self._subscribers:
            safe_put(queue, message)

    def subscribe(self, queue):
        """
        Subscribe a queue to receive wifi state messages.

        Args:
            queue (Queue): The queue object to receive messages.
        """
        self._subscribers.append(queue)

    def unsubscribe(self, queue):
        """
        Remove a subscriber queue.

        Args:
            queue (Queue): The queue object to remove.
        """
        try:
            self._subscribers.remove(queue)
        except ValueError:
            oradio_log.debug("Queue already unsubscribed")

class WifiService():
    """
    Handles wifi state management and connectivity:
    - Listens to USB messages for wifi credentials and attempts connection
    - Tracks states: connected with internet, connected without internet, disconnected, acting as access point
    - Sends messages on wifi state changes
    """
    def __init__(self, queue):
        """
        Initialize wifi service and its dependencies
        Start background processes/threads for USB and wifi monitoring
        Send initial wifi state message

        Args:
            queue (Queue): Queue for sending wifi state messages
        """
        self._queue = queue
        self._usb_q = Queue()

#REVIEW Onno: Waarom Process ('zwaarder') en niet Thread ('lichter') ?
        # Start a separate process to monitor USB messages (e.g. wifi credentials)
        self._usb_listener = Process(target=self._check_usb_messages, args=(self._usb_q,))
        self._usb_listener.start()

        # USBService instance to send USB state updates to usb queue
        self._usb_service = USBService(self._usb_q)

        # Start listening to NetworkManager wifi state changes
        self.nm_listener = WifiEventListener()

        # Subscribe this service's queue to receive wifi state updates
        self.nm_listener.subscribe(self._queue)

        # Send initial wifi state and no-error message
        self._send_message(MESSAGE_NO_ERROR)

    def _check_usb_messages(self, queue) -> None:
        """
        Background process to monitor USB messages from the queue
        On USB present state, check for wifi credentials on USB drive

        Args:
            queue (Queue): Queue to receive USB messages
        """
        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            oradio_log.debug("USB message received: '%s'", message)

            # If USB is present check if USB has file with wifi credentials
            if message.get("state", "Unknown") == STATE_USB_PRESENT:
                self._handle_usb_wifi_invoer()


    def _handle_usb_wifi_invoer(self) -> None:
        """
        Check for wifi credentials on USB drive
        - Lock to ensure 1 process is running this method
        - If found, validate and add to NetworkManager
        """
        if _usb_wifi_lock.acquire(block=False):  # Try to acquire without blocking
            try:
                oradio_log.info("Checking %s for wifi credentials", USB_WIFI_FILE)

                # Check if wifi credentials file exists in USB drive root
                if not path.isfile(USB_WIFI_FILE):
                    oradio_log.debug("'%s' not found", USB_WIFI_FILE)
                    return  # Credentials file not found, nothing to do

                try:
                    # Read and parse JSON file
                    with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                        # Get JSON object as a dictionary
                        data = load(file)
                except (JSONDecodeError, IOError) as ex_err:
                    oradio_log.error("Failed to read or parse '%s': error: %s", USB_WIFI_FILE, ex_err)
                    self._send_message(MESSAGE_WIFI_FILE_ERROR)
                    return

                # Validate data is a list (of networks)
                if "networks" not in data or not isinstance(data["networks"], list):
                    oradio_log.error("'networks' must be a list")
                    self._send_message(MESSAGE_WIFI_FILE_ERROR)
                    return

                # Parse data found
                for i, network in enumerate(data["networks"], start=1):
                    if not validate_network(network, i):
                        self._send_message(MESSAGE_WIFI_FILE_ERROR)
                    else:
                        # Add wifi credentials to NetworkManager
                        ssid = network["SSID"].strip()
                        pswd = network["PASSWORD"].strip()
                        if _networkmanager_add(ssid, pswd):
                            oradio_log.info("Network '%s' added to NetworkManager", ssid)
                        else:
                            oradio_log.error("Failed to add '%s' to NetworkManager", ssid)

                # Remove file after succesful parsing
                try:
                    remove(USB_WIFI_FILE)
                    oradio_log.info("'%s' removed", USB_WIFI_FILE)
                except (FileNotFoundError, PermissionError) as ex_err:
                    oradio_log.error("Failed to remove '%s': %s", USB_WIFI_FILE, ex_err)
            finally:
                _usb_wifi_lock.release()
        else:
            oradio_log.debug("%s already being handled by another process", USB_WIFI_FILE)

    def _send_message(self, error) -> None:
        """
        Send a wifi state message with error info to the parent queue

        Args:
            error (str): Error code or MESSAGE_NO_ERROR if no error
        """
        # Create message
        message = {
            "source": MESSAGE_WIFI_SOURCE,
            "state" : self.get_state(),
            "error" : error
        }
        # Put message in queue
        oradio_log.debug("Send wifi service message: %s", message)
        safe_put(self._queue, message)

    def get_state(self) -> str:
        """
        Retrieve the current wifi connection state

        Returns:
            str: One of the STATE_WIFI_* constants
        """
        # Get active wifi connection, if any
        active = get_wifi_connection()

        if not active:
            # Not connected
            return STATE_WIFI_IDLE
        if active == ACCESS_POINT_SSID:
            # Connection is access point
            return STATE_WIFI_ACCESS_POINT
        # Connection to wifi network
        return STATE_WIFI_CONNECTED

    def wifi_connect(self, ssid, pswd) -> None:
        """
        Add/modify wifi network config and initiate connection in a background process

        Args:
            ssid (str): wifi network SSID
            pswd (str): wifi network password (empty string for open networks)
        """
        # Get active wifi connection
        active = get_wifi_connection()

        # Remember last connection except if currently AP mode
        if active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            set_saved_network(active)

        # Add/modify NetworkManager settings
        if not _networkmanager_add(ssid, pswd):
            # Inform controller of actual state and error
            self._send_message(MESSAGE_WIFI_FAIL_CONFIG)
            # Error, no point continuing
            return

        # Offload the connect operation to a separate process to avoid blocking
        Process(target=self._wifi_connect_process, args=(ssid,)).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_process(self, network) -> None:
        """
        Connect to the given wifi network

        Args:
            network (str): SSID of the wifi network to connect to
        """
        # Connect to network
        if not _wifi_up(network):           # Function includes logging
            # Remove failed network from NetworkManager
            _networkmanager_del(network)    # Function includes logging
        else:
            oradio_log.info("Connected with '%s'", network)

    def wifi_disconnect(self) -> None:
        """Disconnect the active wifi connection, if any."""
        # Get active wifi connection, if any
        active = get_wifi_connection()

        # If connected then disconnect
        if active:
            # Stop the active connection
            if not _wifi_down(active):
                oradio_log.error("Failed to disconnect from '%s'", active)
            else:
                oradio_log.info("Disconnected from: '%s'", active)
        else:
            oradio_log.debug("Already disconnected")

    def close(self) -> None:
        """Cleanup resources and unsubscribe queues on service shutdown."""
        # Unsubscribe callbacks from WifiEventListener
        self.nm_listener.unsubscribe(self._queue)

        # Unsubscribe callbacks from USBService
        self._usb_service.close()

        # Stop listening to USB messages
        if self._usb_listener:
            self._usb_listener.terminate()

        oradio_log.info("wifi service closed")

def _nmcli_try(func, *args, **kwargs) -> bool:
    """
    Safely call a nmcli function with logging.

    Args:
        func (callable): The nmcli function to call.
        *args: Positional arguments for the function.
        **kwargs: Keyword arguments for the function.

    Returns:
        bool: True if call succeeded, False otherwise.
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("nmcli call failed for %s: %s", func.__name__, ex_err)
        return False, None

def parse_nmcli_output(nmcli_output) -> list:
    """
    Return list of unique networks, sorted by strongest signal first,
    with indication if password is required ("closed") or not ("open").
    """
    seen_ssids = set()
    networks_formatted = []

    # Sort by signal strength descending if available, else keep order
    sorted_networks = sorted(
        nmcli_output, key=lambda n: getattr(n, "signal", 0), reverse=True
    )

    # Add unique, ignore own Access Point
    for network in sorted_networks:
        ssid = getattr(network, "ssid", "")
        if ssid and ssid != ACCESS_POINT_SSID and ssid not in seen_ssids:
            seen_ssids.add(ssid)
            networks_formatted.append({
                "ssid": ssid,
                "type": "closed" if getattr(network, "security", False) else "open"
            })

    # List of network SSIDs + password required or not
    return networks_formatted

def parse_iw_output(iw_output) -> list:
    """
    Return list of unique networks, sorted by strongest signal first,
    with indication if password is required ("closed") or not ("open").
    """
    networks = []
    ssid, signal, security = "", -1000, False

    for line in iw_output.splitlines():
        line = line.strip()
        if line.startswith("BSS"):
            if ssid:  # Only append if we have an SSID
                networks.append((ssid, signal, security))
            ssid, signal, security = "", -1000, False
        elif "SSID:" in line:
            ssid = line.split("SSID:", 1)[1].strip()
        elif "signal:" in line:
            match = search(r"signal:\s+(-?\d+(?:\.\d+)?) dBm", line)
            if match:
                signal = float(match.group(1))
        elif "capability:" in line:
            security = "closed" if "Privacy" in line else "open"

    # Append last network
    if ssid:
        networks.append((ssid, signal, security))

    # Sort by signal descending
    networks.sort(key=lambda x: x[1], reverse=True)

    # Filter unique SSIDs while preserving order
    seen_ssids = set()
    networks_formatted = [
        {"ssid": n[0], "type": n[2]}
        for n in networks
        if n[0] not in seen_ssids and not seen_ssids.add(n[0])
    ]

    # List of network SSIDs + password required or not
    return networks_formatted

def get_wifi_networks() -> list:
    """
    Get all available wifi networks, except Oradio access points.
    Tries NetworkManager (nmcli) first, then falls back to iw scan if needed.

    Note: If nmcli and iw both fail we can try forcing the scan using the NetworkMangage D-Bus API to trigger a scan
    https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/main/examples/python/gi/show-wifi-networks.py
    
    Returns:
        List[Dict[str, str]]: list of {"ssid": str, "type": "open"/"closed"}
    """
    networks = []

    oradio_log.debug("Scanning for wifi networks...")

    # 1️. Try nmcli scan
    is_ok, nmcli_output = _nmcli_try(nmcli.device.wifi, None, True)
    if is_ok and nmcli_output:
        # Filter on required info
        networks = parse_nmcli_output(nmcli_output)
    else:
        oradio_log.warning("Failed or empty nmcli scan")

    # 2. Fallback to iw scan if nmcli found nothing
    if not networks:
        oradio_log.debug("Attempting fallback scan using iw...")
        cmd = "sudo iw dev wlan0 scan flush"
        result, response = run_shell_script(cmd)
        if result and response:
            # Filter on required info
            networks = parse_iw_output(response)
        else:
            oradio_log.error("Failed iw scan: %s", response)
            return []

    # 3. Check if any networks found at all
    if not networks:
        oradio_log.warning("No networks found on either nmcli or iw scan")

    # Return list of wifi networks broadcasting their ssid
    return networks

def get_wifi_connection() -> str | None:
    """
    Get active wifi connection

    Returns:
        str | None: network ID (SSID)
    """
    # Get the network Oradio was connected to before starting access point, empty string if None
    cmd = "iw dev wlan0 info | awk '/ssid/ {print $2}' || iwgetid -r wlan0"
    result, response = run_shell_script(cmd)
    return str(response) if result else None

def _get_wifi_password(network) -> str | None:
    """
    Get password from NetworkManager for given network.

    Args:
        network (str): wifi network ssid as configured in NetworkManager

    Returns:
        str | None: password
    """
    oradio_log.debug("Get wifi password")
    cmd = f"sudo nmcli -s -g 802-11-wireless-security.psk con show \"{network}\""
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get password for '%s', error: %s", cmd, network, response)
        # Return fail, so caller can try to recover
        return None
    return response

def _wifi_up(network) -> bool:
    """
    Connect to wifi network
    
    Args:
        network (str): wifi network ssid as configured in NetworkManager
    """
    oradio_log.debug("Activate '%s'", network)
    is_ok, _ = _nmcli_try(nmcli.connection.up, network)
    return is_ok

def _wifi_down(network) -> bool:
    """
    Disconnect from wifi network

    Args:
        network (str): wifi network ssid as configured in NetworkManager
    """
    oradio_log.debug("Disconnect from: '%s'", network)
    is_ok, _ = _nmcli_try(nmcli.connection.down, network)
    return is_ok

def _networkmanager_list() -> list:
    """
    Get defined connections from NetworkManager.

    Returns:
        connections (list): list of network ids defined in NetworkManager.
    """
    oradio_log.debug("Get connections from NetworkManager")

    is_ok, result = _nmcli_try(nmcli.connection)

    # Fail on error
    if not is_ok or result is None:
        return []

    # Only wifi connections
    connections = []
    for connection in result:
        if connection.conn_type == "wifi":
            connections.append(connection.name)

    return connections

def _networkmanager_add(network, password=None) -> bool:
    """
    if network is access point then setup AP in NetworkManager
    If unknown, add network to NetworkManager
    If exists, modify network in NetworkManager

    Args:
        network (str): wifi network ssid to be configured in NetworkManager
        password (str | None): wifi network password to be configured in NetworkManager

    Returns:
        bool: True if call succeeded, False otherwise.
    """
    # Add access point to NetworkManager if not exist
    if network == ACCESS_POINT_SSID:
        if ACCESS_POINT_SSID in _networkmanager_list():
            oradio_log.debug("'%s' already in NetworkManager", ACCESS_POINT_SSID)
            return True

        oradio_log.debug("Add '%s' to NetworkManager", ACCESS_POINT_SSID)
        options = {
            "mode": "ap",
            "ssid": ACCESS_POINT_SSID,
            "ipv4.method": "shared",
            "ipv4.address": ACCESS_POINT_HOST + "/24"
        }

        is_ok, _ = _nmcli_try(nmcli.connection.add, "wifi", options, "*", ACCESS_POINT_SSID, False)
        return is_ok

    # Add wifi network to NetworkManager if not exist, modify if exists
    options = {"ssid": network}
    if password:
        oradio_log.debug("Use '%s' with password", network)
        options.update({
            "wifi-sec.key-mgmt": "wpa-psk",
            "wifi-sec.psk": password
        })
    else:
        oradio_log.debug("Use '%s' without password", network)

    # Modify existing
    if network in _networkmanager_list():
        oradio_log.debug("Modify '%s' in NetworkManager", network)
        is_ok, _ = _nmcli_try(nmcli.connection.modify, network, options)
        return is_ok

    # Add new
    oradio_log.debug("Add '%s' to NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.add, "wifi", options, "*", network, True)
    return is_ok

def _networkmanager_del(network) -> bool:
    """
    Remove given network from NetworkManager

    Args:
        network (str): wifi network ssid as configured in NetworkManager
    """
    oradio_log.debug("Remove '%s' from NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.delete, network)
    return is_ok

# Entry point for stand-alone operation
if __name__ == '__main__':

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

    # Pylint PEP8 ignoring limit of max 12 branches and 50 statement is ok for test menu
    def interactive_menu(queue):    # pylint: disable=too-many-branches, too-many-statements
        """Show menu with test options"""
        # Initialize: no services registered
        wifi_services = []

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Add WifiService instance\n"
            " 2-Remove WifiService instance\n"
            " 3-list wifi networks in NetworkManager\n"
            " 4-add network to NetworkManager\n"
            " 5-remove network from NetworkManager\n"
            " 6-list on air wifi networks\n"
            " 7-get wifi state and connection\n"
            " 8-connect to wifi network\n"
            " 9-start access point\n"
            "10-disconnect from network\n"
            "Select: "
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
                    # Close each wifi service instance
                    for wifi_service in wifi_services:
                        wifi_service.close()
                    break
                case 1:
                    print("\nAdd WifiService to list\n")
                    wifi_services.append(WifiService(queue))
                    print(f"List has {len(wifi_services)} instances\n")
                case 2:
                    print("\nDelete WifiService from list\n")
                    if wifi_services:
                        wifi_services.pop().close()
                        print(f"List has {len(wifi_services)} instances\n")
                    else:
                        print(f"{YELLOW}List has no WifiService instances{NC}\n")
                case 3:
                    print(f"\nNetworkManager wifi connections: {_networkmanager_list()}\n")
                case 4:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        if _networkmanager_add(name, pswrd):
                            print(f"\n{GREEN}'{name}' added to NetworkManager{NC}\n")
                        else:
                            print(f"\n{RED}Failed to add '{name}' to NetworkManager{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 5:
                    name = input("Enter network to remove from NetworkManager: ")
                    if name:
                        if _networkmanager_del(name):
                            print(f"\n{GREEN}'{name}' deleted from NetworkManager{NC}\n")
                        else:
                            print(f"\n{RED}Failed to delete '{name}' from NetworkManager{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    print(f"\nActive wifi networks: {get_wifi_networks()}\n")
                case 7:
                    if wifi_services:
                        wifi_state = wifi_services[0].get_state()
                        if wifi_state == STATE_WIFI_IDLE:
                            print(f"\nwifi state: '{wifi_state}'\n")
                        else:
                            print(f"\nwifi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                    else:
                        print(f"{YELLOW}List has no WifiService instances{NC}\n")
                case 8:
                    if wifi_services:
                        name = input("Enter SSID of the network to add: ")
                        pswrd = input("Enter password for the network to add (empty for open network): ")
                        if name:
                            wifi_services[0].wifi_connect(name, pswrd)
                            print(f"\nConnecting with '{name}'. Check messages for result\n")
                        else:
                            print(f"\n{YELLOW}No network given{NC}\n")
                    else:
                        print(f"{YELLOW}List has no WifiService instances{NC}\n")
                case 9:
                    if wifi_services:
                        print("\nStarting access point. Check messages for result\n")
                        wifi_services[0].wifi_connect(ACCESS_POINT_SSID, None)
                        print(f"\nConnecting with '{ACCESS_POINT_SSID}'. Check messages for result\n")
                    else:
                        print(f"{YELLOW}List has no WifiService instances{NC}\n")
                case 10:
                    if wifi_services:
                        print("\nwifi disconnected: check messages for result\n")
                        wifi_services[0].wifi_disconnect()
                        print("\nDisconnecting. Check messages for result\n")
                    else:
                        print(f"{YELLOW}List has no WifiService instances{NC}\n")
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
