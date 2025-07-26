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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for wifi connectivity services
    :Note
    :Install
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
    ACCESS_POINT_SSID,
    MESSAGE_WIFI_TYPE,
    STATE_WIFI_IDLE,
    STATE_WIFI_INTERNET,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_WIFI_FAIL_DISCONNECT,
    MESSAGE_WIFI_FAIL_AP_START,
    MESSAGE_WIFI_FAIL_AP_STOP,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################
AP_HOST = "108.156.60.1"  # wsj.com

class WifiService():
    """
    States and functions related to wifi handling
    - connected to a wifi network with internet, connected to a wifi network without internet, not connected, acting as access point
    Send messages on state changes
    """
    def __init__(self, queue):
        """
        Initialize wifi state and error
        Report to parent process
        """
        # Initialize
        self.msg_q = queue
        self.saved_network = None

        # Send initial state and error message
        self._send_message(MESSAGE_NO_ERROR)

    def _send_message(self, error):
        """
        Send wifi service message
        :param error: Error message or code to include in the message
        """
        # Create message
        message = {
            "type": MESSAGE_WIFI_TYPE,
            "state": self.get_state(),
            "error": error
        }
        # Put message in queue
        oradio_log.debug("Send wifi service message: %s", message)
        self.msg_q.put(message)

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

    def wifi_connect(self, ssid, pswd): # pylint: disable=too-many-branches
        """
        Public function
        Done if already connected
        Create unique wifi network in NetworkManager
        Manage DNS redirection and reconnection to previous network for access point
        Start thread to connect to the wifi network
        :param ssid: Identifier of wifi network to create
        :param pswd: Password of wifi network to create
        """
        # Get active wifi connection, if any
        active = get_wifi_connection()

        # Check if already connected to ssid
        if active == ssid:
            oradio_log.debug("Connection '%s' already active", ssid)
            # Inform controller of actual state and error
            self._send_message(MESSAGE_NO_ERROR)
            # Return success, so caller can continue
            return

        # Configure if starting access point
        if ssid == ACCESS_POINT_SSID:

            # Keep current network connection when an access point is started
            if active:
                oradio_log.info("Save '%s' for reconnect", active)
                self.saved_network = {"ssid": active, "pswd": _get_wifi_password(active)}

            # Configure DNS redirection
            oradio_log.debug("Redirect DNS")
            cmd = "sudo bash -c 'echo \"address=/#/"+AP_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure DNS redirection, error: %s", cmd, error)
                # Send message with current state and error message
                self._send_message(MESSAGE_WIFI_FAIL_AP_START)

                # Reconnect to saved network (wifi_connect logs and sends message)
                if self.saved_network:
                    oradio_log.info("Reconnecting to saved_network: '%s'", self.saved_network.get("ssid"))
                    self.wifi_connect(self.saved_network.get("ssid"), self.saved_network.get("pswd"))
                    # Clear saved network
                    self.saved_network = None

                # Return fail, so caller can try to recover
                return

        # Removing before adding == replacing the network settings, just in case the password has changed
        # Disconnect and remove the current connection, not trying to reconnect (wifi_disconnect logs and sends message)
        self.wifi_disconnect(False)

        # Add network settings to NetworkManager
        if ssid == ACCESS_POINT_SSID:
            # Add access point credentials
            oradio_log.debug("Add '%s' to NetworkManager", ACCESS_POINT_SSID)
            options = {
                "mode": "ap",
                "ssid": ACCESS_POINT_SSID,
                "ipv4.method": "shared",
                "ipv4.address": AP_HOST+"/24"
            }
            if not _networkmanager_add(ssid, options, False):
                # Inform controller of actual state and error
                self._send_message(MESSAGE_WIFI_FAIL_AP_START)

                # Reconnect to saved network (wifi_connect logs and sends message)
                if self.saved_network:
                    oradio_log.info("Reconnecting to saved_network: '%s'", self.saved_network.get("ssid"))
                    self.wifi_connect(self.saved_network.get("ssid"), self.saved_network.get("pswd"))
                    # Clear saved network
                    self.saved_network = None

                # Return fail, so caller can try to recover
                return
        else:
            # Add network credentials
            if pswd:
                oradio_log.debug("Add '%s' and password to NetworkManager", ssid)
                options = {
                    "ssid": ssid,
                    "wifi-sec.key-mgmt": "wpa-psk",
                    "wifi-sec.psk": pswd
                }
            else:
                oradio_log.debug("Add '%s' without password to NetworkManager", ssid)
                options = {
                    "ssid": ssid
                }
            if not _networkmanager_add(ssid, options, True):
                # Inform controller of actual state and error
                self._send_message(MESSAGE_WIFI_FAIL_CONNECT)
                # Return fail, so caller can try to recover
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
        On error fall back to previous network
        Send message with result
        """
        # Connect to network
        if not _wifi_up(network):
            oradio_log.error("Failed to connect to '%s'", network)

            # Remove failing network from NetworkManager (_networkmanager_remove logs)
            _networkmanager_remove(network)

            # Inform controller of actual state and error
            if network == ACCESS_POINT_SSID:
                # Send message with current state and error message
                self._send_message(MESSAGE_WIFI_FAIL_AP_START)

                # Reconnect to saved network (wifi_connect logs and sends message)
                if self.saved_network:
                    oradio_log.info("Reconnecting to saved_network: '%s'", self.saved_network.get("ssid"))
                    self.wifi_connect(self.saved_network.get("ssid"), self.saved_network.get("pswd"))
                    # Clear saved network
                    self.saved_network = None
            else:
                self._send_message(MESSAGE_WIFI_FAIL_CONNECT)
        else:
            oradio_log.info("'%s' is active", network)

            # Inform controller of actual state and error
            self._send_message(MESSAGE_NO_ERROR)

    def wifi_disconnect(self, reconnect=True):
        """
        Public function
        Disconnect if connected
        Remove connection from NetworkManager
        :param reconnect: If True reconnect to saved network
        Send message with actual state and error info, if any
        """
        # Get active wifi connection, if any
        active = get_wifi_connection()

        # If connected then disconnect and remove from NetworkManager
        if active:

            # Stop the active connection and remove from NetworkManager
            if not _wifi_down(active) or not _networkmanager_remove(active):
                # Inform controller of actual state and error
                self._send_message(MESSAGE_WIFI_FAIL_DISCONNECT)
                # Return fail, so caller can try to recover
                return

            # Cleanup if stopping access point
            if active == ACCESS_POINT_SSID:
                # Remove address redirection
                oradio_log.debug("Remove DNS redirection")
                cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
                result, error = run_shell_script(cmd)
                if not result:
                    oradio_log.error("Error during <%s> to remove DNS redirection, error: %s", cmd, error)
                    # Send message with current state and error message
                    self._send_message(MESSAGE_WIFI_FAIL_AP_STOP)
                    # Return fail, so caller can try to recover
                    return

                # Reconnect to saved network (wifi_connect logs and sends message)
                if reconnect and self.saved_network:
                    oradio_log.info("Reconnecting to saved_network: '%s'", self.saved_network.get("ssid"))
                    self.wifi_connect(self.saved_network.get("ssid"), self.saved_network.get("pswd"))

                    # Clear saved network
                    self.saved_network = None
                else:
                    oradio_log.debug("Not reconnecting")

            oradio_log.info("Disconnected from: '%s'", active)
        else:
            oradio_log.debug("Already disconnected")

        # Inform controller of actual state, no error
        self._send_message(MESSAGE_NO_ERROR)

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
        oradio_log.debug("Get active connection")
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
                if details["GENERAL.STATE"] == "activated":
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
    return response.strip()

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
        oradio_log.debug("Get wifi connections")
        for connection in result:
            # Only wifi connections
            if connection.conn_type == "wifi":
                connections.append(connection.name)

    return connections

def _networkmanager_add(network, config, autoconnect):
    """
    Private function
    Add given network to NetworkManager
    :param network: wifi network ssid to be configured in NetworkManager
    :param config: wifi network configuration to be configured in NetworkManager
    :param autoconnect: Automatically reconnect if connection gets lost
    nmcli does not raise specific exception
    """
    try:
        oradio_log.debug("Add '%s' to NetworkManager", network)
        # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
        nmcli.connection.add("wifi", config, "*", network, autoconnect)
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
        oradio_log.error("Failed to add '%s' to NetworkManager, error = %s", network, ex_err)
        return False
    return True

def _networkmanager_remove(network):
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
    except Exception as ex_err:       # pylint: disable=broad-exception-caught
        oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", network, ex_err)
        return False
    return True

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("Listening for messages\n")

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
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-get wifi state\n"
                       " 2-list on air wifi networks\n"
                       " 3-list wifi networks in NetworkManager\n"
                       " 4-remove network from NetworkManager\n"
                       " 5-get active wifi connection\n"
                       " 6-connect to wifi network\n"
                       " 7-start access point\n"
                       " 8-disconnect from network\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(INPUT_SELECTION))  # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1  # pylint: disable=invalid-name

        # Execute selected function
        match function_nr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print(f"\nWiFi state: {wifi.get_state()}\n")
            case 2:
                print(f"\nActive wifi networks: {get_wifi_networks()}\n")
            case 3:
                print(f"\nNetworkManager wifi connections: {_networkmanager_list()}\n")   # pylint: disable=protected-access
            case 4:
                network_id = input("Enter connection to remove from NetworkManager: ")
                if network_id:
                    print(f"\nRemoved {network_id} from NetworkManager: {_networkmanager_remove(network_id)}\n")
                else:
                    print("\nNo connection given\n")
            case 5:
                print(f"\nActive wifi connection: {get_wifi_connection()}\n")
            case 6:
                network_id = input("Enter SSID of the network to add: ")
                password = input("Enter password for the network to add (empty for open network): ")
                if network_id:
                    wifi.wifi_connect(network_id, password)
                    if password:
                        print(f"\nConnecting to '{network_id}' with password '{password}'. Check messages for result\n")
                    else:
                        print(f"\nConnecting to '{network_id}' without password. Check messages for result\n")
                else:
                    print("\nNo network given\n")
            case 7:
                print("\nStarting access point. Check messages for result\n")
                wifi.wifi_connect(ACCESS_POINT_SSID, None)
            case 8:
                print("\nWiFi disconnected: check messages for result\n")
                wifi.wifi_disconnect()
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
