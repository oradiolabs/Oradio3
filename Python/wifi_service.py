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
@summary: Class for WiFi connectivity services
    :Note
    :Install
    :Documentation
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
"""
import nmcli
from threading import Thread

##### oradio modules ####################
import oradio_utils

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
ACCESS_POINT_HOST = "108.156.60.1"  # wsj.com

class wifi_service():
    """
    States and functions related to WiFi handling
    - States: Connected to a WiFi network, not connected, acting as access point
    Send messages on state changes
    """
    def __init__(self, queue):
        """
        Initialize WiFi state and error
        Report to parent process
        """
        # Initialize
        self.msg_q = queue
        self.error = None

        # Send initial state and error message
        self.send_wifi_message()

        # Log status
        oradio_utils.logging("info", "WiFi service initialized")

    def send_wifi_message(self):
        """
        Send WiFi message
        :param ssid ==> If connection fails then send ssid, so control can 
        Include ssid if define
        """
        # Create message
        message = {}
        message["type"]  = MESSAGE_WIFI_TYPE
        message["state"] = get_wifi_state()
        message["error"] = self.error

        # Put message in queue
        self.msg_q.put(message)

        # Log status
        oradio_utils.logging("info", f"WiFi message sent: {message}")

    def wifi_connect(self, ssid, password):
        """
        Done if already connected
        Create unique WiFi network in NetworkManager
        Start thread to connect to the WiFi network
        :param ssid ==> Identifier of WiFi network to create
        :param password ==> Password of WiFi network to create
        """
        # Initialize
        self.error = None

        # Get active WiFi connection, if any
        active = get_wifi_connection()

        # Check if already connected to ssid
        if active == ssid:
            oradio_utils.logging("info", f"Connection '{ssid}' already active")
            # Inform controller of actual state and error
            self.send_wifi_message()
            # Return success, so caller can continue
            return True

        # If connected then disconnect and cleanup NetworkManager
        if active:

            # Stop the active connection
            try:
                # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.down(active)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to disconnect from '{active}', error = {ex_err}")
                # Inform controller of actual state and error
                if ssid == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_START_AP
                else:
                    self.error = MESSAGE_WIFI_FAIL_CONNECT
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False

        # Ensure NetworkManager has no old ssid info
        if ssid in get_wifi_connections_NM():
            # Delete the ssid from NetworkManager
            try:
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(ssid)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to remove '{ssid}' from NetworkManager, error = {ex_err}")
                # Inform controller of actual state and error
                if ssid == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_START_AP
                else:
                    self.error = MESSAGE_WIFI_FAIL_CONNECT
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False

        # Setup access point or network connection
        if ssid == ACCESS_POINT_SSID:
            # Create access point
            try:
                options = {
                    "mode": "ap",
                    "ssid": ACCESS_POINT_SSID,
                    "ipv4.method": "shared",
                    "ipv4.address": ACCESS_POINT_HOST+"/24"
                }
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", ACCESS_POINT_SSID, False)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to add access point '{ACCESS_POINT_SSID}', error = {ex_err}")
                # Inform controller of actual state and error
                self.error = MESSAGE_WIFI_FAIL_START_AP
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False
        else:
            # Add WiFi network configuration
            try:
                options = {
                    "ssid": ssid,
                    "wifi-sec.key-mgmt": "wpa-psk",
                    "wifi-sec.psk": password
                }
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", ssid, True)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to configure WiFi network '{ssid}', error = {ex_err}")
                # Inform controller of actual state and error
                self.error = MESSAGE_WIFI_FAIL_CONNECT
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False

        # Connecting takes time, can fail: offload to a separate thread
        # ==> Don't use reference so that the python interpreter can garbage collect when thread is done
        Thread(target=self.wifi_connect_thread, args=(ssid, active,)).start()

        oradio_utils.logging("info", f"Connecting to '{ssid}' started")

        # Return success, so caller can continue
        return True

    def wifi_connect_thread(self, new_ssid, old_ssid):
        """
        Activate the connection
        Send message with result
        """
        # Initialize
        self.error = None

        # Connect to new_ssid
        try:
            # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
            nmcli.connection.up(new_ssid)
        except Exception as ex_err:
            oradio_utils.logging("error", f"Failed to activate '{new_ssid}', error = {ex_err}")

            # Connect to the old_ssid
            try:
                # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
                nmcli.connection.up(old_ssid)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to activate '{old_ssid}', error = {ex_err}")
                if new_ssid == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_START_AP
                else:
                    self.error = MESSAGE_WIFI_FAIL_CONNECT

            # Delete new_ssid from NetworkManager
            try:
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(new_ssid)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to remove '{new_ssid}' from NetworkManager, error = {ex_err}")
                """ OMJ: NetworkManager now has an orphan. Do we need to do garbage collection? """

        # Connected to new_ssid: cleanup old_ssid
        else:
            oradio_utils.logging("success", f"'{new_ssid}' is active")

            # Delete old_ssid from NetworkManager, if exists
            if old_ssid:
                try:
                    # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                    nmcli.connection.delete(old_ssid)
                except Exception as ex_err:
                    oradio_utils.logging("error", f"Failed to remove '{old_ssid}' from NetworkManager, error = {ex_err}")
                    """ OMJ: NetworkManager now has an orphan. Do we need to do garbage collection? """

        # Inform controller of actual state and error
        self.send_wifi_message()

    def wifi_disconnect(self):
        """
        Disconnect if connected to connection
        If exists remove access point from NetworkManager
        Send message with actual state and error info, if any
        """
        # Initialize
        self.error = None

        # Get active WiFi connection, if any
        active = get_wifi_connection()

        # If connected then disconnect and remove from NetworkManager
        if active:

            # Stop the active connection
            try:
                # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.down(active)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to disconnect from '{active}', error = {ex_err}")
                # Inform controller of actual state and error
                if active == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_STOP
                else:
                    self.error = MESSAGE_WIFI_FAIL_DISCONNECT
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False

            # Delete the active network from NetworkManager
            try:
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(active)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to remove '{active}' from NetworkManager, error = {ex_err}")
                # Inform controller of actual state and error
                if active == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_STOP
                else:
                    self.error = MESSAGE_WIFI_FAIL_DISCONNECT
                self.send_wifi_message()
                # Return fail, so caller can try to recover
                return False

            # Inform controller of actual state, no error
            self.send_wifi_message()

        # Return success, so caller can continue
        return True

    def access_point_start(self):
        """
        Redirect DNS to internal
        Setup access point network
        """
        oradio_utils.logging("info", "Setup access point")

        # Initialize
        self.error = None

        # Configure DNS redirection
        cmd = "sudo bash -c 'echo \"address=/#/"+ACCESS_POINT_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
        result, error = oradio_utils.run_shell_script(cmd)
        if not result:
            oradio_utils.logging("error", f"Error during <{cmd}> to configure DNS redirection, error: {error}")
            # Inform controller of actual state and error
            self.error = MESSAGE_WIFI_FAIL_START_AP
            self.send_wifi_message()
            # Return fail, so caller can try to recover
            return False

        oradio_utils.logging("info", "DNS redirect active")

        # Setup and start acccess point
        if not self.wifi_connect(ACCESS_POINT_SSID, None):
            oradio_utils.logging("error", f"Failed to connect '{ACCESS_POINT_SSID}'")
            # wifi_connect function informs controller
            # Return fail, so caller can try to recover
            return False

        # Return success, so caller can continue
        return True

    def access_point_stop(self):
        """
        Stop and cleanup access point
        Remove DNS redirect to internal 
        """
        # Initialize
        status = True
        self.error = None

        # Remove address redirection
        cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
        result, error = oradio_utils.run_shell_script(cmd)
        if not result:
            oradio_utils.logging("error", f"Error during <{cmd}> to remove DNS redirection, error: {error}")
            # Inform controller of actual state and error
            self.error = MESSAGE_WIFI_FAIL_STOP_AP
            self.send_wifi_message()
            # Return fail, so caller can try to recover
            return False

        oradio_utils.logging("info", "DNS redirection removed")

        # Only disconnect if access point is active
        if get_wifi_connection() == ACCESS_POINT_SSID:
            # Disconnect and remove the access point without sending message
            if not self.wifi_disconnect():
                oradio_utils.logging("error", f"Failed to disconnect '{ACCESS_POINT_SSID}'")
                # wifi_connect function informs controller
                # Return fail, so caller can try to recover
                return False

        # Return success, so caller can continue
        return True

def get_wifi_state():
    """
    Using threads for connect and access point we cannot use class variables
    """
    # Get active WiFi connection, if any
    active = get_wifi_connection()
    # No connection: idle
    if not active:
        return STATE_WIFI_IDLE
    # Connection to access point
    elif active == ACCESS_POINT_SSID:
        return STATE_WIFI_ACCESS_POINT
    # Connection to wifi network
    elif active != ACCESS_POINT_SSID:
        return STATE_WIFI_INFRASTRUCTURE

def get_wifi_networks():
    """
    Get all available WiFi networks, except Oradio access points
    :return networks ==> list of network ssid + if password required, sorted by strongest signal first
    """
    # initialize
    networks = []

    # Get available WiFi networks
    try:
        # nmcli.device.wifi(ifname: str = None, rescan: bool = None) -> List[DeviceWifi]
        wifi_list = nmcli.device.wifi(None, None)
    except Exception as ex_err:
        oradio_utils.logging("error", f"Failed to get WiFi networks, error = {ex_err}")
    else:
        for network in wifi_list:
            # Add unique, ignore own Access Point
            if network.ssid != ACCESS_POINT_SSID and not any(network.ssid in d['ssid'] for d in networks):
                networks.append({"ssid": network.ssid, "security": bool(network.security)})

    return networks

def get_wifi_connections_NM():
    """
    Get defined connections from NetworkManager
    :return connections ==> list of network ids defined in NetworkManager
    """
    #Initialize
    connections = []

    # Get networks from NetworkManager
    try:
        # nmcli.connection() -> List[Connection]
        list = nmcli.connection()
    except Exception as ex_err:
        oradio_utils.logging("error", f"Failed to get connections, error = {ex_err}")
    else:
        # Inspect connections
        for connection in list:
            # Only WiFi connections
            if connection.conn_type == "wifi":
                connections.append(connection.name)

    return connections

def get_wifi_connection():
    """
    Get active WiFi connection
    :return connection ==> network ID | None
    """
    # initialize
    network = None

    try:
        # Get all network connections
        # nmcli.connection() -> List[Connection]
        connections = nmcli.connection()
    except Exception as ex_err:
        oradio_utils.logging("error", f"Failed to get network connections, error = {ex_err}")
    else:
        # Inspect connections
        for connection in connections:
            # Ignore access point and only WiFi connections with a device can be active
            if connection.conn_type == "wifi" and connection.device != "--":
                # Get connection details, inspect GENERAL.STATE
                details = nmcli.connection.show(connection.name)
                if details["GENERAL.STATE"] == "activated":
                    # Connection is WiFi, has device and is activated
                    network = connection.name

    return network

''' Park until proven to be needed
def wifi_clean_NM():
    """
    Rem networks in NetworkManager except the active connection
    """
    # Get active WiFi connection, if any
    active = get_wifi_connection_NM()
    for ssid in get_wifi_connections_NM():
        if ssid != active:
            # Delete old_ssid from NetworkManager
            try:
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(old_ssid)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to remove '{old_ssid}' from NetworkManager, error = {ex_err}")
                """ OMJ: NetworkManager still has an orphan. What else can we do to clean the NetworkManager? """
                # Return fail, so caller can try to recover
                return False

    # Return success, so caller can continue
    return True
'''

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        oradio_utils.logging("info", "Listening for messages")

        while True:
            # Wait for WiFi message
            message = queue.get(block=True, timeout=None)
            # Show message received
            oradio_utils.logging("info", f"Message received: '{message}'")

    # Initialize
    message_queue = Queue()
    oradio_wifi_service = wifi_service(message_queue)

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-get WiFi state\n"
                       " 2-list on air WiFi networks\n"
                       " 3-list WiFi networks in NetworkManager\n"
                       " 4-get active WiFi connection\n"
                       " 5-connect to WiFi network\n"
                       " 6-disconnect from WiFi network\n"
                       " 7-start access point\n"
                       " 8-stop access point\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                break
            case 1:
                oradio_utils.logging("info", f"WiFi state: {get_wifi_state()}")
            case 2:
                oradio_utils.logging("info", f"Registered WiFi networks: {get_wifi_networks()}")
            case 3:
                oradio_utils.logging("info", f"Defined WiFi connections: {get_wifi_connections_NM()}")
            case 4:
                oradio_utils.logging("info", f"Active WiFi connection: {get_wifi_connection()}")
            case 5:
                ssid = input("Enter SSID of the network to add: ")
                pswd = input("Enter password for the network to add: ")
                if ssid and pswd:
                    oradio_wifi_service.wifi_connect(ssid, pswd)
                    oradio_utils.logging("info", f"Connecting to ssid: '{ssid}', password: '{pswd}'. Check messages for result")
                else:
                    oradio_utils.logging("warning", "No SSID and/or password given")
            case 6:
                oradio_utils.logging("info", f"wifi_disconnect() returned '{oradio_wifi_service.wifi_disconnect()}'")
            case 7:
                if oradio_wifi_service.access_point_start():
                    oradio_utils.logging("info", "Setting up access point. Check messages for result")
                else:
                    oradio_utils.logging("error", "Failed to setup access point")
            case 8:
                if oradio_wifi_service.access_point_stop():
                    oradio_utils.logging("info", "WiFi access point stopped")
                else:
                    oradio_utils.logging("error", "Failed to stop access point")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
