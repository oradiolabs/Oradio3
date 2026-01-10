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
            NM can setup a wifi hotspot that sets up a local private net, with DHCP and IP forwarding
        Not supported:
            Connecting to captive portal.
            Connecting to VPN
"""
from re import search
from threading import Thread
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
from oradio_logging import oradio_log
from oradio_utils import run_shell_script, safe_put

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    USB_MOUNT_POINT,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    MESSAGE_WIFI_SOURCE,
    STATE_WIFI_IDLE,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_WIFI_FAIL_CONFIG,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
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
_saved_lock = Lock()                # Process-safe read/write _saved_network

def set_saved_network(network) -> None:
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

@singleton
class WifiEventListener():
    """
    Singleton class to listen to wifi state changes via NetworkManager D-Bus signals.
    - Connects to the system D-Bus, finds the wifi device managed by NetworkManager, and listens
      for the 'StateChanged' signal on the wireless device interface to track wifi connection state changes
    - Runs a GLib main loop in a background thread to handle asynchronous signals without blocking the main application
    """

    def __init__(self) -> None:
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

    def subscribe(self, queue) -> None:
        """
        Subscribe a queue to receive wifi state messages.

        Args:
            queue (Queue): The queue object to receive messages.
        """
        self._subscribers.append(queue)

    def unsubscribe(self, queue) -> None:
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

        # Start listening to NetworkManager wifi state changes
        self.nm_listener = WifiEventListener()

        # Subscribe this service's queue to receive wifi state updates
        self.nm_listener.subscribe(self._queue)

        # Send initial wifi state and no-error message
        self._send_message(MESSAGE_NO_ERROR)

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
        if not networkmanager_add(ssid, pswd):
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

        oradio_log.info("wifi service closed")

def _nmcli_try(func, *args, **kwargs) -> tuple[bool, object | None]:
    """
    Safely call a nmcli function with logging.

    Args:
        func (callable): The nmcli function to call.
        *args: Positional arguments for the function.
        **kwargs: Keyword arguments for the function.

    Returns:
        tuple[bool, object | None]: (success, result). `success` is True if call succeeded,
        `result` contains the nmcli function output or None on failure.
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
    Parse the output of `iw scan` and return available networks.
    Sorts networks by signal strength descending and filters duplicate SSIDs.

    Args:
        iw_output (str): Raw string output from `iw dev wlan0 scan`.

    Returns:
        list[dict[str, str]]: List of networks with keys 'ssid' and 'type' ('open' or 'closed').
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
    Connect to a Wi-Fi network using NetworkManager.
    
    Args:
        network (str): SSID of the network to connect to.

    Returns:
        bool: True if the connection was successfully activated, False otherwise.
    """
    oradio_log.debug("Activate '%s'", network)
    is_ok, _ = _nmcli_try(nmcli.connection.up, network)
    return is_ok

def _wifi_down(network) -> bool:
    """
    Disconnect from a Wi-Fi network using NetworkManager.

    Args:
        network (str): SSID of the network to disconnect from.

    Returns:
        bool: True if successfully disconnected, False otherwise.
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

def networkmanager_add(network, password=None) -> bool:
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

    Returns:
        bool: True if deletion succeeded, False otherwise.
    """
    oradio_log.debug("Remove '%s' from NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.delete, network)
    return is_ok

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _check_messages(queue) -> None:
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it

        Args:
            queue (Queue): The queue to check for incoming messages
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    # Pylint PEP8 ignoring limit of max 12 branches and 50 statement is ok for test menu
    def interactive_menu(queue) -> None:    # pylint: disable=too-many-branches, too-many-statements
        """
        Show menu with test options

        Args:
            queue (Queue): The queue to receive wifi messages on
        """
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
                        if networkmanager_add(name, pswrd):
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

    # Start process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Present menu with tests
    interactive_menu(message_queue)

    # Stop listening to messages
    message_listener.terminate()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
