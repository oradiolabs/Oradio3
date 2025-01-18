'''

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
'''
import nmcli
from multiprocessing import Process, Event, Queue

##### oradio modules ####################
import oradio_utils
from oradio_const import *

def get_wifi_networks():
    '''
    Get all available wifi networks, except Oradio access points
    :return networks ==> list of network ssid + if password required, sorted by strongest signal first
    '''
    # initialize
    networks = []

    # Get available wifi networks
    try:
        # nmcli.device.wifi(ifname: str = None, rescan: bool = None) -> List[DeviceWifi]
        wifi_list = nmcli.device.wifi(None, None)
    except Exception as ex_err:
        oradio_utils.logging("error", f"Failed to get wifi networks, error = {ex_err}")
    else:
        for network in wifi_list:
            # Add unique, ignore own Access Point
            if network.ssid != ACCESS_POINT_SSID and not any(network.ssid in d['ssid'] for d in networks):
                networks.append({"ssid": network.ssid, "security": bool(network.security)})

    return networks

def get_wifi_connection():
    '''
    Get active wifi connection
    :return connection ==> network ID | None
    '''
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
            # Ignore access point and only wifi connections with a device can be active
            if connection.conn_type == "wifi" and connection.device != "--":
                # Get connection details, inspect GENERAL.STATE
                details = nmcli.connection.show(connection.name)
                if details["GENERAL.STATE"] == "activated":
                    # Connection is wifi, has device and is activated
                    network = connection.name

    return network

def wifi_create(network, password):
    '''
    Create wifi network
    :param network ==> Identifier of wifi network to create
    :param password ==> Password of wifi network to create
    '''
    # initialize
    status = True

    # Check if password meets minimum length criterium
    if len(password) >= 8:

        # Add wifi network configuration
        try:
            options = {
                "ssid": network,
                "wifi-sec.key-mgmt": "wpa-psk",
                "wifi-sec.psk": password
            }
            # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
            nmcli.connection.add("wifi", options, "*", network, False)
        except Exception as ex_err:
            oradio_utils.logging("error", f"Failed to configure wifi network '{network}', error = {ex_err}")
            status = False
        else:
            oradio_utils.logging("success", f"Configured wifi network '{network}'")

    else:
        oradio_utils.logging("error", f"Failed to configure wifi network '{network}', error = Password needs to be 8 characters or more")
        status = False

    # Return status, so caller can recover if need be
    return status

def get_wifi_connections():
    '''
    Get defined connections from NetworkManager
    :return connections ==> list of network ids defined in NetworkManager
    '''
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
            # Only wifi connections
            if connection.conn_type == "wifi":
                connections.append(connection.name)

    return connections

def wifi_connect(connection):
    '''
    Connect to wifi network
    :param connection ==> Identifier of wifi network to connect to
    '''
    # initialize
    status = True

    # Connect to wifi network
    try:
        # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
        nmcli.connection.up(connection)
    except Exception as ex_err:
        oradio_utils.logging("error", f"Failed to connect to '{connection}', error = {ex_err}")
        status = False
    else:
        oradio_utils.logging("success", f"Wifi connected to '{connection}'")

    # Return status, so caller can recover if need be
    return status

def wifi_disconnect(*args, **kwargs):
    '''
    Disconnect from provided connection, or if no connection given, from active connection
    :param connection (optional) ==> identfies the network to disconnect from
    Do not remove the network from NetworkManager, so the Oradio will automatically reconnect when starting up in range of an already configured wifi network
    '''
    # initialize
    status = True

    # Get active connection
    active = get_wifi_connection()

    # All done if no active connection
    if active:

        # Get connection parameter, None if not provided
        connection = kwargs.get('connection', None)

        # Disconnect if the active connection is the provided connection
        if not connection or connection == active:
            try:
                # nmcli.connection.down(name: str, wait: int = None) -> None # Default timeout is 10 seconds
                nmcli.connection.down(active)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to disconnect from '{active}', error = {ex_err}")
                status = False
            else:
                oradio_utils.logging("success", f"Wifi disconnected from '{active}'")
    else:
        oradio_utils.logging("warning", "No active network to disconnect from")

    # Return status, so caller can recover if need be
    return status

def wifi_remove(connection):
    '''
    Remove wifi network from NetworkManager
    NOTE: the NetworkManager will disconnect if 'connection' is the active connection
    :param connection ==> NetworkManager connection identfier
    '''
    # initialize
    status = True

    # First disconnect
    if not wifi_disconnect():
        return False

    # Delete the connection
    try:
        # nmcli.connection.delete(name: str, wait: int = None) -> None # Default timeout is 10 seconds
        nmcli.connection.delete(connection)
    except Exception as ex_err:
        # Ignore removing a connection which was not configured
        if "access point does not exist" in str(ex_err):
            oradio_utils.logging("warning", f"Wifi {connection} did not exist, so it is succesfully 'removed'")
        else:
            oradio_utils.logging("error", f"Failed to remove '{connection}', error = {ex_err}")
            status = False
    else:
        oradio_utils.logging("success", f"Wifi '{connection}' removed")

    
    # Return status, so caller can recover if need be
    return status

def wifi_autoconnect(network, password):
    '''
    Create and connect to wifi network, setting to autoconnect
    :param network ==> Identifier of wifi network to create and connect to
    :param password ==> Password of wifi network to create
    '''
    # initialize
    status = True

    # Create network configuration
    if not wifi_create(network, password):
        status = False

    # Connect to network
    elif not wifi_connect(network):
        # Failed to connect: Remove network
        wifi_remove(network)
        status = False

    else:
        # Set to autoconnect
        try:
            # nmcli.connection.modify(name: str, options: ConnectionOptions) -> None
            nmcli.connection.modify(network, {"autoconnect": "yes"})
        except Exception as ex_err:
            oradio_utils.logging("error", f"Failed to modify autoconnect '{network}', error = {ex_err}")
            # Failed to modify: Remove network
            wifi_remove(network)
            status = False
        else:
            oradio_utils.logging("success", f"Wifi will autoconnect to '{network}'")

    # Return status, so caller can recover if need be
    return status

def access_point_start():
    '''
    Create the access point if not yet known
    Activate the access point if not yet active
    Leave with running access point
    '''
    # Check if access point is already active
    if get_wifi_connection() != ACCESS_POINT_NAME:

        # Check if access point is already defined
        if ACCESS_POINT_NAME not in get_wifi_connections():

            # Configure redirection (overwrite)
            cmd = "sudo bash -c 'echo \"address=/#/"+ACCESS_POINT_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
            if not oradio_utils.run_shell_script(cmd):
                oradio_utils.logging("error", f"Error during <{cmd}> to configure IP address redirection, error ={error}")
                return False
            else:
                oradio_utils.logging("success", f"Redirection to host '{ACCESS_POINT_HOST}' configured")

            # Create access point
            try:
                options = {
                    "mode": "ap",
                    "ssid": ACCESS_POINT_SSID,
                    "ipv4.method": "shared",
                    "ipv4.address": ACCESS_POINT_HOST+"/24"
                }
                # nmcli.connection.add(conn_type: str, options: Optional[ConnectionOptions] = None, ifname: str = "*", name: str = None, autoconnect: Bool = None) -> None
                nmcli.connection.add("wifi", options, "*", ACCESS_POINT_NAME, False)
            except Exception as ex_err:
                oradio_utils.logging("error", f"Failed to add access point '{ACCESS_POINT_NAME}' with SSID = {ACCESS_POINT_SSID}, error = {ex_err}")
                return False

        # Connect to the access point
        try:
            # nmcli.connection.up(name: str, wait: int = None) -> None # Default timeout is 90 seconds
            nmcli.connection.up(ACCESS_POINT_NAME)
        except Exception as ex_err:
            oradio_utils.logging("error", f"Failed to activate access point '{ACCESS_POINT_NAME}' with SSID = {ACCESS_POINT_SSID}, error = {ex_err}")
            return False
        else:
            oradio_utils.logging("success", f"Access point '{ACCESS_POINT_NAME}' with SSID = {ACCESS_POINT_SSID} is active")

    else:
        oradio_utils.logging("warning", f"Access point '{ACCESS_POINT_NAME}' already active")

    # Return status, so caller can recover if need be
    return True

def access_point_stop():
    '''
    Stop access point
    '''
    # initialize
    status = True

    # Remove the access point
    status = wifi_remove(ACCESS_POINT_NAME)

    # Remove address redirection
    cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
    if not oradio_utils.run_shell_script(cmd):
        oradio_utils.logging("error", f"Error during <{cmd}> to remove IP address redirection, error ={error}")
        status = False
    else:
        oradio_utils.logging("success", "IP address redirection removed")

    # Return status, so caller can recover if need be
    return status

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Check if monitoring is available, i.e. running in Oradio context
    import importlib.util
    system_monitoring = importlib.util.find_spec("system_monitoring")

    '''
    TODO: Move to oradio_utils to determine what to do: logging only, monitoring, ...
    # If monitoring is available then use it
    if system_monitoring:
        import logging.config
        from system_monitoring import system_monitor
        from oradio_data_collector import oradio_data_collector
        from settings import get_config

        # Initialize logging and monitoring
        logging.config.fileConfig(ORADIO_LOGGING_CONFIG)
        status, oradio_config = get_config()
        data_collector = oradio_data_collector()
        sys_monitor = system_monitor(oradio_config, data_collector)

        # No system checks
        sys_monitor.timer_off()
    '''

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-list on air wifi networks\n"
                       " 2-get active wifi connection\n"
                       " 3-register wifi network credentials\n"
                       " 4-list all registered wifi networks\n"
                       " 5-connect to registered wifi network\n"
                       " 6-disconnect from active wifi network\n"
                       " 7-remove registered wifi network\n"
                       " 8-create, connect and set autoconnect\n"
                       " 9-start access point\n"
                       " 10-stop access point\n"
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
                oradio_utils.logging("info", f"Wifi networks found: {get_wifi_networks()}")
                oradio_utils.logging("warning", f"NOTE: Oradio access points with SSID = {ACCESS_POINT_SSID} are ignored")
            case 2:
                oradio_utils.logging("info", f"Active wifi connection: {get_wifi_connection()}")
            case 3:
                ssid = input("Enter SSID of the network to add: ")
                pswd = input("Enter password for the network to add: ")
                if ssid and pswd:
                    oradio_utils.logging("info", f"wifi_create({ssid}, {pswd}) returned {wifi_create(ssid, pswd)}")
                else:
                    oradio_utils.logging("warning", "No SSID and/or password given")
            case 4:
                oradio_utils.logging("info", f"Defined wifi connections: {get_wifi_connections()}")
            case 5:
                ssid = input("Enter SSID of the network to connect to: ")
                if ssid:
                    oradio_utils.logging("info", f"wifi_connect({ssid}) returned {wifi_connect(ssid)}")
                else:
                    oradio_utils.logging("warning", "No network given")
            case 6:
                oradio_utils.logging("info", f"wifi_disconnect() returned {wifi_disconnect()}")
            case 7:
                ssid = input("Enter SSID of the network to remove: ")
                if ssid:
                    oradio_utils.logging("info", f"wifi_remove({ssid}) returned {wifi_remove(ssid)}")
                else:
                    oradio_utils.logging("warning", "No network given")
            case 8:
                ssid = input("Enter SSID of the network to add: ")
                pswd = input("Enter password for the network to add: ")
                if ssid and pswd:
                    oradio_utils.logging("info", f"wifi_autoconnect({ssid}, {pswd}) returned {wifi_autoconnect(ssid, pswd)}")
                else:
                    oradio_utils.logging("warning", "No SSID and/or password given")
            case 9:
                oradio_utils.logging("info", f"access_point_start() returned {access_point_start()}")
            case 10:
                oradio_utils.logging("info", f"access_point_stop() returned {access_point_stop()}")
            case _:
                print("\nPlease input a valid number\n")

    '''
    TODO: put check in oradio_utils
    # If monitoring: Stop monitoring
    if system_monitoring:
        sys_monitor.stop()
    '''
