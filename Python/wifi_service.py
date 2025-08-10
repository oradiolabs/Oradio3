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
import threading
import nmcli

##### oradio modules ####################
from oradio_utils import check_internet_connection, run_shell_script
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    MESSAGE_WIFI_TYPE,
    STATE_WIFI_IDLE,
    STATE_WIFI_INTERNET,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_WIFI_FAIL_CONFIG,
    MESSAGE_WIFI_FAIL_START_AP,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################

class WifiService():
    """
    States and functions related to wifi handling
    - connected to a wifi network with internet, connected to a wifi network without internet, not connected, acting as access point
    Send messages on state changes
    """
    def __init__(self, queue):
        """
        Initialize wifi state
        Setup access point in NetworkManager
        Report state and error to parent process
        """
        # Initialize
        self.msg_q = queue
        self.saved = None

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
        if self.msg_q:
            self.msg_q.put(message)
        else:
            oradio_log.error("No queue proviced to send wifi service message")

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

    def get_saved_network(self):
        """
        Public function
        Return the ssid of the last wifi connection
        """
        return self.saved

    def wifi_connect(self, ssid, pswd): # pylint: disable=too-many-branches
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

        # Connecting takes time, can fail: offload to a separate thread
        # ==> Don't use reference so that the python interpreter can garbage collect when thread is done
        threading.Thread(target=self._wifi_connect_thread, args=(ssid,)).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_thread(self, network):
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

def _networkmanager_restart():
    """
    Private function
    Restart NetworkManager forces rescan and reconnect if a known wifi network is in range
    """
    cmd = "sudo systemctl restart NetworkManager"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to restart NetworkManager, error: %s", cmd, response)
        # Return fail, so caller can try to recover

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # import when running stand-alone
    from multiprocessing import Process, Queue

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("\nListening for messages")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\nMessage received: '{message}'\n")

    # Initialize
    message_queue = Queue()
    wifi = WifiService(message_queue)

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    INPUT_SELECTION = ("\nSelect a function, input the number.\n"
                       " 0-quit\n"
                       " 1-list wifi networks in NetworkManager\n"
                       " 2-add network to NetworkManager\n"
                       " 3-remove network from NetworkManager\n"
                       " 4-restart NetworkManager\n"
                       " 5-list on air wifi networks\n"
                       " 6-get wifi state and connection\n"
                       " 7-connect to wifi network\n"
                       " 8-start access point\n"
                       " 9-disconnect from network\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(INPUT_SELECTION)) # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1 # pylint: disable=invalid-name

        # Execute selected function
        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print(f"\nNetworkManager wifi connections: {_networkmanager_list()}\n") # pylint: disable=protected-access
            case 2:
                name = input("Enter SSID of the network to add: ")
                pswrd = input("Enter password for the network to add (empty for open network): ")
                if name:
                    if _networkmanager_add(name, pswrd): # pylint: disable=protected-access
                        print(f"\n{GREEN}'{name}' added to NetworkManager{NC}\n")
                    else:
                        print(f"\n{RED}Failed to add '{name}' to NetworkManager{NC}\n")
                else:
                    print(f"\n{YELLOW}No network given{NC}\n")
            case 3:
                name = input("Enter network to remove from NetworkManager: ")
                if name:
                    if _networkmanager_del(name): # pylint: disable=protected-access
                        print(f"\n{GREEN}'{name}' deleted from NetworkManager{NC}\n")
                    else:
                        print(f"\n{RED}Failed to delete '{name}' from NetworkManager{NC}\n")
                else:
                    print(f"\n{YELLOW}No network given{NC}\n")
            case 4:
                print("\nRestart NetworkManager\n")
                _networkmanager_restart() # pylint: disable=protected-access
            case 5:
                print(f"\nActive wifi networks: {get_wifi_networks()}\n")
            case 6:
                wifi_state = wifi.get_state() # pylint: disable=invalid-name
                if wifi_state == STATE_WIFI_IDLE:
                    print(f"\nWiFi state: '{wifi_state}'\n")
                else:
                    print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
            case 7:
                name = input("Enter SSID of the network to add: ")
                pswrd = input("Enter password for the network to add (empty for open network): ")
                if name:
                    wifi.wifi_connect(name, pswrd)
                    print(f"\nConnecting with '{name}'. Check messages for result\n")
                else:
                    print(f"\n{YELLOW}No network given{NC}\n")
            case 8:
                print("\nStarting access point. Check messages for result\n")
                wifi.wifi_connect(ACCESS_POINT_SSID, None)
                print(f"\nConnecting with '{ACCESS_POINT_SSID}'. Check messages for result\n")
            case 9:
                print("\nWiFi disconnected: check messages for result\n")
                wifi.wifi_disconnect()
                print("\nDisconnecting. Check messages for result\n")
            case _:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
