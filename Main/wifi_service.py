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
@summary:       WiFi connectivity service.
    Provides network scanning, connection management, access-point setup,
    and real-time state change notifications via the messaging bus.
    Internet reachability is determined by reading NetworkManager's built-in
    Connectivity property (no separate probe is made).
    Documentation:
        https://networkmanager.dev/
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
        https://blogs.gnome.org/dcbw/2016/05/16/networkmanager-and-wifi-scans/
    Not supported:
        Connecting through a captive portal (detected but not handled).
        Connecting to VPN.
"""
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
from oradio_utils import run_shell_script, safe_put  # has_internet removed: NM Connectivity property is used instead
from messaging import (
    CommandMessage,
    publish_command,
    ErrorMessage,
    publish_error,
    WIFI_SOURCE,
    WIFI_CONNECTED,
    WIFI_DISCONNECTED,
    WIFI_ACCESS_POINT,
    WIFI_ERROR_NMCLI,
    WIFI_ERROR_CONNECT,
    WIFI_ERROR_DISCONNECT,
)

##### GLOBAL constants ####################
from oradio_const import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
# NetworkManager device state codes
NM_DISCONNECTED = 30
NM_CONNECTED    = 100
NM_FAILED       = 120

# NetworkManager connectivity assessment codes.
# NM probes a known URL after each connection attempt and updates this value.
NM_CONNECTIVITY_NONE    = 1   # No network at all
NM_CONNECTIVITY_PORTAL  = 2   # Behind a captive portal (no open internet)
NM_CONNECTIVITY_LIMITED = 3   # IP connectivity, but no internet route
NM_CONNECTIVITY_FULL    = 4   # Full internet access confirmed

# How long (seconds) to wait for a background thread to respond
THREAD_TIMEOUT = 3

# Build the nmcli exception tuple dynamically so it stays correct if the
# nmcli package adds or renames exception classes in a future release.
nmcli_exceptions = tuple(
    exc for exc in vars(nmcli_exc).values()
    if isinstance(exc, type) and issubclass(exc, Exception)
)

# Module-level state shared across threads and processes
_saved_network = {"network": ""}    # Last successfully connected WiFi SSID
_saved_lock = Lock()                # Guards all access to _saved_network

##### Helpers #####

def _set_saved_network(network) -> None:
    """
    Store the last active WiFi network in a process-safe manner.

    Converts network to a string before storing so the value is always
    a plain str regardless of what the caller passes.  Stores an empty
    string if network is falsy (``None``, empty string, etc.).

    Args:
        network: The SSID of the network to save, or a falsy value to clear it.
    """
    with _saved_lock:
        _saved_network["network"] = str(network) if network else ""


def _nmcli_try(func, *args, **kwargs) -> tuple[bool, object | None]:
    """
    Call an nmcli function, catching all known nmcli and OS errors.

    On failure, logs the error and publishes WIFI_ERROR_NMCLI on the error
    bus so subscribers are notified without the caller needing to handle it.

    Args:
        func:     The nmcli callable to invoke.
        *args:    Positional arguments forwarded to func``.
        **kwargs: Keyword arguments forwarded to func``.

    Returns:
        A ``(success, result)`` tuple where success is True if the
        call completed without error and result holds the return value,
        or ``(False, None)`` on any failure.
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:  # * unpacks nmcli_exceptions into a flat exception tuple
        oradio_log.error("nmcli call failed for %s: %s", func.__name__, ex_err)
        publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_NMCLI))
        return False, None


def _wifi_up(network) -> bool:
    """
    Activate a NetworkManager connection by SSID.

    Args:
        network: SSID of the connection profile to bring up.

    Returns:
        True if activation succeeded, False otherwise.
    """
    oradio_log.debug("Activate '%s'", network)
    is_ok, _ = _nmcli_try(nmcli.connection.up, network)
    return is_ok


def _wifi_down(network) -> bool:
    """
    Deactivate a NetworkManager connection by SSID.

    Args:
        network: SSID of the active connection to bring down.

    Returns:
        True if deactivation succeeded, False otherwise.
    """
    oradio_log.debug("Disconnect from: '%s'", network)
    is_ok, _ = _nmcli_try(nmcli.connection.down, network)
    return is_ok

@singleton
class WifiEventListener():
    """
    Singleton listener for WiFi state changes via NetworkManager D-Bus signals.

    Connects to the system D-Bus, locates the WiFi device managed by
    NetworkManager (``DeviceType == 2``, i.e. NM_DEVICE_TYPE_WIFI``), and
    subscribes to the StateChanged signal on that device's interface.

    Internet reachability is determined by reading NetworkManager's own
    Connectivity property rather than making a separate probe, so no
    additional network round-trip is needed and the captive-portal case is
    detected correctly.

    A GLib main loop runs in a daemon thread to dispatch D-Bus signals
    asynchronously without blocking the main application thread.

    If no WiFi device is found, or if the D-Bus connection fails, the instance
    is left in an inert state (``_loop remains None``) and no signal
    subscription is made.  All other modules can still operate normally; WiFi
    state changes will simply not be reported.
    """

    def __init__(self) -> None:
        """
        Set up D-Bus integration and subscribe to WiFi state-change signals.

        Initialises the GLib main loop integration, connects to the system bus,
        iterates over NetworkManager devices to find the WiFi adapter, and
        registers _wifi_state_changed as the StateChanged signal
        receiver.  Also stores a D-Bus Properties interface on the top-level
        NetworkManager object so _wifi_state_changed can query the
        Connectivity property without re-connecting to the bus.

        Starts a daemon thread to run the GLib event loop, then verifies it is
        alive.  Publishes WIFI_ERROR_DBUS and returns early on any failure,
        leaving the instance inert.  The singleton decorator ensures this
        constructor runs at most once per process.
        """
        # Initialise guards; all three stay None/False if setup fails at any point:
        #   _wifi_path — set once the WiFi device is located on the bus
        #   _nm_props  — set once the NM Properties interface is obtained
        #   _loop      — set once the GLib main loop is created and running
        self._loop = None
        self._wifi_path = None
        self._nm_props = None

        try:
            # Required before the first SystemBus() call: integrates GLib's
            # event loop with dbus-python so signal callbacks are dispatched
            # on the GLib main loop thread rather than the calling thread.
            DBusGMainLoop(set_as_default=True)

            # Connect to the system-wide D-Bus (requires no special privileges)
            self.bus = SystemBus()

            # Obtain the top-level NetworkManager object and its primary interface
            nm_object = self.bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            nm_iface = Interface(nm_object, "org.freedesktop.NetworkManager")

            # Store a Properties interface on the NM object so the signal
            # callback can read the Connectivity property without reopening
            # the bus connection on every state change.
            self._nm_props = Interface(nm_object, "org.freedesktop.DBus.Properties")

            # Iterate devices and find the first WiFi adapter.
            # DeviceType == 2 corresponds to NM_DEVICE_TYPE_WIFI.
            for device in nm_iface.GetDevices():
                dev = self.bus.get_object("org.freedesktop.NetworkManager", device)
                dev_props = Interface(dev, "org.freedesktop.DBus.Properties")
                dev_type = dev_props.Get("org.freedesktop.NetworkManager.Device", "DeviceType")
                if dev_type == 2:
                    self._wifi_path = device
                    break

        except DBusException as ex_err:
            oradio_log.error("Failed to connect to NetworkManager D-Bus: %s", ex_err.get_dbus_message())
            publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_DBUS))
            return
        except OSError as ex_err:
            oradio_log.error("D-Bus connection error: %s", ex_err)
            publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_DBUS))
            return

        if not self._wifi_path:
            oradio_log.error("No wifi device found")
            publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_DBUS))
            return

        # Register the state-change callback for the specific WiFi device path.
        # Scoping to self._wifi_path avoids receiving spurious StateChanged
        # signals from other network devices (ethernet, VPN, etc.).
        self.bus.add_signal_receiver(
            self._wifi_state_changed,
            dbus_interface="org.freedesktop.NetworkManager.Device",
            signal_name="StateChanged",
            path=self._wifi_path,
        )

        # Start the GLib event loop in a daemon thread so it is torn down
        # automatically when the main process exits.
        self._loop = GLib.MainLoop()
        self.dbus_receiver = Thread(target=self._loop.run, daemon=True)
        self.dbus_receiver.start()

        # Verify the receiver thread actually started; it can fail silently
        # (e.g. GLib resource limit reached) without raising an exception.
        if not self.dbus_receiver.is_alive():
            oradio_log.error("D-Bus receiver thread failed to start: WiFi state changes will not be reported")
            publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_DBUS))
            return

        oradio_log.info("Wifi event listener started")

    def _get_connectivity(self) -> int:
        """
        Return NetworkManager's current connectivity assessment.

        Reads the Connectivity property from the top-level NetworkManager
        D-Bus object.  NM updates this value by probing a known URL after each
        connection attempt, so no additional network round-trip is made here.

        Returns:
            An integer connectivity code:

            * NM_CONNECTIVITY_NONE (1)``    — no network at all
            * NM_CONNECTIVITY_PORTAL (2)``  — behind a captive portal
            * NM_CONNECTIVITY_LIMITED (3)`` — IP connectivity, no internet route
            * NM_CONNECTIVITY_FULL (4)``    — full internet access confirmed

            Returns NM_CONNECTIVITY_NONE (1)`` on any D-Bus error so the
            caller can safely treat an unreadable state as no connectivity.
        """
        try:
            return int(self._nm_props.Get(
                "org.freedesktop.NetworkManager",
                "Connectivity",
            ))
        except DBusException as ex_err:
            oradio_log.error("Failed to read NM Connectivity property: %s", ex_err.get_dbus_message())
            return NM_CONNECTIVITY_NONE     # Treat unreadable state as no connectivity

    def _wifi_state_changed(self, new_state, _old_state, _reason) -> None:
        """
        Handle a StateChanged D-Bus signal from the WiFi device.

        Called by the GLib main loop thread whenever the NetworkManager WiFi
        device transitions between states.  Only the three terminal states that
        require an application response are acted upon; intermediate states
        (e.g. NM_DEVICE_STATE_PREPARE``, NM_DEVICE_STATE_CONFIG``) are
        silently ignored to avoid spurious messages during connection setup.

        On NM_CONNECTED``, the active SSID is checked first to detect AP
        mode.  For all other connections, NetworkManager's Connectivity
        property is read to distinguish full internet access from limited or
        no connectivity — without making a separate network probe.

        Args:
            new_state:  New NM device state code (``int``).
            _old_state: Previous NM device state code (unused; leading
                        underscore suppresses the pylint unused-argument warning).
            _reason:    NM reason code for the transition (unused; same
                        convention as _old_state``).
        """
        # Ignore transient intermediate states; only react to settled outcomes
        if new_state not in (NM_CONNECTED, NM_DISCONNECTED, NM_FAILED):
            return

        if new_state == NM_CONNECTED:
            active = get_wifi_connection()
            if active == ACCESS_POINT_SSID:
                # Connected to the Oradio's own access point (AP mode);
                # connectivity check is not relevant here
                oradio_log.debug("Publish wifi service message: %s", WIFI_ACCESS_POINT)
                publish_command(CommandMessage(WIFI_SOURCE, WIFI_ACCESS_POINT))
            else:
                # Read NM's connectivity assessment — it has already probed
                # for internet access so no separate round-trip is needed here
                connectivity = self._get_connectivity()
                if connectivity == NM_CONNECTIVITY_FULL:
                    # External network with confirmed internet access
                    oradio_log.debug("Publish wifi service message: %s", WIFI_CONNECTED)
                    publish_command(CommandMessage(WIFI_SOURCE, WIFI_CONNECTED))
                else:
                    # NM_CONNECTIVITY_PORTAL, NM_CONNECTIVITY_LIMITED, or NM_CONNECTIVITY_NONE:
                    # IP may be assigned but there is no usable internet route
                    oradio_log.debug("Publish wifi service error: %s", WIFI_ERROR_CONNECT)
                    publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_CONNECT))

        elif new_state == NM_DISCONNECTED:
            oradio_log.debug("Publish wifi service message: %s", WIFI_DISCONNECTED)
            publish_command(CommandMessage(WIFI_SOURCE, WIFI_DISCONNECTED))

        else:   # NM_FAILED — NetworkManager could not complete the connection
            oradio_log.debug("Publish wifi service error: %s", WIFI_ERROR_CONNECT)
            publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_CONNECT))

class WifiService():
    """
    Manage WiFi connection state and expose connect/disconnect operations.

    Tracks four possible states: connected with internet, connected to the
    Oradio access point, disconnected, and connection failed.  State changes
    are reported on the command message bus by the WifiEventListener
    singleton; this class handles the active operations that trigger them.
    """

    def __init__(self):
        """
        Initialise the WiFi service and publish the current connection state.

        Starts the WifiEventListener singleton (which begins monitoring
        D-Bus state changes in a background thread) and immediately publishes
        the current WiFi state so subscribers have an up-to-date view before
        any state-change signals arrive.
        """
        # Start the D-Bus listener; it runs its own daemon thread internally
        self.nm_listener = WifiEventListener()

        # Publish the current state immediately so subscribers don't have to
        # wait for the first state-change signal from NetworkManager
        publish_command(CommandMessage(WIFI_SOURCE, self.get_state()))

    def get_state(self) -> str:
        """
        Return the current WiFi connection state.

        Performs a direct check of the active connection rather than relying
        on any cached state.

        Returns:
            One of the WIFI_*`` command constants:
            WIFI_DISCONNECTED``, WIFI_ACCESS_POINT``, or WIFI_CONNECTED``.
        """
        active = get_wifi_connection()

        if not active:
            return WIFI_DISCONNECTED
        if active == ACCESS_POINT_SSID:
            # Active connection is the Oradio's own access point
            return WIFI_ACCESS_POINT
        return WIFI_CONNECTED

    def wifi_connect(self, ssid, pswd) -> None:
        """
        Add or update a network profile and start connecting in a background process.

        Saves the current connection (unless in AP mode) so it can be restored
        later.  Adds or modifies the NetworkManager profile for ssid``, then
        spawns a Process to call _wifi_connect_process so the blocking
        nmcli connection up call does not stall the main thread.

        State change notifications are published by WifiEventListener once
        the connection attempt settles.

        Args:
            ssid: SSID of the network to connect to.
            pswd: Password for the network; pass an empty string for open networks.
        """
        active = get_wifi_connection()

        # Remember the last non-AP connection so it can be restored later
        if active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            _set_saved_network(active)

        # Ensure the NetworkManager profile exists and has the correct credentials
        if not networkmanager_add(ssid, pswd):
            oradio_log.error("Publish wifi service error")
            return  # networkmanager_add already published the error; no point continuing

        # Offload the blocking connection attempt to a separate process
        Process(target=self._wifi_connect_process, args=(ssid,)).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_process(self, network) -> None:
        """
        Activate the given network profile (runs in a child process).

        Called by wifi_connect in a separate Process so the blocking
        nmcli connection up call does not stall the main thread.  If
        activation fails, the profile is removed from NetworkManager to avoid
        leaving a broken entry.  On success, WifiEventListener publishes
        the resulting WiFi state.

        Args:
            network: SSID of the NetworkManager connection profile to activate.
        """
        if not _wifi_up(network):
            # Activation failed; clean up the broken profile
            _networkmanager_del(network)    # includes its own error logging
        else:
            # Connection is up; WifiEventListener will publish the new state
            oradio_log.info("Connected with '%s'", network)

    def wifi_disconnect(self) -> None:
        """
        Disconnect the currently active WiFi connection, if any.

        If a connection is active, brings it down via NetworkManager.
        WifiEventListener will publish WIFI_DISCONNECTED once the
        state-change signal arrives.  Does nothing if already disconnected.
        """
        active = get_wifi_connection()

        if active:
            if not _wifi_down(active):
                oradio_log.error("Failed to disconnect from '%s'", active)
                publish_error(ErrorMessage(WIFI_SOURCE, WIFI_ERROR_DISCONNECT))
            else:
                # WifiEventListener publishes the WIFI_DISCONNECTED state
                oradio_log.info("Disconnected from: '%s'", active)
        else:
            oradio_log.debug("Already disconnected")

class WifiScanner:
    """
    Fast WiFi network scanner backed by NetworkManager's scan cache.

    Always reads from the NetworkManager cache (fast, no radio delay), and
    triggers a background rescan after each read to keep the cache fresh for
    the next call.  The Oradio access point SSID is always excluded from
    results.
    """
    def __init__(self):
        """
        Initialise the scanner by seeding the NetworkManager scan cache.

        Triggers an immediate scan so subsequent calls to get_active_ssids
        return real results rather than an empty cache.
        """
        # Prime the NM scan cache; result is discarded — we only care that
        # NM now has recent data available for the first get_active_ssids() call.
        _, _ = _nmcli_try(nmcli.device.wifi, None, True)

    def _async_rescan(self) -> None:
        """
        Trigger a background WiFi rescan without blocking the caller.

        Runs nmcli.device.wifi with rescan=True in a daemon thread so
        the NM cache is refreshed asynchronously for the next call to
        get_active_ssids``.
        """
        Thread(target=_nmcli_try, args=(nmcli.device.wifi, None, True), daemon=True).start()

    def _parse_nmcli_output(self, nmcli_output) -> list:
        """
        Parse raw nmcli scan results into a deduplicated, sorted network list.

        Deduplicates by SSID, sorts by descending signal strength, labels each
        network as ``"open"`` or ``"closed"`` based on its security setting,
        and excludes the Oradio access point SSID.

        Args:
            nmcli_output: List of nmcli network objects exposing ssid``,
                          signal``, and security attributes (all optional
                          via getattr with defaults).

        Returns:
            A list of ``{"ssid": str, "type": "open" | "closed"}`` dicts,
            ordered by descending signal strength.
        """
        seen_ssids = set()
        networks_formatted = []

        # Sort by signal strength descending; networks without a signal
        # attribute are treated as weakest (0) and appear at the end.
        sorted_networks = sorted(
            nmcli_output, key=lambda n: getattr(n, "signal", 0), reverse=True
        )

        for network in sorted_networks:
            ssid = getattr(network, "ssid", "")
            # Skip empty SSIDs, the Oradio access point, and already-seen SSIDs
            if ssid and ssid != ACCESS_POINT_SSID and ssid not in seen_ssids:
                seen_ssids.add(ssid)
                networks_formatted.append({
                    "ssid": ssid,
                    "type": "closed" if getattr(network, "security", False) else "open",
                })

        return networks_formatted

    def get_active_ssids(self) -> list:
        """
        Return a list of currently visible WiFi networks from the NM cache.

        Reads from the NetworkManager scan cache (no radio delay), then kicks
        off an asynchronous background rescan to keep the cache fresh.

        Returns:
            A list of ``{"ssid": str, "type": "open" | "closed"}`` dicts
            ordered by descending signal strength, excluding the Oradio AP.
        """
        oradio_log.debug("Scanning for wifi networks...")

        # Read from the NM cache without triggering a new scan (rescan=False)
        is_ok, nmcli_output = _nmcli_try(nmcli.device.wifi, None, False)
        if is_ok and nmcli_output:
            networks = self._parse_nmcli_output(nmcli_output)
        else:
            oradio_log.warning("No networks found in cached NM scan")
            networks = []

        # Refresh the cache in the background for the next call
        self._async_rescan()

        return networks

# Module-level scanner instance; shared by all callers of get_wifi_networks()
_wifi_scanner = WifiScanner()

#### Public API #####

def get_saved_network() -> str:
    """
    Return the last active WiFi network in a process-safe manner.

    Returns:
        The SSID of the last saved network, or an empty string if none has
        been saved yet or the saved value was cleared.
    """
    with _saved_lock:
        return _saved_network["network"]


def get_wifi_networks() -> list:
    """
    Return visible WiFi networks from the NetworkManager scan cache.

    Returns:
        A list of ``{"ssid": str, "type": "open" | "closed"}`` dicts ordered
        by descending signal strength, excluding the Oradio access point SSID.
    """
    return _wifi_scanner.get_active_ssids()


def get_wifi_connection() -> str | None:
    """
    Return the SSID of the currently active WiFi connection, if any.

    Queries the kernel directly via iw (with iwgetid as fallback)
    so the result reflects the live radio state independent of NM's view.

    Returns:
        The active SSID as a string, or None if the command fails or no
        connection is active.
    """
    cmd = "iw dev wlan0 info | awk '/ssid/ {print $2}' || iwgetid -r wlan0"
    result, response = run_shell_script(cmd)
    return str(response) if result else None


def get_wifi_password(network) -> str | None:
    """
    Return the stored password for a NetworkManager connection profile.

    Args:
        network: SSID of the connection profile as stored in NetworkManager.

    Returns:
        The password string, or None if the profile is not found or the
        command fails.
    """
    oradio_log.debug("Get wifi password")
    cmd = f"sudo nmcli -s -g 802-11-wireless-security.psk con show \"{network}\""
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get password for '%s', error: %s", cmd, network, response)
        return None
    return response


def networkmanager_list() -> list:
    """
    Return the SSIDs of all WiFi connection profiles stored in NetworkManager.

    Returns:
        A list of connection name strings (one per WiFi profile), or an empty
        list if the query fails or no WiFi profiles are configured.
    """
    oradio_log.debug("Get connections from NetworkManager")

    is_ok, result = _nmcli_try(nmcli.connection)

    if not is_ok or result is None:
        return []

    # Filter to WiFi-type connections only; other types (ethernet, VPN) are not relevant
    return [connection.name for connection in result if connection.conn_type == "wifi"]


def networkmanager_add(network, password=None) -> bool:
    """
    Add or update a WiFi connection profile in NetworkManager.

    For the Oradio access point SSID, creates an AP-mode profile with a
    shared IPv4 configuration if one does not already exist.  For all other
    SSIDs, adds a new profile or modifies the existing one with the supplied
    credentials.

    Args:
        network:  SSID of the network to configure.
        password: WPA passphrase; pass None or an empty string for open
                  (unsecured) networks.

    Returns:
        True if the profile was successfully added or updated, False
        otherwise.
    """
    # --- Access point profile ---
    if network == ACCESS_POINT_SSID:
        if ACCESS_POINT_SSID in networkmanager_list():
            oradio_log.debug("'%s' already in NetworkManager", ACCESS_POINT_SSID)
            return True

        oradio_log.debug("Add '%s' to NetworkManager", ACCESS_POINT_SSID)
        options = {
            "mode": "ap",
            "ssid": ACCESS_POINT_SSID,
            "ipv4.method": "shared",
            "ipv4.address": ACCESS_POINT_HOST + "/24",
        }
        is_ok, _ = _nmcli_try(nmcli.connection.add, "wifi", options, "*", ACCESS_POINT_SSID, False)
        return is_ok

    # --- Regular WiFi profile ---
    options = {"ssid": network}
    if password:
        oradio_log.debug("Use '%s' with password", network)
        options.update({
            "wifi-sec.key-mgmt": "wpa-psk",
            "wifi-sec.psk": password,
        })
    else:
        oradio_log.debug("Use '%s' without password", network)

    if network in networkmanager_list():
        # Profile exists; update credentials in place
        oradio_log.debug("Modify '%s' in NetworkManager", network)
        is_ok, _ = _nmcli_try(nmcli.connection.modify, network, options)
        return is_ok

    # Profile does not exist; create a new one
    oradio_log.debug("Add '%s' to NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.add, "wifi", options, "*", network, True)
    return is_ok


def _networkmanager_del(network) -> bool:
    """
    Remove a WiFi connection profile from NetworkManager.

    Private helper; called internally when a failed connection should be
    cleaned up to avoid leaving a broken profile in NetworkManager.

    Args:
        network: SSID of the connection profile to delete.

    Returns:
        True if deletion succeeded, False otherwise.
    """
    oradio_log.debug("Remove '%s' from NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.delete, network)
    return is_ok

# Stand-alone entry point
if __name__ == '__main__':

    # Imports only needed for the interactive self-test
    from messaging import Topic, subscribe_commands, subscribe_errors   # pylint: disable=ungrouped-imports,wrong-import-position
    from oradio_const import RED, GREEN, YELLOW, NC                     # pylint: disable=ungrouped-imports,wrong-import-position

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    def topic_handler(message, topic) -> None:
        """
        Print any message received on a subscribed message bus topic.

        Passed as a callback to subscribe_commands and subscribe_errors
        so that all bus traffic is visible during interactive testing.

        Args:
            message: The CommandMessage or ErrorMessage received.
            topic:   The bus topic on which the message arrived, used as a
                     label in the printed output.
        """
        print(f"[{topic}] - Message received: {message!r}")

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the WiFi service.

        Instantiates WifiService and loops until the user selects quit (0).
        Options cover the full public API: scanning, connecting, disconnecting,
        access-point mode, and direct NetworkManager profile management.
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-list wifi networks in NetworkManager\n"
            " 2-add network to NetworkManager\n"
            " 3-remove network from NetworkManager\n"
            " 4-list on air wifi networks\n"
            " 5-get wifi state and connection\n"
            " 6-connect to wifi network\n"
            " 7-start access point\n"
            " 8-disconnect from network\n"
            "Select: "
        )

        # Instantiate the service; WifiEventListener starts its daemon thread here
        wifi_service = WifiService()

        while True:
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                # Non-integer input; fall through to the default case
                function_nr = -1

            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    print(f"\nNetworkManager wifi connections: {networkmanager_list()}\n")
                case 2:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        if networkmanager_add(name, pswrd):
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
                    wifi_state = wifi_service.get_state()
                    if wifi_state == WIFI_DISCONNECTED:
                        print(f"\nwifi state: '{wifi_state}'\n")
                    else:
                        print(f"\nwifi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                case 6:
                    name = input("Enter SSID of the network to connect to: ")
                    pswrd = input("Enter password (empty for open network): ")
                    if name:
                        wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 7:
                    print("\nStarting access point. Check messages for result\n")
                    wifi_service.wifi_connect(ACCESS_POINT_SSID, None)
                    print(f"\nConnecting with '{ACCESS_POINT_SSID}'. Check messages for result\n")
                case 8:
                    print("\nDisconnecting. Check messages for result\n")
                    wifi_service.wifi_disconnect()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Subscribe to command and error topics before starting the service so no
    # messages published during initialisation are missed
    subscribe_commands(topic_handler, (Topic.COMMAND,))
    subscribe_errors(topic_handler, (Topic.ERROR,))

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
