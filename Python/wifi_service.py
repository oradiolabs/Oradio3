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
from threading import Thread
import nmcli

##### oradio modules ####################
from oradio_utils import check_internet_connection, run_shell_script
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    ACCESS_POINT_SSID,
    MESSAGE_WIFI_TYPE,
    STATE_WIFI_IDLE,
    STATE_WIFI_INFRASTRUCTURE,
    STATE_WIFI_LOCAL_NETWORK,
    STATE_WIFI_ACCESS_POINT,
    MESSAGE_NO_ERROR,
    MESSAGE_WIFI_FAIL_CONNECT,
    MESSAGE_WIFI_FAIL_DISCONNECT,
    MESSAGE_WIFI_FAIL_AP_START,
    MESSAGE_WIFI_FAIL_AP_STOP
)

##### LOCAL constants ####################
AP_HOST = "108.156.60.1"  # wsj.com

class WIFIService():
    """
    States and functions related to wifi handling
    - States: Connected to a wifi network, not connected, acting as access point
    Send messages on state changes
    """
    def __init__(self, queue):
        """
        Initialize wifi state and error
        Report to parent process
        """
        # Initialize
        self.msg_q = queue
        self.error = None
        self.saved_ssid = None

    def send_message(self):
        """
        Send wifi message
        :param ssid ==> If connection fails then send ssid, so control can 
        Include ssid if define
        """
        # Create message
#OMJ: Het type klopt niet? Het is geen web service state message, eerder iets als info. Maar voor control is wel een state...
        message = {"type": MESSAGE_WIFI_TYPE, "state": self.get_state()}

        # Optionally add error message
        if self.error:
            message["error"] = self.error
        else:
            message["error"] = MESSAGE_NO_ERROR

        # Put message in queue
        oradio_log.debug("Send wifi message: %s", message)
        self.msg_q.put(message)

    def wifi_connect(self, ssid, password):
        """
        Done if already connected
        Create unique wifi network in NetworkManager
        Start thread to connect to the wifi network
        :param ssid ==> Identifier of wifi network to create
        :param password ==> Password of wifi network to create
        """
        # Initialize
        self.error = None

        # Get active wifi connection, if any
        active = self.get_wifi_connection()

        # Check if already connected to ssid
        if active == ssid:
            oradio_log.debug("Connection '%s' already active", ssid)
            # Inform controller of actual state and error
            self.send_message()
            # Return success, so caller can continue
            return True

        # If connected then disconnect
        if active:
            # Stop the active connection
            try:
                oradio_log.debug("Disconnect from: '%s'", active)
                # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.down(active)
            except Exception as ex_err:
                oradio_log.error("Failed to disconnect from '%s', error = %s", active, ex_err)
                # Inform controller of actual state and error
                if ssid == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_START
                else:
                    self.error = MESSAGE_WIFI_FAIL_CONNECT
                self.send_message()
                # Return fail, so caller can try to recover
                return False

        # Ensure NetworkManager has no old ssid info
        if ssid in self._get_wifi_nm_connections() and ssid != self.saved_ssid:
            # Delete the ssid from NetworkManager
            try:
                oradio_log.debug("Remove '%s' from NetorkManager", ssid)
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(ssid)
            except Exception as ex_err:
                oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", ssid, ex_err)
                # Inform controller of actual state and error
                if ssid == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_START
                else:
                    self.error = MESSAGE_WIFI_FAIL_CONNECT
                self.send_message()
                # Return fail, so caller can try to recover
                return False

        # Setup access point or network connection
        if ssid == ACCESS_POINT_SSID:
            # Create access point
            try:
                oradio_log.debug("Add '%s' to NetworkManager", ACCESS_POINT_SSID)
                options = {
                    "mode": "ap",
                    "ssid": ACCESS_POINT_SSID,
                    "ipv4.method": "shared",
                    "ipv4.address": AP_HOST+"/24"
                }
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", ACCESS_POINT_SSID, False)
            except Exception as ex_err:
                oradio_log.error("Failed to add access point '%s', error = %s", ACCESS_POINT_SSID, ex_err)
                # Inform controller of actual state and error
                self.error = MESSAGE_WIFI_FAIL_AP_START
                self.send_message()
                # Return fail, so caller can try to recover
                return False
        else:
            # Saved network is already configured
            if ssid != self.saved_ssid:
                # Add wifi network configuration
                try:
                    oradio_log.debug("Add '%s' to NetworkManager", ssid)
                    options = {
                        "ssid": ssid,
                        "wifi-sec.key-mgmt": "wpa-psk",
                        "wifi-sec.psk": password
                    }
                    # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                    nmcli.connection.add("wifi", options, "*", ssid, True)
                except Exception as ex_err:
                    oradio_log.error("Failed to configure wifi network '%s', error = %s", ssid, ex_err)
                    # Inform controller of actual state and error
                    self.error = MESSAGE_WIFI_FAIL_CONNECT
                    self.send_message()
                    # Return fail, so caller can try to recover
                    return False
            else:
                oradio_log.debug("Network '%s' already exists in NetworkManager", ssid)

        # Connecting takes time, can fail: offload to a separate thread
        # ==> Don't use reference so that the python interpreter can garbage collect when thread is done
        Thread(target=self._wifi_connect_thread, args=(ssid, active,)).start()

        oradio_log.info("Connecting to '%s' started", ssid)

        # Return success, so caller can continue
        return True

    def _wifi_connect_thread(self, new_ssid, old_ssid):
        """
        Activate the connection
        Send message with result
        """
        # Initialize
        self.error = None

        # Connect to new_ssid
        try:
            oradio_log.debug("Activate '%s'", new_ssid)
            # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
            nmcli.connection.up(new_ssid)
        except Exception as ex_err:
            oradio_log.error("Failed to activate '%s', error = %s", new_ssid, ex_err)
            if new_ssid == ACCESS_POINT_SSID:
                self.error = MESSAGE_WIFI_FAIL_AP_START
            else:
                self.error = MESSAGE_WIFI_FAIL_CONNECT

            # Connect to the old_ssid
            if old_ssid:
                try:
                    oradio_log.debug("Failed to activate '%s', activate '%s'", new_ssid, old_ssid)
                    # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
                    nmcli.connection.up(old_ssid)
                except Exception as ex_err:
                    oradio_log.error("Failed to activate '%s', error = %s", old_ssid, ex_err)
                    if new_ssid == ACCESS_POINT_SSID:
                        self.error = MESSAGE_WIFI_FAIL_AP_START
                    else:
                        self.error = MESSAGE_WIFI_FAIL_CONNECT
                else:
                    oradio_log.info("Connect to '%s' is active", old_ssid)

            # Delete new_ssid from NetworkManager
            try:
                oradio_log.debug("Failed to activate '%s': remove from NetworkManager", new_ssid)
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(new_ssid)
            except Exception as ex_err:
                oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", new_ssid, ex_err)
                ''' OMJ: NetworkManager now has an orphan. Do we need to do garbage collection? '''

        # Connected to new_ssid: cleanup old_ssid
        else:
            oradio_log.info("'%s' is active", new_ssid)

            # Delete old_ssid from NetworkManager, if exists
            if old_ssid and old_ssid != self.saved_ssid:
                try:
                    oradio_log.debug("Remove '%s' from NetworkManager", old_ssid)
                    # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                    nmcli.connection.delete(old_ssid)
                except Exception as ex_err:
                    oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", old_ssid, ex_err)
                    ''' OMJ: NetworkManager now has an orphan. Do we need to do garbage collection? '''

        # Inform controller of actual state and error
        self.send_message()

    def _wifi_disconnect(self):
        """
        Disconnect if connected to connection
        If exists remove access point from NetworkManager
        Send message with actual state and error info, if any
        """
        # Initialize
        self.error = None

        # Get active wifi connection, if any
        active = self.get_wifi_connection()

        # If connected then disconnect and remove from NetworkManager
        if active:

            # Stop the active connection
            try:
                oradio_log.debug("Disconnect from: '%s'", active)
                # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.down(active)
            except Exception as ex_err:
                oradio_log.error("Failed to disconnect from '%s', error = %s", active, ex_err)
                # Inform controller of actual state and error
                if active == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_STOP
                else:
                    self.error = MESSAGE_WIFI_FAIL_DISCONNECT
                self.send_message()
                # Return fail, so caller can try to recover
                return False

            # Delete the active network from NetworkManager
            try:
                oradio_log.debug("Remove '%s' from NetworkManager", active)
                # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.delete(active)
            except Exception as ex_err:
                oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", active, ex_err)
                # Inform controller of actual state and error
                if active == ACCESS_POINT_SSID:
                    self.error = MESSAGE_WIFI_FAIL_AP_STOP
                else:
                    self.error = MESSAGE_WIFI_FAIL_DISCONNECT
                self.send_message()
                # Return fail, so caller can try to recover
                return False

            # Inform controller of actual state, no error
            self.send_message()

        # Return success, so caller can continue
        oradio_log.info("Disconnected from: '%s'", active)
        return True

    def access_point_start(self, force_ap=False):
        """
        Redirect DNS to internal
        Setup access point network
        """
        # Initialize
        self.error = None

        # Get active wifi connection, if any
        active = self.get_wifi_connection()

        # Done if access point is already active
        if active == ACCESS_POINT_SSID:
            oradio_log.debug("Access point already active")
            return True

        # Configure DNS redirection
        oradio_log.debug("Redirect DNS")
        cmd = "sudo bash -c 'echo \"address=/#/"+AP_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to configure DNS redirection, error: %s", cmd, error)
            # Inform controller of actual state and error
            self.error = MESSAGE_WIFI_FAIL_AP_START
            self.send_message()
            # Return fail, so caller can try to recover
            return False

        # If not connected setup access point
        # If connected, and force access point, then save connection and setup access point
        # If connected, and not force access point, then keep the active connection
        if active:
            if force_ap:
                # Keep current network connection when an access point is started
                self.saved_ssid = active
                oradio_log.info("Save ssid '%s' for reconnect on stop", self.saved_ssid)
            else:
                oradio_log.debug("Keep active wifi network '%s'", active)
                return True

        oradio_log.debug("Activate access point '%s'", ACCESS_POINT_SSID)
        # Setup and start acccess point
        if not self.wifi_connect(ACCESS_POINT_SSID, active):
            oradio_log.error("Failed to connect '%s'", ACCESS_POINT_SSID)
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
        self.error = None

        # Remove address redirection
        oradio_log.debug("Remove DNS redirection")
        cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to remove DNS redirection, error: %s", cmd, error)
            # Inform controller of actual state and error
            self.error = MESSAGE_WIFI_FAIL_AP_STOP
            self.send_message()
            # Return fail, so caller can try to recover
            return False

        # Only disconnect if access point is active
        if self.get_wifi_connection() == ACCESS_POINT_SSID:

            # Reconnect if any, ortherwise stop access point
            if self.saved_ssid:
                oradio_log.info("Restore connection to '%s'", self.saved_ssid)
                # Reconnect to saved wifi network
                if not self.wifi_connect(self.saved_ssid, None):
                    oradio_log.error("Failed to connect '%s'", ACCESS_POINT_SSID)
                    # wifi_connect function informs controller
                    # Return fail, so caller can try to recover
                    return False

            else:
                # Disconnect and remove the access point without sending message
                oradio_log.debug("Disconnect from '%s'", ACCESS_POINT_SSID)
                if not self._wifi_disconnect():
                    oradio_log.error("Failed to disconnect from '%s'", ACCESS_POINT_SSID)
                    # wifi_connect function informs controller
                    # Return fail, so caller can try to recover
                    return False

        # Clear saved ssid
        self.saved_ssid = None

        # Return success, so caller can continue
        return True

    def get_wifi_networks(self):
        """
        Get all available wifi networks, except Oradio access points
        :return networks ==> list of network ssid + if password required, sorted by strongest signal first
        """
        # initialize
        networks = []

        # Get available wifi networks
        try:
            oradio_log.debug("Get list of networks broadcasting their ssid")
            # nmcli.device.wifi(ifname: str = None, rescan: bool = None) -> List[DeviceWifi]
            wifi_list = nmcli.device.wifi(None, None)
        except Exception as ex_err:
            oradio_log.error("Failed to get wifi networks, error = %s", ex_err)
        else:
            oradio_log.debug("Remove '%s' from the list", ACCESS_POINT_SSID)
            for network in wifi_list:
                # Add unique, ignore own Access Point
                if (network.ssid != ACCESS_POINT_SSID) and (len(network.ssid) != 0) and (network.ssid not in networks):
                    networks.append(network.ssid)

        return networks

    def get_wifi_connection(self):
        """
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
        except Exception as ex_err:
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

        return network

    def _get_wifi_nm_connections(self):
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
        except Exception as ex_err:
            oradio_log.error("Failed to get connections from NetworkManager, error = %s", ex_err)
        else:
            # Inspect connections
            oradio_log.debug("Get wifi connections")
            for connection in result:
                # Only wifi connections
                if connection.conn_type == "wifi":
                    connections.append(connection.name)

        return connections

    def get_state(self):
        """
        Using threads for connect and access point we cannot use class variables
        """
        # Initialize
        state = STATE_WIFI_IDLE
        # Get active wifi connection, if any
        active = self.get_wifi_connection()
        # No connection: idle
        if not active:
            state = STATE_WIFI_IDLE
        # Connection to access point
        elif active == ACCESS_POINT_SSID:
            state = STATE_WIFI_ACCESS_POINT
        # Connection to wifi network
        elif active != ACCESS_POINT_SSID:
            # Connected: determine connection type
            if check_internet_connection():
                state = STATE_WIFI_INFRASTRUCTURE
            else:
                state = STATE_WIFI_LOCAL_NETWORK
        return state

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    def _remove_network_from_nm(network):
        """ Remove given network from NetworkManager """
        try:
            oradio_log.debug("Remove '%s' from NetworkManager", network)
            # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
            nmcli.connection.delete(network)
        except Exception as ex_err:
            oradio_log.error("Failed to remove '%s' from NetworkManager, error = %s", network, ex_err)
            return False
        return True

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
    wifi = WIFIService(message_queue)

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    InputSelection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-get wifi state\n"
                       " 2-list on air wifi networks\n"
                       " 3-list wifi networks in NetworkManager\n"
                       " 4-remove network from NetworkManager\n"
                       " 5-get active wifi connection\n"
                       " 6-connect to wifi network\n"
                       " 7-disconnect from wifi network\n"
                       " 8-start access point\n"
                       " 9-stop access point\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            FunctionNr = int(input(InputSelection))
        except ValueError:
            FunctionNr = -1

        # Execute selected function
        match FunctionNr:
            case 0:
                print("\nExiting test program...\n")
                break
            case 1:
                print(f"\nWiFi state: {wifi.get_state()}\n")
            case 2:
                print(f"\nRegistered wifi networks: {wifi.get_wifi_networks()}\n")
            case 3:
                print(f"\nDefined wifi connections: {wifi._get_wifi_nm_connections()}\n")   # pylint: disable=protected-access
            case 4:
                network = input("Enter network to remove from NetworkManager: ")
                if network:
                    print(f"\nRemoved {network} from NetworkManager: {_remove_network_from_nm(network)}\n")
                else:
                    print("\nNo network given\n")
            case 5:
                print(f"\nActive wifi connection: {wifi.get_wifi_connection()}\n")
            case 6:
                ssid = input("Enter SSID of the network to add: ")
                pswd = input("Enter password for the network to add: ")
                if ssid and pswd:
                    wifi.wifi_connect(ssid, pswd)
                    print(f"\nConnecting to ssid: '{ssid}', password: '{pswd}'. Check messages for result\n")
                else:
                    print("\nNo SSID and/or password given\n")
            case 7:
                print(f"\n_wifi_disconnect() returned '{wifi._wifi_disconnect()}'\n")       # pylint: disable=protected-access
            case 8:
                if wifi.access_point_start():
                    print("\nSetting up access point. Check messages for result\n")
                else:
                    print("\nFailed to setup access point\n")
            case 9:
                if wifi.access_point_stop():
                    print("\nWiFi access point stopped\n")
                else:
                    print("\nFailed to stop access point\n")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
