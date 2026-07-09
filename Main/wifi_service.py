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
    WifiService composes a WifiEventListener (built on ThreadTemplate, utilities.py)
    and exposes explicit start()/stop() methods, so the D-Bus listener thread is only
    started when the caller asks for it rather than as a side effect of construction.
    Documentation:
        https://networkmanager.dev/
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
        https://blogs.gnome.org/dcbw/2016/05/16/networkmanager-and-wifi-scans/
    Not supported:
        Connecting through a captive portal (detected but not handled).
        Connecting to VPN.
"""
from typing import Any
from threading import Thread, Lock
from subprocess import CalledProcessError
import nmcli
from dbus import SystemBus, Interface
from dbus.mainloop.glib import DBusGMainLoop
from dbus.exceptions import DBusException
from gi.repository import GLib

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from utilities import run_shell_script, ThreadTemplate, JOIN_TIMEOUT
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    WIFI_SOURCE,
    WIFI_CONNECTED,
    WIFI_DISCONNECTED,
    WIFI_ACCESS_POINT,
    WIFI_INCIDENT_DBUS,
    WIFI_INCIDENT_NMCLI,
    WIFI_INCIDENT_CONNECT,
    WIFI_INCIDENT_DISCONNECT,
)

##### GLOBAL constants ####################################
from constants import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
)

##### LOCAL constants #####################################
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

# Build the nmcli exception tuple dynamically so it stays correct if the
# nmcli package adds or renames exception classes in a future release.
# The starred expression unpacks nmcli_exceptions into a flat tuple suitable
# for use in an except clause (requires Python 3.11+).
nmcli_exceptions = tuple(
    exc for exc in vars(nmcli._exception).values()   # pylint: disable=protected-access
    if isinstance(exc, type) and issubclass(exc, Exception)
)

# nmcli._exception is a private module; if a future nmcli release
# renames or restructures it, the comprehension above could silently
# return an empty tuple, and _nmcli_try's except clause would then let
# every nmcli error propagate uncaught from all its call sites instead
# of being caught, logged, and reported. Fail fast at import time
# instead of failing mysteriously later.
if not nmcli_exceptions:
    oradio_log.error(
        "No nmcli exception classes discovered from nmcli._exception; "
        "nmcli error handling in _nmcli_try will not work as intended"
    )
    raise ImportError("Failed to discover nmcli exception classes for error handling")

# Module-level state shared across threads and processes
_saved_network = {"network": ""}    # Last successfully connected WiFi SSID
_saved_lock = Lock()                # Guards concurrent reads and writes across threads and processes

##### Helpers #############################################

def _set_saved_network(network) -> None:
    """
    Store the last active WiFi network in a process-safe manner.

    Stores the SSID string when network is truthy, or an empty string
    when network is falsy (None, empty string, etc.) to signal that
    no network is saved.

    Args:
        network: The SSID of the network to save, or a falsy value to clear it.
    """
    with _saved_lock:
        _saved_network["network"] = str(network) if network else ""

def _nmcli_try(func, *args, **kwargs) -> tuple[bool, Any | None]:
    """
    Call an nmcli function, catching all known nmcli and OS errors.

    On failure, logs the error and publishes WIFI_INCIDENT_NMCLI on the error
    bus so subscribers are notified without the caller needing to handle it.

    Args:
        func:     The nmcli callable to invoke.
        *args:    Positional arguments forwarded to func.
        **kwargs: Keyword arguments forwarded to func.

    Returns:
        A (success, result) tuple where success is True if the
        call completed without error and result holds the return value,
        or (False, None) on any failure.
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    # Exceptions built dynamically, so mypy can't verify it's a valid exception tuple statically
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:      # type: ignore[misc]
        oradio_log.error("nmcli call failed for %s: %s", func.__name__, ex_err)
        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_NMCLI))
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
class WifiEventListener(ThreadTemplate):
    """
    Singleton listener for WiFi state changes via NetworkManager D-Bus signals.

    Connects to the system D-Bus, locates the WiFi device managed by
    NetworkManager (DeviceType == 2, i.e. NM_DEVICE_TYPE_WIFI), and
    subscribes to the StateChanged signal on that device's interface.

    Internet reachability is determined by reading NetworkManager's own
    Connectivity property rather than making a separate probe, so no
    additional network round-trip is needed and the captive-portal case is
    detected correctly.

    Built on ThreadTemplate rather than a bare daemon Thread, so the
    listener gets restart support and crash detection for free:
        * setup()    - one-time D-Bus connection + signal subscription.
        * do_work()  - runs the GLib main loop. This is a single blocking
                        call rather than a quick repeated unit of work:
                        GLib.MainLoop.run() only returns once something
                        calls its quit(), which safe_stop() does below.
        * safe_stop()- overridden to call the GLib loop's quit() (so the
                        blocking do_work() call actually returns) before
                        delegating to ThreadTemplate's join-based safe_stop().

    If no WiFi device is found, or if the D-Bus connection fails, setup()
    raises. ThreadTemplate then logs and records the crash, and the three
    internal guards (_loop, _wifi_path, _nm_props) are left as None. All
    other modules can still operate normally; WiFi state changes will
    simply not be reported. Use the inherited crashed / exception
    properties to detect this from the outside.
    """

    def __init__(self) -> None:
        """
        Set up the listener's initial state.

        The singleton decorator ensures this constructor runs at most once
        per process. Does not start the background thread -- call
        safe_start() (typically via WifiService.start()) explicitly when
        ready to begin listening. All actual D-Bus/GLib work happens in
        setup(), which then runs on the worker thread.
        """
        super().__init__(name="WifiEventListener")

        # Guards; all three stay None if setup() fails at any point:
        #   _wifi_path — set once the WiFi device is located on the bus
        #   _nm_props  — set once the NM Properties interface is obtained
        #   _loop      — set once the GLib main loop object is created
        self.bus: SystemBus | None = None
        self._wifi_path: str | None = None
        self._nm_props: Interface | None = None
        self._loop: GLib.MainLoop | None = None

    def setup(self) -> None:
        """
        One-time D-Bus integration: connect to the bus, find the WiFi
        device, and subscribe to its StateChanged signal.

        Runs once per safe_start() (i.e. again on every restart), on the
        worker thread. Publishes WIFI_INCIDENT_DBUS and raises on any
        failure so ThreadTemplate.run() logs and records the crash; the
        guards above are left as None so other methods degrade gracefully
        (e.g. _get_connectivity() treats a None _nm_props as "no
        connectivity" rather than raising).
        """
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

            if not self._wifi_path:
                oradio_log.error("No wifi device found")
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))
                raise RuntimeError("No wifi device found")

            # Register the state-change callback for the specific WiFi device path.
            # Scoping to self._wifi_path avoids receiving spurious StateChanged
            # signals from other network devices (ethernet, VPN, etc.).
            self.bus.add_signal_receiver(
                self._wifi_state_changed,
                dbus_interface="org.freedesktop.NetworkManager.Device",
                signal_name="StateChanged",
                path=self._wifi_path,
            )

            # Built here; run (as do_work) on the worker thread started by safe_start().
            self._loop = GLib.MainLoop()

        except DBusException as ex_err:
            oradio_log.error("Failed to connect to NetworkManager D-Bus: %s", ex_err.get_dbus_message())
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))
        except OSError as ex_err:
            oradio_log.error("D-Bus connection error: %s", ex_err)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))
        except RuntimeError as ex_err:
            oradio_log.error(str(ex_err))
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))

        oradio_log.info("Wifi event listener started")

    def do_work(self) -> None:
        """
        Run the GLib main loop.

        Unlike a typical ThreadTemplate subclass, this is a single blocking
        call rather than a quick unit of work polled every interval:
        GLib.MainLoop.run() only returns once something calls its quit(),
        which safe_stop() below does. If run() ever returned on its own
        (e.g. quit() triggered from elsewhere) while _stop_event is still
        clear, ThreadTemplate's loop would call do_work() again -- a
        harmless self-healing restart of the event loop.
        """
        # setup() always runs (and sets self._loop) before ThreadTemplate
        # ever calls do_work(); the assert documents/enforces that
        # invariant for mypy, which can't see across the two methods.
        assert self._loop is not None, "do_work() called before setup() completed"
        self._loop.run()

    def safe_stop(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """
        Stop the listener: unblock the GLib loop, then join the thread.

        do_work() is parked inside self._loop.run() until something calls
        quit() on it, so the base implementation's _stop_event alone
        can't interrupt it. _stop_event is set here *before* calling
        quit() so that once run() returns, ThreadTemplate's run() loop
        sees the stop request immediately instead of calling do_work()
        (and restarting the GLib loop) again.

        Args:
            timeout: Max seconds to wait for the thread to exit.

        Returns:
            True if the thread finished within timeout, or if it was
            never started. False if it's still alive afterward or crashed.
        """
        self._stop_event.set()
        if self._loop is not None:
            self._loop.quit()
        return super().safe_stop(timeout)

    def _get_connectivity(self) -> int:
        """
        Return NetworkManager's current connectivity assessment.

        Reads the Connectivity property from the top-level NetworkManager
        D-Bus object. NM updates this value by probing a known URL after each
        connection attempt, so no additional network round-trip is made here.

        Returns:
            An integer connectivity code:

            * NM_CONNECTIVITY_NONE (1)    — no network at all
            * NM_CONNECTIVITY_PORTAL (2)  — behind a captive portal
            * NM_CONNECTIVITY_LIMITED (3) — IP connectivity, no internet route
            * NM_CONNECTIVITY_FULL (4)    — full internet access confirmed

            Returns NM_CONNECTIVITY_NONE on any D-Bus error so the
            caller can safely treat an unreadable state as no connectivity.
        """
        if self._nm_props is None:
            return NM_CONNECTIVITY_NONE
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
        device transitions between states. Only the three terminal states that
        require an application response are acted upon; intermediate states
        are ignored to avoid spurious messages during connection setup.

        On NM_CONNECTED, the active SSID is checked first to detect AP
        mode. For all other connections, NetworkManager's Connectivity
        property is read to distinguish full internet access from limited or
        no connectivity — without making a separate network probe.

        Args:
            new_state:  New NM device state code (int).
            _old_state: Previous NM device state code (unused).
            _reason:    NM reason code for the transition (unused).

        Wrapped in a broad except: an uncaught exception here would either
        crash the whole listener or silently drop just this one state
        transition, and neither would report anything to Incidents.
        """
        try:
            # Transient states such as PREPARE and CONFIG are excluded.
            if new_state not in (NM_CONNECTED, NM_DISCONNECTED, NM_FAILED):
                return

            if new_state == NM_CONNECTED:
                active = get_wifi_connection()
                if active == ACCESS_POINT_SSID:
                    # Connected to the Oradio's own access point (AP mode);
                    # connectivity check is not relevant here
                    oradio_log.debug("Publish wifi service message: %s", WIFI_ACCESS_POINT)
                    Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_ACCESS_POINT))
                else:
                    # Read NM's connectivity assessment — it has already probed
                    # for internet access so no separate round-trip is needed here
                    connectivity = self._get_connectivity()
                    if connectivity == NM_CONNECTIVITY_FULL:
                        # External network with confirmed internet access
                        oradio_log.debug("Publish wifi service message: %s", WIFI_CONNECTED)
                        Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_CONNECTED))
                    else:
                        # PORTAL, LIMITED, or NONE: IP may be assigned but
                        # there is no usable internet route
                        oradio_log.debug("Publish wifi service error: %s", WIFI_INCIDENT_CONNECT)
                        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_CONNECT))

            elif new_state == NM_DISCONNECTED:
                oradio_log.debug("Publish wifi service message: %s", WIFI_DISCONNECTED)
                Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_DISCONNECTED))

            else:   # NM_FAILED — NetworkManager could not complete the connection
                oradio_log.debug("Publish wifi service error: %s", WIFI_INCIDENT_CONNECT)
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_CONNECT))

        # Broad catch is intentional: this callback must never take down the GLib main
        # loop or the listener thread over a single bad signal delivery.
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Error handling WiFi StateChanged signal (new_state=%s): %s", new_state, ex_err)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))

class WifiService:
    """
    Manage WiFi connection state and expose connect/disconnect operations.

    Tracks four possible states: connected with internet, connected to the
    Oradio access point, disconnected, and connection failed. State changes
    are reported on the command message bus by the WifiEventListener
    singleton; this class handles the active operations that trigger them.

    Construction only sets up state; the background D-Bus listener thread
    is not started until start() is called.

    Note:
        The initial Commands.publish happens in start(), not __init__.
        Error states are never published at start time; they are only
        emitted in response to failed connection attempts.
    """
    def __init__(self) -> None:
        """
        Create (but do not start) the WifiEventListener singleton.

        Callers must call start() explicitly to begin monitoring D-Bus
        state changes, and may stop()/start() again later since the
        listener is restartable.
        """
        # Singleton D-Bus listener, shared across all WifiService instances.
        self.nm_listener = WifiEventListener()

    def start(self) -> None:
        """
        Start the background WiFi event listener thread and publish the
        current connection state.

        Blocks until the listener signals readiness, or until it crashes
        or times out. Idempotent: a no-op if already running.
        """
        if self.nm_listener.is_alive():
            oradio_log.debug("WiFi event listener thread already running")
            return

        if not self.nm_listener.safe_start():
            oradio_log.error("WiFi event listener thread failed to start")
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))
            return

        if self.nm_listener.crashed:
            oradio_log.error(
                "WiFi event listener thread crashed during startup: %s", self.nm_listener.exception,
            )
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DBUS))
            return

        oradio_log.info("WiFi event listener thread started")

        # Publish the current state immediately so subscribers don't have to
        # wait for the first state-change signal from NetworkManager
        Commands.publish(CommandMessage(WIFI_SOURCE, self.get_state()))

    def stop(self) -> None:
        """
        Signal the listener thread to stop and wait for it to exit.

        WifiEventListener.safe_stop() unblocks its own blocking GLib
        loop.run() call before joining.
        """
        self.nm_listener.safe_stop()

    def get_state(self) -> str:
        """
        Return the current WiFi connection state.

        Performs a direct check of the active connection rather than
        relying on any cached state.

        Returns:
            One of WIFI_DISCONNECTED, WIFI_ACCESS_POINT, or WIFI_CONNECTED.
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
        Add or update a network profile and start connecting in the background.

        Saves the current connection (if any, and not the AP) so it can be
        restored later, then starts a daemon Thread to activate the profile
        so the blocking nmcli call does not stall the caller.

        Args:
            ssid: SSID of the network to connect to.
            pswd: Password for the network; empty string for open networks.
        """
        active = get_wifi_connection()

        # Remember the last non-AP, non-empty connection so it can be restored later
        if active and active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            _set_saved_network(active)

        # Ensure the NetworkManager profile exists and has the correct credentials
        if not networkmanager_add(ssid, pswd):
            oradio_log.error("Publish wifi service error")
            return  # networkmanager_add already published the error; no point continuing

        # Offload the blocking connection attempt to a separate process
        Thread(target=self._wifi_connect_process, args=(ssid,), daemon=True).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_process(self, network) -> None:
        """
        Activate the given network profile (runs in a background thread).

        On failure the broken profile is removed from NetworkManager; on
        success WifiEventListener publishes the resulting WiFi state.

        Args:
            network: SSID of the NetworkManager connection profile to activate.
        """
        if not _wifi_up(network):
            # Activation failed; clean up the broken profile
            networkmanager_del(network)     # includes its own error logging
        else:
            # Connection is up; WifiEventListener will publish the new state
            oradio_log.info("Connected with '%s'", network)

    def wifi_disconnect(self) -> None:
        """
        Disconnect the currently active WiFi connection, if any.

        WifiEventListener will publish WIFI_DISCONNECTED once the
        state-change signal arrives. Does nothing if already disconnected.
        """
        active = get_wifi_connection()

        if active:
            if not _wifi_down(active):
                oradio_log.error("Failed to disconnect from '%s'", active)
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_INCIDENT_DISCONNECT))
            else:
                # WifiEventListener publishes the WIFI_DISCONNECTED state
                oradio_log.info("Disconnected from: '%s'", active)
        else:
            oradio_log.debug("Already disconnected")

class WifiScanner:
    """
    Fast WiFi network scanner backed by NetworkManager's scan cache.

    Always reads from the NetworkManager cache (fast, no radio delay), and
    triggers a background rescan after each read to keep the cache fresh.
    The Oradio access point SSID, empty SSIDs, and duplicate SSIDs are
    always excluded from results.
    """
    def __init__(self) -> None:
        """
        Prime the NetworkManager scan cache so the first get_active_ssids()
        call returns real results rather than an empty cache.
        """
        is_ok, _ = _nmcli_try(nmcli.device.wifi, None, True)
        if not is_ok:
            oradio_log.warning("Initial WiFi scan failed; NM cache may be empty")

    def _rescan(self) -> None:
        """
        Trigger a background WiFi rescan without blocking the caller, to
        keep the NM cache fresh for the next call to get_active_ssids.
        """
        # Daemon thread: exits automatically when the main process exits.
        Thread(target=_nmcli_try, args=(nmcli.device.wifi, None, True), daemon=True).start()

    def _parse_nmcli_output(self, nmcli_output) -> list:
        """
        Parse raw nmcli scan results into a deduplicated, sorted network list.

        Deduplicates by SSID, sorts by descending signal strength, and
        excludes empty SSIDs, duplicates, and the Oradio access point.

        Args:
            nmcli_output: List of nmcli network objects exposing ssid,
                          signal, and security attributes.

        Returns:
            A list of {"ssid": str, "type": "open" | "closed"} dicts,
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
        Return currently visible WiFi networks from the NM cache, and kick
        off an asynchronous background rescan to keep the cache fresh.

        Returns:
            A list of {"ssid": str, "type": "open" | "closed"} dicts
            ordered by descending signal strength, excluding the Oradio AP,
            empty SSIDs, and duplicates.
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
        self._rescan()

        return networks

# Module-level scanner instance; shared by all callers of get_wifi_networks()
_wifi_scanner = WifiScanner()

##### Public API ##########################################

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
        A list of {"ssid": str, "type": "open" | "closed"} dicts ordered
        by descending signal strength, excluding the Oradio AP, empty
        SSIDs, and duplicates.
    """
    return _wifi_scanner.get_active_ssids()

def get_wifi_connection() -> str | None:
    """
    Return the SSID of the currently active WiFi connection, if any.

    Queries the kernel directly via iw (with iwgetid as fallback) so the
    result reflects the live radio state independent of NM's view.

    Note: the interface name wlan0 is hardcoded; a different interface
    name will cause this to silently return None.

    Returns:
        The active SSID, or None if the command fails or no connection
        is active.
    """
    cmd = "iw dev wlan0 info | awk '/ssid/ {print $2}' || iwgetid -r wlan0"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.warning("Could not determine active WiFi connection: %s", response)
        return None
    return str(response)

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
        A list of connection name strings (one per WiFi profile), or an
        empty list if the query fails or none are configured.
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
    shared IPv4 configuration if one does not already exist. For all other
    SSIDs, adds a new profile or modifies the existing one with the
    supplied credentials.

    Args:
        network:  SSID of the network to configure.
        password: WPA passphrase; None or empty string for open networks.

    Returns:
        True if the profile was successfully added or updated, False otherwise.
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

def networkmanager_del(network) -> bool:
    """
    Remove a WiFi connection profile from NetworkManager.

    Called internally to clean up after a failed connection so no broken
    profile is left behind.

    Args:
        network: SSID of the connection profile to delete.

    Returns:
        True if deletion succeeded, False otherwise.
    """
    oradio_log.debug("Remove '%s' from NetworkManager", network)
    is_ok, _ = _nmcli_try(nmcli.connection.delete, network)
    return is_ok

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports
    from constants import RED, GREEN, YELLOW, NC    # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:    # pylint: disable=too-many-branches,too-many-statements
        """
        Run an interactive self-test menu for the WiFi service.

        Loops until the user selects quit (0). Covers the full public
        API: start/stop, scanning, connecting, disconnecting, AP mode,
        and direct NetworkManager profile management.
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Start WiFi monitor\n"
            " 2-Stop WiFi monitor\n"
            " 3-list wifi networks in NetworkManager\n"
            " 4-add network to NetworkManager\n"
            " 5-remove network from NetworkManager\n"
            " 6-list on air wifi networks\n"
            " 7-get wifi state and connection\n"
            " 8-connect to wifi network\n"
            " 9-start access point\n"
            " 10-disconnect from network\n"
            " 11-show WiFi event listener thread status\n"
            "Select: "
        )

        # Construct the service; WifiEventListener's D-Bus listener thread
        # is not started until wifi_service.start() is called (option 1).
        wifi_service = WifiService()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    wifi_service.stop()  # Ensure nothing is left running on exit
                    break
                case 1:
                    print("\nStarting WiFi monitor...\n")
                    wifi_service.start()
                case 2:
                    print("\nStopping WiFi monitor...\n")
                    wifi_service.stop()
                case 3:
                    print(f"\nNetworkManager wifi connections: {networkmanager_list()}\n")
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
                        if networkmanager_del(name):
                            print(f"\n{GREEN}'{name}' deleted from NetworkManager{NC}\n")
                        else:
                            print(f"\n{RED}Failed to delete '{name}' from NetworkManager{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    print(f"\nActive wifi networks: {get_wifi_networks()}\n")
                case 7:
                    wifi_state = wifi_service.get_state()
                    if wifi_state == WIFI_DISCONNECTED:
                        print(f"\nwifi state: '{wifi_state}'\n")
                    else:
                        print(f"\nwifi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                case 8:
                    name = input("Enter SSID of the network to connect to: ")
                    pswrd = input("Enter password (empty for open network): ")
                    if name:
                        wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 9:
                    print("\nStarting access point. Check messages for result\n")
                    wifi_service.wifi_connect(ACCESS_POINT_SSID, None)
                    print(f"\nConnecting with '{ACCESS_POINT_SSID}'. Check messages for result\n")
                case 10:
                    print("\nDisconnecting. Check messages for result\n")
                    wifi_service.wifi_disconnect()
                case 11:
                    listener = wifi_service.nm_listener
                    print(
                        f"\nis_alive={listener.is_alive()}, "
                        f"crashed={listener.crashed}, "
                        f"exception={listener.exception}"
                    )
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Subscribe to command and error topics so published messages are printed to console
    command_handler = DebugMessageHandler(Commands.subscribe())
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Commands.unsubscribe(command_handler.get_queue())
    Incidents.unsubscribe(incident_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    command_handler.stop()
    incident_handler.stop()

    print("\nExiting test program...\n")

    # Re-enable the duplicate-code check for any code that follows
    # pylint: enable=duplicate-code
