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
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
"""
import os
import json
from threading import Thread, Lock
from multiprocessing import Process, Queue
from subprocess import CalledProcessError
import nmcli
import nmcli._exception as nmcli_exc
import dbus
import dbus.mainloop.glib
from dbus.exceptions import DBusException
from gi.repository import GLib

##### oradio modules ####################
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

# Dynamic tuple generation (less maintenance-heavy if exceptions change)
nmcli_exceptions = tuple(
    exc for exc in vars(nmcli_exc).values()
    if isinstance(exc, type) and issubclass(exc, Exception)
)

class SaveWifi:
    """
    Singleton-style class to store and retrieve the last wifi connection as a string
    Uses class-level variables and a threading.Lock to ensure thread-safe access
    to the shared data across all instances or direct class usage
    """
    _saved = ""     # Holds the last saved wifi connection (class-level)
    _lock = Lock()  # Lock to synchronize access to _saved for thread safety

    @classmethod
    def set_saved(cls, value):
        """
        Thread-safe setter to update the saved wifi connection string
        value (str): The wifi connection string to save
        """
        with cls._lock:        # Acquire lock to prevent race conditions
            cls._saved = str(value) if value else ""

    @classmethod
    def get_saved(cls):
        """
        Thread-safe getter to retrieve the saved wifi connection string
        Returns: The last saved wifi connection string
        """
        with cls._lock:        # Acquire lock to ensure consistent reads
            return cls._saved

class WifiEventListener:
    """
    Singleton class that listens for wifi state changes via NetworkManager D-Bus signals
    Connects to the system D-Bus, finds the wifi device managed by NetworkManager, and listens
    for the 'StateChanged' signal on the wireless device interface to track wifi connection state changes
    Runs a GLib main loop in a background thread to handle asynchronous signals without blocking the main application
    """

# In below code using same construct in multiple modules for singletons
# pylint: disable=duplicate-code

    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    def __new__(cls, *args, **kwargs):
        """Ensure only one instance of WifiEventListener is created (singleton pattern)"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WifiEventListener, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Initialize the listener by setting up the D-Bus main loop integration,
        connecting to the system bus, finding the wifi device, and subscribing
        to the 'StateChanged' signal
        """
        # Prevent re-initialization if the singleton is created again
        if self._initialized:
            return  # Avoid re-initialization if already done
        self._initialized = True

# In above code using same construct in multiple modules for singletons
# pylint: enable=duplicate-code

        # List of subscriber queues to send wifi state messages
        self._subscribers = []

        try:
            # Setup GLib main loop for dbus-python signal handling
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

            # Connect to system D-Bus
            self.bus = dbus.SystemBus()

            # Access NetworkManager object
            network_manager = self.bus.get_object(
                "org.freedesktop.NetworkManager",
                "/org/freedesktop/NetworkManager"
            )
            nm_iface = dbus.Interface(network_manager, "org.freedesktop.NetworkManager")

            # Find the wifi device (DeviceType == 2)
            self.wifi_path = None
            for path in nm_iface.GetDevices():
                dev = self.bus.get_object("org.freedesktop.NetworkManager", path)
                dev_props = dbus.Interface(dev, "org.freedesktop.DBus.Properties")
                dev_type = dev_props.Get("org.freedesktop.NetworkManager.Device", "DeviceType")
                if dev_type == 2:
                    self.wifi_path = path
                    break

        except DBusException as ex_err:
            oradio_log.error("Failed to connect to NetworkManager: %s", ex_err.get_dbus_message())
            return
        except OSError as ex_err:
            oradio_log.error("D-Bus connection error: %s", ex_err)
            return

        if not self.wifi_path:
            oradio_log.error("No wifi device found")
            return

        # Subscribe to 'StateChanged' signal on the wifi device interface
        self.bus.add_signal_receiver(
            self._wifi_state_changed,
            dbus_interface="org.freedesktop.NetworkManager.Device",
            signal_name="StateChanged",
            path=self.wifi_path
        )

        # Create GLib main loop in a background thread
        self._loop = GLib.MainLoop()
        self._thread = Thread(target=self._loop.run, daemon=True)

    def _wifi_state_changed(self, new_state, _old_state, _reason):
        """
        Callback invoked on wifi device 'StateChanged' signal
        new_state (int): The new device state
        _old_state (int): The previous device state (unused, underscore avoids pylint warning)
        _reason (int): Reason for state change (unused, underscore avoids pylint warning)
        """
        message = {"source": MESSAGE_WIFI_SOURCE}

        # Parse states: only interested in disconnected, failed and connected
        if new_state == NM_DISCONNECTED:
            # wifi disconnected
            message["state"] = STATE_WIFI_IDLE
            message["error"] = MESSAGE_NO_ERROR
            # Send message to queue of each subscriber
            oradio_log.debug("Send wifi service message: %s", message)
            for queue in self._subscribers:
                safe_put(queue, message)

        elif new_state == NM_CONNECTED:
            # wifi connected: distinguish access point vs internet availability
            active = get_wifi_connection()
            if active == ACCESS_POINT_SSID:
                # Connection is access point
                message["state"] = STATE_WIFI_ACCESS_POINT
            else:
                # Connection to wifi network WITHOUT internet access
                message["state"] = STATE_WIFI_CONNECTED
            message["error"] = MESSAGE_NO_ERROR
            # Send message to queue of each subscriber
            oradio_log.debug("Send wifi service message: %s", message)
            for queue in self._subscribers:
                safe_put(queue, message)

        elif new_state == NM_FAILED:
            # wifi failed to connect
            message["state"] = STATE_WIFI_IDLE
            message["error"] = MESSAGE_WIFI_FAIL_CONNECT
            # Send message to queue of each subscriber
            oradio_log.debug("Send wifi service message: %s", message)
            for queue in self._subscribers:
                safe_put(queue, message)

    def start(self):
        """
        Start the GLib main loop in a background thread to listen for wifi state changes
        """
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self):
        """
        Stop the GLib main loop and wait for the background thread to finish
        Use this to cleanly shutdown the listener, typically during application exit
        """
        if hasattr(self, "_loop") and self._loop.is_running():
            self._loop.quit()
        if hasattr(self, "_thread"):
            self._thread.join()

    def subscribe(self, queue):
        """
        Register a queue to receive wifi state messages
        queue (Queue): A queue object where wifi state messages will be posted
        """
        self._subscribers.append(queue)

    def unsubscribe(self, queue):
        """
        Remove a previously registered subscriber queue
        queue (Queue): The queue to unsubscribe
        """
        try:
            self._subscribers.remove(queue)
        except ValueError:
            oradio_log.debug("Was already unsubscribed from wifi events")
        else:
            oradio_log.info("Stopped listening to wifi events")

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
        :param queue: multiprocessing.Queue for sending wifi state messages
        """
        self.queue = queue
        usb_queue = Queue()

        # Start a separate process to monitor USB messages (e.g. wifi credentials)
        self._usb_listener = Process(target=self._check_usb_messages, args=(usb_queue,))
        self._usb_listener.start()

        # USBService instance to send USB state updates to usb_queue
        self._usb_service = USBService(usb_queue)

        # Start listening to NetworkManager wifi state changes
        self.nm_listener = WifiEventListener()
        self.nm_listener.start()

        # Subscribe this service's queue to receive wifi state updates
        self.nm_listener.subscribe(self.queue)

        # Send initial wifi state and no-error message
        self._send_message(MESSAGE_NO_ERROR)

    def _check_usb_messages(self, queue):
        """
        Background process to monitor USB messages from the queue
        On USB present state, check for wifi credentials on USB drive
        :param queue: Queue to receive USB messages
        """
        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            oradio_log.debug("USB message received: '%s'", message)

            # If USB is present check if USB has file with wifi credentials
            if message.get("state", "Unknown") == STATE_USB_PRESENT:
                self._handle_usb_wifi_credentials()

    def _handle_usb_wifi_credentials(self):
        """
        Check for wifi credentials on USB drive
        If found, validate and attempt to connect using those credentials
        """
        oradio_log.info("Checking %s for wifi credentials", USB_WIFI_FILE)

        # Check if wifi credentials file exists in USB drive root
        if not os.path.isfile(USB_WIFI_FILE):
            oradio_log.debug("'%s' not found", USB_WIFI_FILE)
            return  # Credentials file not found, nothing to do

        try:
            # Read and parse JSON file
            with open(USB_WIFI_FILE, "r", encoding="utf-8") as file:
                # Get JSON object as a dictionary
                data = json.load(file)
        except (json.JSONDecodeError, IOError) as ex_err:
            oradio_log.error("Failed to read or parse '%s': error: %s", USB_WIFI_FILE, ex_err)
            self._send_message(MESSAGE_WIFI_FILE_ERROR)
            return

        # Check if the SSID and PASSWORD keys are present
        ssid = data.get('SSID')
        pswd = data.get('PASSWORD')
        if not ssid or pswd is None:
            oradio_log.error("SSID and/or PASSWORD not found in '%s'", USB_WIFI_FILE)
            self._send_message(MESSAGE_WIFI_FILE_ERROR)
            return

        # Test if ssid is empty or >= 8 characters
        if 0 < len(pswd) < 8:
            oradio_log.error("Password length invalid: must be empty for open network or at least 8 characters for secured network")
            self._send_message(MESSAGE_WIFI_FILE_ERROR)
            return

        # Log wifi credentials found
        oradio_log.info("USB wifi credentials found: ssid=%s", ssid)

        # Connect to the wifi network
        self.wifi_connect(ssid, pswd)

    def _send_message(self, error):
        """
        Send a wifi state message with error info to the parent queue
        :param error: Error code or MESSAGE_NO_ERROR if no error
        """
        # Create message
        message = {
            "source": MESSAGE_WIFI_SOURCE,
            "state" : self.get_state(),
            "error" : error
        }
        # Put message in queue
        oradio_log.debug("Send wifi service message: %s", message)
        safe_put(self.queue, message)

    def get_state(self):
        """
        Retrieve the current wifi connection state
        :return: One of the STATE_WIFI_* constants
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

    def wifi_connect(self, ssid, pswd):
        """
        Add/modify wifi network config and initiate connection in a background process
        :param ssid: wifi network SSID
        :param pswd: wifi network password (empty string for open networks)
        """
        # Get active wifi connection
        active = get_wifi_connection()

        # Remember last connection except if currently AP mode
        if active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            SaveWifi.set_saved(active)

        # Add/modify NetworkManager settings
        if not _networkmanager_add(ssid, pswd):
            # Inform controller of actual state and error
            self._send_message(MESSAGE_WIFI_FAIL_CONFIG)
            # Error, no point continuing
            return

        # Offload the connect operation to a separate process to avoid blocking
        Process(target=self._wifi_connect_process, args=(ssid,)).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_process(self, network):
        """
        Connect to the given wifi network
        :param network: SSID of the wifi network to connect to
        """
        # Connect to network
        if not _wifi_up(network):           # Function includes logging
            # Remove failed network from NetworkManager
            _networkmanager_del(network)    # Function includes logging
        else:
            oradio_log.info("Connected with '%s'", network)

    def wifi_disconnect(self):
        """
        Disconnect the active wifi connection, if any
        """
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

    def close(self):
        """
        Cleanup resources and unsubscribe queues on service shutdown
        """
        # Unsubscribe callbacks from WifiEventListener
        self.nm_listener.unsubscribe(self.queue)

        # Unsubscribe callbacks from USBService
        self._usb_service.close()

        # Stop listening to USB messages
        if self._usb_listener:
            self._usb_listener.terminate()

        oradio_log.info("wifi service closed")

def get_saved_network():
    """
    Return the ssid of the last wifi connection
    """
    return SaveWifi.get_saved()

def get_wifi_networks():
    """
    Get all available wifi networks, except Oradio access points
    NetworkManager provides the networks sorted by signal strength
    :return networks ==> list of network ssid + if password required
    """
    # initialize
    networks = []

    # Get available wifi networks
    try:
        oradio_log.debug("Get list of networks broadcasting their ssid")
        # Force a rescan to get currently active networks
        # nmcli.device.wifi(ifname: str = None, rescan: bool = None) -> List[DeviceWifi]
        wifi_list = nmcli.device.wifi(None, True)
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to get wifi networks, error = %s", ex_err)
    else:
        for network in wifi_list:
            # Add unique, ignore own Access Point
            if (network.ssid != ACCESS_POINT_SSID) and (len(network.ssid) != 0) and (network.ssid not in [n["ssid"] for n in networks]):
                if network.security:
                    networks.append({"ssid": network.ssid, "type": "closed"})
                else:
                    networks.append({"ssid": network.ssid, "type": "open"})

    # Return list of wifi networks broadcasting their ssid
    return networks

def get_wifi_connection():
    """
    Get active wifi connection
    :return connection ==> network ID | None
    """
    # initialize
    network = None

    try:
        # Get all network connections
        # nmcli.connection() -> List[Connection]
        connections = nmcli.connection()
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to get active connection, error = %s", ex_err)
    else:
        # Inspect connections
        for connection in connections:
            # Ignore access point and only wifi connections with a device can be active
            if connection.conn_type == "wifi" and connection.device != "--":
                # Get connection details, inspect GENERAL.STATE
                details = nmcli.connection.show(connection.name)
                if details.get("GENERAL.STATE") == "activated":
                    # Connection is wifi, has device and is activated
                    network = connection.name

    # Return active network, None if not connected
    return network

def _get_wifi_password(network):
    """
    Get password from NetworkManager for given network
    :param network: wifi network ssid as configured in NetworkManager
    :return: password | None
    """
    oradio_log.debug("Get wifi password")
    cmd = f"sudo nmcli -s -g 802-11-wireless-security.psk con show \"{network}\""
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get password for '%s', error: %s", cmd, network, response)
        # Return fail, so caller can try to recover
        return None
    return response

def _wifi_up(network):
    """
    Connect to network
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    # Stop the connection
    try:
        oradio_log.debug("Activate '%s'", network)
        # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
        nmcli.connection.up(network)
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to activate '%s', error = %s", network, ex_err)
        return False
    return True

def _wifi_down(network):
    """
    Disconnect from network
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    # Stop the connection
    try:
        oradio_log.debug("Disconnect from: '%s'", network)
        # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
        nmcli.connection.down(network)
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to disconnect from '%s', error = %s", network, ex_err)
        return False
    return True

def _networkmanager_list():
    """
    Get defined connections from NetworkManager
    :return connections ==> list of network ids defined in NetworkManager
    """
    #Initialize
    connections = []

    # Get networks from NetworkManager
    try:
        oradio_log.debug("Get connections from NetworkManager")
        # nmcli.connection() -> List[Connection]
        result = nmcli.connection()
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to get connections from NetworkManager, error = %s", ex_err)
    else:
        # Inspect connections
        for connection in result:
            # Only wifi connections
            if connection.conn_type == "wifi":
                connections.append(connection.name)

    return connections

def _networkmanager_add(network, password=None):
    """
    if network is access point then setup AP in NetworkManager
    If unknown, add network to NetworkManager
    If exists, modify network in NetworkManager
    :param network: wifi network ssid to be configured in NetworkManager
    :param password: wifi network password to be configured in NetworkManager
    nmcli does not raise specific exception
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
            "ipv4.address": ACCESS_POINT_HOST+"/24"
        }
        try:
            # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
            nmcli.connection.add("wifi", options, "*", ACCESS_POINT_SSID, False)
        except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
            oradio_log.error("Failed to add '%s' to NetworkManager, error = %s", ACCESS_POINT_SSID, ex_err)
            return False

    # Add network to NetworkManager if not exist, modify if exists
    else:
        # Setup connection options
        options = {"ssid": network}
        if password:
            oradio_log.debug("Use '%s' with password", network)
            options.update({
                "wifi-sec.key-mgmt": "wpa-psk",
                "wifi-sec.psk": password
            })
        else:
            oradio_log.debug("Use '%s' without password", network)

        if network in _networkmanager_list():
            oradio_log.debug("Modify '%s' in NetworkManager", network)
            try:
                # nmcli.connection.modify(name: str, options: ConnectionOptions) -> None
                nmcli.connection.modify(network, options)
            except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
                oradio_log.error("Failed to modify '%s' in NetworkManager, error = %s", network, ex_err)
                return False
        else:
            oradio_log.debug("Add '%s' to NetworkManager", network)
            try:
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", network, True)
            except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
                oradio_log.error("Failed to add '%s' to NetworkManager, error = %s", network, ex_err)
                return False

    # Network is added or modified successfully
    return True

def _networkmanager_del(network):
    """
    Remove given network from NetworkManager
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    try:
        oradio_log.debug("Remove '%s' from NetworkManager", network)
        # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
        nmcli.connection.delete(network)
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * uses Python’s unpacking to merge them into a flat tuple
        oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", network, ex_err)
        return False
    return True

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
            " 0-quit\n"
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
