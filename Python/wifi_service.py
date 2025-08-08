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
from multiprocessing import Process, Queue
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
import nmcli

##### oradio modules ####################
from oradio_utils import check_internet_connection, run_shell_script, safe_put
from usb_service import USBService
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    USB_MOUNT_POINT,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    STATE_USB_PRESENT,
    MESSAGE_WIFI_TYPE,
    STATE_WIFI_IDLE,
    STATE_WIFI_INTERNET,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_WIFI_FILE_ERROR,
    MESSAGE_WIFI_FAIL_CONFIG,
    MESSAGE_WIFI_FAIL_START_AP,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_UNKNOWN_STATE,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
WIFI_MONITOR  = "Wifi_invoer.json"                      # Name of file used to monitor for wifi credentials
WEB_WIFI_PATH = "/tmp"                                  # Path for web interface file with wifi credentials
USB_WIFI_FILE = USB_MOUNT_POINT + "/Wifi_invoer.json"   # USB file with wifi credentials
WEB_WIFI_FILE = WEB_WIFI_PATH + "/Wifi_invoer.json"     # Web file with wifi credentials
TIMEOUT       = 10                                      # Seconds to wait

class WifiService():
    """
    States and functions related to wifi handling:
    - connected to a wifi network with internet, connected to a wifi network without internet, not connected, acting as access point
    Send messages on state changes
    """
    class WebMonitor(PatternMatchingEventHandler):
        """
        Monitor wifi credentials file created by the web interface
        """
        def __init__(self, handler, *args, **kwargs):
            """Class contructor, including parent class PatternMatchingEventHandler"""
            super().__init__(*args, **kwargs)
            self.handler = handler

        def on_created(self, event):
            """When file is created"""
            oradio_log.debug("Web interface wifi credentials '%s' created", event.src_path)
            # Parse file and try to connect
            self.handler(WEB_WIFI_FILE)
            # Cleanup: remove file
            try:
                os.remove(WEB_WIFI_FILE)
            except PermissionError:
                oradio_log.error("Permission denied deleting '%s'", WEB_WIFI_FILE)
            except OSError as ex_err:
                oradio_log.error("Error deleting '%s': %s", WEB_WIFI_FILE, ex_err)

    def __init__(self, queue):
        """
        Initialize wifi state
        Report state and error to parent process
        """
        # Initialize
        self.saved = None
        self.queue = queue
        usb_queue = Queue()

        # Start  process to monitor the USB message queue
        self.usb_listener = Process(target=self._check_usb_messages, args=(usb_queue,))
        self.usb_listener.start()

        # Start monitoring USB status
        self.usb = USBService(usb_queue)

        # Set observer to handle wifi credentials submitted using web interface
        self.observer = Observer()
        # Pass private functions as arguments to avoid pylint protected-access warnings
        event_handler = self.WebMonitor(self._handle_usb_wifi_credentials, patterns=[WIFI_MONITOR])
        self.observer.schedule(event_handler, path = WEB_WIFI_PATH, recursive=False)
        self.observer.start()

        # Send initial state and error message
        self._send_message(MESSAGE_NO_ERROR)

    def _send_message(self, error):
        """
        Send wifi service message
        :param error: Error message
        """
        # Create message
        message = {
            "type": MESSAGE_WIFI_TYPE,
            "state": self.get_state(),
            "error": error
        }
        # Put message in queue
        oradio_log.debug("Send wifi service message: %s", message)
        safe_put(self.queue, message)

    def get_state(self):
        """
        Public function
        Using threads for connect and access point we cannot use class variables
        """
        # Get active wifi connection, if any
        active = get_wifi_connection()

        if not active:
            # Not connected
            return STATE_WIFI_IDLE
        if active == ACCESS_POINT_SSID:
            # Connection is access point
            return STATE_WIFI_ACCESS_POINT
        if check_internet_connection():
            # Connection to wifi network with internet access
            return STATE_WIFI_INTERNET
        # Connection to wifi network WITHOUT internet access
        return STATE_WIFI_CONNECTED

    def _check_usb_messages(self, queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and process it
        :param queue = the queue to check for
        """
        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Process message received
            oradio_log.debug("USB message received: '%s'", message)
            if message.get("state", MESSAGE_UNKNOWN_STATE) == STATE_USB_PRESENT:
                if not self._handle_usb_wifi_credentials(USB_WIFI_FILE):
                    # Parse file and try to connect
                    self._send_message(MESSAGE_WIFI_FILE_ERROR)

    def _handle_usb_wifi_credentials(self, wifi_file):
        """
        Check if wifi credentials are available on the USB drive root folder
        If exists, then try to connect using the wifi credentials from the file
        """
        oradio_log.info("Checking for %s", wifi_file)

        # Check if wifi credentials file exists in USB drive root
        if not os.path.isfile(wifi_file):
            oradio_log.debug("'%s' not found", wifi_file)
            return False

        # Read and parse JSON file
        with open(wifi_file, "r", encoding="utf-8") as file:
            try:
                # Get JSON object as a dictionary
                data = json.load(file)
            except json.JSONDecodeError:
                oradio_log.error("Error parsing '%s'", wifi_file)
                return False

        # Check if the SSID and PASSWORD keys are present
        if data and 'SSID' in data.keys() and 'PASSWORD' in data.keys():
            ssid = data['SSID']
            pswd = data['PASSWORD']
        else:
            oradio_log.error("SSID and/or PASSWORD not found in '%s'", wifi_file)
            return False

        # Test if ssid is empty or >= 8 characters
        if 0 < len(pswd) < 8:
            oradio_log.error("Password must be empty for open network or at least 8 characters for secured network")
            return False

        # Log wifi credentials found
        oradio_log.info("USB wifi credentials found: ssid=%s", ssid)

        # Connect to the wifi network
        self._wifi_connect(ssid, pswd)

        # No issues found
        return True

    def get_saved_network(self):
        """
        Public function
        Return the ssid of the last wifi connection
        """
        return self.saved

    def _wifi_connect(self, ssid, pswd): # pylint: disable=too-many-branches
        """
        Public function
        Create/modify wifi network in NetworkManager
        Start thread to connect to the wifi network
        :param ssid: Identifier of wifi network to create
        :param pswd: Password of wifi network to create
        """
        # Get active wifi connection, if any
        active = get_wifi_connection()

        # Store network connection, but not if access point
        if active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            self.saved = active

        # Add/modify NetworkManager settings
        if not _networkmanager_add(ssid, pswd):
            # Inform controller of actual state and error
            self._send_message(MESSAGE_WIFI_FAIL_CONFIG)
            # Error, no point continuing
            return

        # Connecting takes time, can fail: offload to a separate process
        # ==> Don't use reference so that the python interpreter can garbage collect when process is done
        Process(target=self._wifi_connect_process, args=(ssid,)).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_process(self, network):
        """
        Private function
        Activate the network
        :param network: wifi network ssid as configured in NetworkManager
        On error determine error message
        Send message with actual state and error info
        """
        # Initialize: assume no error
        err_msg = MESSAGE_NO_ERROR

        # Connect to network
        if not _wifi_up(network):
            oradio_log.error("Failed to connect with '%s'", network)
            # Inform control of error
            if network == ACCESS_POINT_SSID:
                err_msg = MESSAGE_WIFI_FAIL_START_AP
            else:
                err_msg = MESSAGE_WIFI_FAIL_CONNECT
            # Remove failed network from NetworkManager
            # Ignore success/fail: err_msg is already FAIL
            _networkmanager_del(network)
        else:
            oradio_log.info("Connected with '%s'", network)

        # Inform controller of actual state and error info
        self._send_message(err_msg)

    def wifi_disconnect(self):
        """
        Public function
        Disconnect if connected
        No messages required
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

    def _stop(self):
        """Stop the wifi credentials monitors"""
        # Stop listening to USB messages
        if self.usb_listener:
            self.usb_listener.kill()
        # Only stop if active
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=TIMEOUT)
        # Log status
        oradio_log.info("wifi service stopped")

def get_wifi_networks():
    """
    Public function
    Get all available wifi networks, except Oradio access points
    :return networks ==> list of network ssid + if password required, sorted by strongest signal first
    """
    # initialize
    networks = []

    # Get available wifi networks
    try:
        oradio_log.debug("Get list of networks broadcasting their ssid")
        # Force a rescan to get currently active networks
        # nmcli.device.wifi(ifname: str = None, rescan: bool = None) -> List[DeviceWifi]
        wifi_list = nmcli.device.wifi(None, True)
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
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
    Public function
    Get active wifi connection
    :return connection ==> network ID | None
    """
    # initialize
    network = None

    try:
        # Get all network connections
        # nmcli.connection() -> List[Connection]
        connections = nmcli.connection()
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
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
    Private function
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
    Private function
    Connect to network
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    # Stop the connection
    try:
        oradio_log.debug("Activate '%s'", network)
        # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
        nmcli.connection.up(network)
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
        oradio_log.error("Failed to activate '%s', error = %s", network, ex_err)
        return False
    return True

def _wifi_down(network):
    """
    Private function
    Disconnect from network
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    # Stop the connection
    try:
        oradio_log.debug("Disconnect from: '%s'", network)
        # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
        nmcli.connection.down(network)
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
        oradio_log.error("Failed to disconnect from '%s', error = %s", network, ex_err)
        return False
    return True

def _networkmanager_list():
    """
    Private function
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
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
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
    Private function
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
        except Exception as ex_err: # pylint: disable=broad-exception-caught
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
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Failed to modify '%s' in NetworkManager, error = %s", network, ex_err)
                return False
        else:
            oradio_log.debug("Add '%s' to NetworkManager", network)
            try:
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", network, True)
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Failed to add '%s' to NetworkManager, error = %s", network, ex_err)
                return False

    # Network is added or modified successfully
    return True

def _networkmanager_del(network):
    """
    Private function
    Remove given network from NetworkManager
    :param network: wifi network ssid as configured in NetworkManager
    nmcli does not raise specific exception
    """
    try:
        oradio_log.debug("Remove '%s' from NetworkManager", network)
        # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
        nmcli.connection.delete(network)
    except Exception as ex_err: # pylint: disable=broad-exception-caught
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
        wifi = WifiService(queue)

        # Show menu with test options
        input_selection = (
            "Select a function, input the number:\n"
            " 0-quit\n"
            " 1-list wifi networks in NetworkManager\n"
            " 2-add network to NetworkManager\n"
            " 3-remove network from NetworkManager\n"
            " 4-list on air wifi networks\n"
            " 5-get wifi state and connection\n"
            " 6-connect to wifi network\n"
            " 7-start access point\n"
            " 8-disconnect from network\n"
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
                    wifi._stop() # pylint: disable=protected-access
                    break
                case 1:
                    print(f"\nNetworkManager wifi connections: {_networkmanager_list()}\n")
                case 2:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        if _networkmanager_add(name, pswrd):
                            print(f"\n{GREEN}'{name}' added to NetworkManager{NC}\n")
                        else:
                            print(f"\n{RED}Failed to add '{name}' to NetworkManager{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 3:
                    name = input("Enter network to remove from NetworkManager: ")
                    if name:
                        if _networkmanager_del(name):
                            print(f"\n{GREEN}'{name}' deleted from NetworkManager{NC}\n")
                        else:
                            print(f"\n{RED}Failed to delete '{name}' from NetworkManager{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 4:
                    print(f"\nActive wifi networks: {get_wifi_networks()}\n")
                case 5:
                    wifi_state = wifi.get_state()
                    if wifi_state == STATE_WIFI_IDLE:
                        print(f"\nWiFi state: '{wifi_state}'\n")
                    else:
                        print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                case 6:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        # Calling private function for testing is ok
                        wifi._wifi_connect(name, pswrd) # pylint: disable=protected-access
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 7:
                    print("\nStarting access point. Check messages for result\n")
                    # Calling private function for testing is ok
                    wifi._wifi_connect(ACCESS_POINT_SSID, None) # pylint: disable=protected-access
                    print(f"\nConnecting with '{ACCESS_POINT_SSID}'. Check messages for result\n")
                case 8:
                    print("\nWiFi disconnected: check messages for result\n")
                    wifi.wifi_disconnect()
                    print("\nDisconnecting. Check messages for result\n")
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
    if message_listener:
        message_listener.kill()
