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
    Provides network discovery, connection management, access-point setup,
    and real-time state change notifications via the messaging bus.
    Internet reachability is determined by reading NetworkManager's built-in
    Connectivity property (no separate probe is made).
    The list of available networks is accumulated from NetworkManager's
    AccessPointAdded/Removed D-Bus signals rather than by scanning on demand,
    so get_wifi_networks() is a pure cache read. It is built by a burst of
    scans at startup and kept accurate by a periodic keeper sweep, both of
    which run in the background while the radio is otherwise idle. Having
    the list ready in advance is what lets the access point start without
    delay: scanning once the access point is up stalls beaconing and can
    drop the very client that is reading the list, so the keeper stands
    down for as long as the access point is active.
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
# File exceeds pylint's default 1000-line module threshold. The bulk of it is
# one cohesive class (WifService) plus its own standalone test menu in
# __main__, matching every other module in this codebase (see utilities.py,
# wifi_service.py); splitting WifiService itself would hurt cohesion more than
# it would help, so the check is disabled here rather than restructured.
# pylint: disable=too-many-lines
from typing import Any
from time import sleep, monotonic
from threading import Thread, Lock, Event
from subprocess import CalledProcessError
import nmcli
from nmcli import ScanningNotAllowedException
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
    WIFI_DBUS_FAILED,
    WIFI_NMCLI_FAILED,
    WIFI_CONNECT_FAILED,
    WIFI_DISCONNECT_FAILED,
)

##### GLOBAL constants ####################################
from constants import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
)

##### LOCAL constants #####################################
# WiFi interface name. Used for device-level nmcli calls and the kernel
# queries in get_wifi_connection(); a different adapter name breaks both.
WIFI_INTERFACE = "wlan0"

# D-Bus names. Collected here because the access-point tracking below uses
# several of them repeatedly; keeping them as constants avoids typo-prone
# literals scattered through the listener.
NM_BUS_NAME        = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH     = "/org/freedesktop/NetworkManager"
NM_IFACE           = "org.freedesktop.NetworkManager"
NM_DEVICE_IFACE    = "org.freedesktop.NetworkManager.Device"
NM_WIRELESS_IFACE  = "org.freedesktop.NetworkManager.Device.Wireless"
NM_AP_IFACE        = "org.freedesktop.NetworkManager.AccessPoint"
DBUS_PROPS_IFACE   = "org.freedesktop.DBus.Properties"

# NM_DEVICE_TYPE_WIFI: value of the Device DeviceType property for WiFi adapters
NM_DEVICE_TYPE_WIFI = 2

# NM_802_11_AP_FLAGS_PRIVACY: access point requires a key/encryption.
# An AP counts as open only when this bit is clear *and* it advertises
# neither WPA nor RSN, so all three are checked together.
NM_AP_FLAGS_PRIVACY = 0x1

# Startup scan burst, run in the background as soon as the listener is up
# (see WifiService._build_network_list).
# A single scan sweep regularly misses access points: the radio can be on
# another channel when an AP beacons, and 5 GHz DFS channels need a longer
# passive dwell. NetworkManager's access-point list is cumulative, so each
# extra sweep can only add.
#
# Sweeps are paced by waiting for NetworkManager to report each scan
# complete, not by a fixed interval: a sweep takes around nine seconds on
# this hardware, so requests issued every three seconds produced one scan
# rather than three, and the per-sweep gain figures were meaningless.
AP_SCAN_SWEEPS = 4              # Number of completed sweeps in the startup burst
SCAN_COMPLETE_TIMEOUT = 20.0    # Max seconds to wait for one sweep to complete
SCAN_POLL_INTERVAL = 0.5        # Seconds between LastScan checks while waiting

# Keeper scan interval. NetworkManager scans by itself while the device is
# disconnected, but largely stops once associated -- which is the Oradio's
# normal state -- so without this the list slowly ages out exactly when it is
# least being refreshed. One sweep every interval keeps it accurate at the
# cost of a few hundred milliseconds off-channel, which the audio buffer
# absorbs. Skipped while hosting the access point, where a scan stalls
# beaconing and can drop the connected client.
AP_KEEPER_INTERVAL = 120  # Seconds between keeper sweeps

# Maximum seconds to wait for the startup burst when the access point is
# requested before it has finished (button pressed seconds after power-on).
# Now that sweeps are paced by completion rather than a fixed interval, the
# burst is AP_SCAN_SWEEPS real sweeps at roughly nine seconds each (~27s),
# not the ~10s it appeared to be when three requests were collapsing into one
# scan. Must stay comfortably below WIFI_STATE_TIMEOUT in web_service.py,
# which bounds the same wait from the caller's side: if that expires first,
# the web service reports a failed start for an access point that is merely
# waiting and about to come up.
AP_LIST_READY_TIMEOUT = 40.0

# Waiting for NetworkManager to appear at startup (see WifiService.start).
# oradio_control starts after basic.target, deliberately ahead of the network
# being ready, and the listener is started as early as possible so its scan
# burst finishes before anyone can press the button. Measured margin between
# NM claiming its D-Bus name and the listener starting was under four seconds,
# so rather than losing the listener for the whole boot when that margin goes
# negative, wait for NM and start then.
NM_WAIT_TIMEOUT  = 60.0   # Max seconds to wait for NM to claim its bus name
NM_POLL_INTERVAL = 1.0    # Seconds between availability checks while waiting

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

# Must run before the first SystemBus() call anywhere in this process.
# dbus-python binds a connection to whatever main loop is default at the time
# the connection is constructed, and SystemBus() returns a shared connection.
# nm_available() below opens that connection during WifiService.start(), which
# is earlier than WifiEventListener.setup(); without this, the shared
# connection would be cached without main-loop integration and the listener
# would silently never receive a single StateChanged or AccessPointAdded
# signal. setup() calls this again, which is harmless: it is idempotent.
DBusGMainLoop(set_as_default=True)

def nm_available() -> bool:
    """
    Report whether NetworkManager is running and owns its D-Bus name.

    NetworkManager.service is a Type=dbus unit declaring NM_BUS_NAME, so
    ownership of that name is the authoritative "NM is up and serving"
    signal -- the same condition that must hold for
    WifiEventListener.setup() to succeed.

    Cheap: one call on the already-open shared system bus, with no
    subprocess and no radio activity.

    Returns:
        True if the NetworkManager daemon holds NM_BUS_NAME on the system
        bus, False if it does not or if the system bus is unreachable.
    """
    try:
        return bool(SystemBus().name_has_owner(NM_BUS_NAME))
    except (DBusException, OSError) as ex_err:
        # Debug, not error: an absent bus is an expected early-boot state
        # here, not a fault worth reporting on the incident bus.
        oradio_log.debug("System D-Bus not reachable: %s", ex_err)
        return False

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

def _nmcli_try(func, *args, ignore=(), **kwargs) -> tuple[bool, Any | None]:
    """
    Call an nmcli function, catching all known nmcli and OS errors.

    On failure, logs the error and publishes WIFI_NMCLI_FAILED on the error
    bus so subscribers are notified without the caller needing to handle it.

    Args:
        func:     The nmcli callable to invoke.
        *args:    Positional arguments forwarded to func.
        ignore:   Exception classes treated as a benign non-result: logged at
                  debug and reported as failure, but with no error logged and
                  no incident published. Defaults to an empty tuple, which
                  never matches, so existing callers are unaffected.
        **kwargs: Keyword arguments forwarded to func.

    Returns:
        A (success, result) tuple where success is True if the
        call completed without error and result holds the return value,
        or (False, None) on any failure.
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    # Must precede the general clause below, which would otherwise match first
    except ignore as ex_err:
        oradio_log.debug("nmcli call declined for %s: %s", func.__name__, ex_err)
        return False, None
    # Exceptions built dynamically, so mypy can't verify it's a valid exception tuple statically
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:      # type: ignore[misc]
        oradio_log.error("nmcli call failed for %s: %s", func.__name__, ex_err)
        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_NMCLI_FAILED))
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

        # Accumulated view of the WiFi neighbourhood, maintained from
        # AccessPointAdded/Removed signals and read by get_wifi_networks().
        # Keyed by D-Bus object path rather than SSID: paths are unique per
        # BSSID, so removals are unambiguous and one SSID served by several
        # access points is tracked correctly. Deduplication by SSID happens
        # at read time in get_access_points().
        self._access_points: dict[str, dict] = {}
        self._ap_lock = Lock()      # Guards _access_points against the GLib thread

        # Set once the startup scan burst has finished. Lives here, on the
        # singleton, rather than on WifiService: oradio_control, web_service
        # and rms_service each construct a WifiService, so a per-instance
        # flag would be set on one object and read on the other, and the
        # access point path would always wait out its full timeout.
        # The singleton decorator runs this __init__ exactly once per
        # process, so neither this nor _access_points is ever reset by a
        # later construction.
        self.list_ready = Event()

    def setup(self) -> None:
        """
        One-time D-Bus integration: connect to the bus, find the WiFi
        device, and subscribe to its StateChanged signal.

        Runs once per safe_start() (i.e. again on every restart), on the
        worker thread. Publishes WIFI_DBUS_FAILED and raises on any
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
            nm_object = self.bus.get_object(NM_BUS_NAME, NM_OBJECT_PATH)
            nm_iface = Interface(nm_object, NM_IFACE)

            # Store a Properties interface on the NM object so the signal
            # callback can read the Connectivity property without reopening
            # the bus connection on every state change.
            self._nm_props = Interface(nm_object, DBUS_PROPS_IFACE)

            # Iterate devices and find the first WiFi adapter
            for device in nm_iface.GetDevices():
                dev = self.bus.get_object(NM_BUS_NAME, device)
                dev_props = Interface(dev, DBUS_PROPS_IFACE)
                dev_type = dev_props.Get(NM_DEVICE_IFACE, "DeviceType")
                if dev_type == NM_DEVICE_TYPE_WIFI:
                    self._wifi_path = device
                    break

            if not self._wifi_path:
                raise RuntimeError("No wifi device found")

            # Register the state-change callback for the specific WiFi device path.
            # Scoping to self._wifi_path avoids receiving spurious StateChanged
            # signals from other network devices (ethernet, VPN, etc.).
            self.bus.add_signal_receiver(
                self._wifi_state_changed,
                dbus_interface=NM_DEVICE_IFACE,
                signal_name="StateChanged",
                path=self._wifi_path,
            )

            # Track the access points NetworkManager discovers. NM scans on its
            # own whenever the device is idle or disconnected, so subscribing is
            # enough to accumulate the neighbourhood over time without this
            # module ever requesting a scan. That matters because scanning is
            # impossible while hosting the access point and disruptive to
            # playback while associated.
            for signal_name, handler in (
                ("AccessPointAdded",   self._access_point_added),
                ("AccessPointRemoved", self._access_point_removed),
            ):
                self.bus.add_signal_receiver(
                    handler,
                    dbus_interface=NM_WIRELESS_IFACE,
                    signal_name=signal_name,
                    path=self._wifi_path,
                )

            # Signals only report changes from now on, so anything NM already
            # knows about would never be seen. Seed from the current property
            # value; subscription happens first so an access point discovered
            # between these two steps arrives as a signal instead of being lost.
            self._seed_access_points()

            # Keep the list accurate for as long as the loop runs. Registered
            # on the same main context the loop below uses, so it needs no
            # thread of its own and stops automatically when the loop quits.
            GLib.timeout_add_seconds(AP_KEEPER_INTERVAL, self._keeper_sweep)

            # Built here; run (as do_work) on the worker thread started by safe_start().
            self._loop = GLib.MainLoop()

        except DBusException as ex_err:
            oradio_log.error("Failed to connect to NetworkManager D-Bus: %s", ex_err.get_dbus_message())
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            raise
        except OSError as ex_err:
            oradio_log.error("D-Bus connection error: %s", ex_err)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            raise
        except RuntimeError as ex_err:
            oradio_log.error(str(ex_err))
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            raise

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
            return int(self._nm_props.Get(NM_IFACE, "Connectivity"))
        except DBusException as ex_err:
            oradio_log.error("Failed to read NM Connectivity property: %s", ex_err.get_dbus_message())
            return NM_CONNECTIVITY_NONE     # Treat unreadable state as no connectivity

    def _seed_access_points(self) -> None:
        """
        Load the access points NetworkManager already knows about.

        Called once from setup(), immediately after subscribing to the
        AccessPointAdded/Removed signals, because those signals carry only
        subsequent changes.
        """
        try:
            wifi_props = Interface(
                self.bus.get_object(NM_BUS_NAME, self._wifi_path), DBUS_PROPS_IFACE
            )
            ap_paths = wifi_props.Get(NM_WIRELESS_IFACE, "AccessPoints")
        except DBusException as ex_err:
            # Not fatal: the list simply starts empty and fills from signals.
            oradio_log.warning("Could not read known access points: %s", ex_err.get_dbus_message())
            return

        for ap_path in ap_paths:
            self._access_point_added(ap_path)

        # Access points, not networks: one SSID is commonly served by several
        # (2.4 and 5 GHz radios, mesh nodes), so the network count is lower.
        oradio_log.debug(
            "Seeded %d access points (%d networks)", len(ap_paths), len(self.get_access_points())
        )

    def _access_point_added(self, ap_path) -> None:
        """
        Record an access point NetworkManager has discovered.

        Called by the GLib main loop thread on every AccessPointAdded signal,
        and once per known access point from _seed_access_points().

        Args:
            ap_path: D-Bus object path of the new access point.
        """
        try:
            ap_props = Interface(
                self.bus.get_object(NM_BUS_NAME, ap_path), DBUS_PROPS_IFACE
            ).GetAll(NM_AP_IFACE)
        except DBusException:
            # The access point can disappear between the signal and this read;
            # there is simply nothing to record.
            return

        # Ssid is a byte array, not a string, and is not guaranteed to be valid
        # UTF-8: a hidden network advertises an empty one, and a misconfigured
        # access point can advertise arbitrary bytes.
        ssid = bytes(ap_props.get("Ssid", b"")).decode("utf-8", errors="replace")

        # Skip hidden networks and the Oradio's own access point
        if not ssid or ssid == ACCESS_POINT_SSID:
            return

        secured = bool(
            ap_props.get("Flags", 0) & NM_AP_FLAGS_PRIVACY
            or ap_props.get("WpaFlags", 0)
            or ap_props.get("RsnFlags", 0)
        )

        with self._ap_lock:
            self._access_points[str(ap_path)] = {
                "ssid":   ssid,
                "type":   "closed" if secured else "open",
                "signal": int(ap_props.get("Strength", 0)),
            }

    def _access_point_removed(self, ap_path) -> None:
        """
        Forget an access point NetworkManager has aged out.

        Ignored while hosting the Oradio access point: no scanning is possible
        in that mode, so NM keeps ageing entries out with nothing to refresh
        them. Honouring removals would make the network list shown to the
        connected client quietly shrink the longer they take to choose.

        Args:
            ap_path: D-Bus object path of the departed access point.
        """
        if get_wifi_connection() == ACCESS_POINT_SSID:
            return

        with self._ap_lock:
            self._access_points.pop(str(ap_path), None)

    def get_access_points(self) -> list:
        """
        Return the accumulated access points, strongest first.

        Deduplicates by SSID, keeping the strongest signal where the same
        network is served by several access points. Reads cached state only:
        no scan, no subprocess, and therefore safe to call while the Oradio
        access point is serving a client.

        Returns:
            A list of {"ssid": str, "type": "open" | "closed"} dicts ordered
            by descending signal strength.
        """
        with self._ap_lock:
            access_points = list(self._access_points.values())

        strongest: dict[str, dict] = {}
        for access_point in access_points:
            ssid = access_point["ssid"]
            if ssid not in strongest or access_point["signal"] > strongest[ssid]["signal"]:
                strongest[ssid] = access_point

        return [
            {"ssid": ap["ssid"], "type": ap["type"]}
            for ap in sorted(strongest.values(), key=lambda ap: ap["signal"], reverse=True)
        ]

    def request_scan(self) -> bool:
        """
        Ask NetworkManager to scan for access points.

        Goes through nmcli rather than the Device.Wireless RequestScan D-Bus
        method: that method is gated by the polkit action
        org.freedesktop.NetworkManager.wifi.scan, which is granted to active
        local sessions only. oradio_control runs as a systemd system service
        and therefore has no session, so every direct call fails with
        "not authorized". The nmcli package invokes the binary under sudo,
        which is authorized.

        Returns as soon as NM accepts the request; the scan itself completes
        in the background and its results arrive as AccessPointAdded signals.

        Returns:
            True if NM accepted the request, False if it declined or failed.
        """
        # ScanningNotAllowedException means a scan is already running or one
        # finished very recently. Both mean scanning is happening, which is
        # the point, so it warrants neither an error nor an incident.
        is_ok, _ = _nmcli_try(nmcli.device.wifi_rescan, ignore=(ScanningNotAllowedException,))
        return is_ok

    def last_scan(self) -> int | None:
        """
        Return NetworkManager's LastScan timestamp for the WiFi device.

        The value is CLOCK_BOOTTIME milliseconds at the point the last scan
        completed, or -1 if no scan has ever completed. Only used to detect
        that a scan has finished, by watching for the value to change, so
        the clock it is measured against does not matter.

        Returns:
            The raw LastScan value, or None if it cannot be read.
        """
        if self._wifi_path is None:
            return None
        try:
            return int(
                Interface(
                    self.bus.get_object(NM_BUS_NAME, self._wifi_path), DBUS_PROPS_IFACE
                ).Get(NM_WIRELESS_IFACE, "LastScan")
            )
        except DBusException:
            return None

    def scan_and_wait(self, timeout: float = SCAN_COMPLETE_TIMEOUT) -> bool:
        """
        Request a scan and wait for NetworkManager to report it complete.

        Requesting scans on a fixed interval does not work: a full sweep
        takes several seconds (5 GHz DFS channels must be dwelt on
        passively), and a request issued while one is running is either
        refused or folded into it. Measured on Raspberry Pi hardware, three
        requests three seconds apart produced a single scan, not three.
        Waiting for LastScan to advance instead paces each request behind
        the previous result, so a sweep is genuinely a sweep, and adapts
        automatically to however long the hardware takes.

        Args:
            timeout: Maximum seconds to wait for completion.

        Returns:
            True if a scan completed within timeout, False on timeout or if
            LastScan cannot be read.
        """
        baseline = self.last_scan()
        self.request_scan()

        if baseline is None:
            # No LastScan to watch (device path missing, or property
            # unreadable): fall back to a fixed wait so the caller still
            # paces itself rather than spinning.
            sleep(SCAN_POLL_INTERVAL)
            return False

        deadline = monotonic() + timeout
        while monotonic() < deadline:
            sleep(SCAN_POLL_INTERVAL)
            if self.last_scan() != baseline:
                return True

        oradio_log.debug("Scan did not complete within %.0fs", timeout)
        return False

    def _keeper_sweep(self) -> bool:
        """
        Periodic scan that keeps the access point list accurate.

        Runs on the GLib main loop thread. NetworkManager scans on its own
        while the device is disconnected but largely stops once associated,
        so without this the list ages out during exactly the long connected
        periods where nothing else refreshes it.

        Returns:
            True, so GLib keeps rescheduling it. Returning False would
            cancel the timeout permanently.
        """
        # Never while hosting the access point: a scan there stalls beaconing
        # and can drop the client currently reading the list.
        if get_wifi_connection() == ACCESS_POINT_SSID:
            return True

        before = len(self.get_access_points())
        self.request_scan()
        # Logged at debug and reported next sweep: the scan is asynchronous,
        # so its results are not in the list yet at this point.
        oradio_log.debug("Keeper sweep requested (%d networks known)", before)
        return True

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
                        oradio_log.debug("Wifi connected to internet")
                        Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_CONNECTED))
                    else:
                        # PORTAL, LIMITED, or NONE: IP may be assigned but
                        # there is no usable internet route
                        oradio_log.debug("Wifi not connected to internet")
                        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_CONNECT_FAILED))

            elif new_state == NM_DISCONNECTED:
                oradio_log.debug("Wifi disconnected")
                Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_DISCONNECTED))

            else:   # NM_FAILED — NetworkManager could not complete the connection
                oradio_log.debug("Wifi could not complete connection: %s", new_state)
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_CONNECT_FAILED))

        # Broad catch is intentional: this callback must never take down the GLib main
        # loop or the listener thread over a single bad signal delivery.
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Error handling WiFi StateChanged signal (new_state=%s): %s", new_state, ex_err)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))

@singleton
class WifiService:
    """
    Manage WiFi connection state and expose connect/disconnect operations.

    Tracks four possible states: connected with internet, connected to the
    Oradio access point, disconnected, and connection failed. State changes
    are reported on the command message bus by the WifiEventListener
    singleton; this class handles the active operations that trigger them.

    Construction only sets up state; the background D-Bus listener thread
    is not started until start() is called.

    Singleton, because oradio_control, web_service and rms_service each
    construct one and they must not diverge: the network list, its
    readiness flag and the listener thread describe one radio, not one per
    caller. Any state added here would otherwise be written by whichever
    module acted and read as absent by the others -- which is precisely how
    the access point path came to wait out its full timeout on a list that
    had been built minutes earlier.

    Note:
        The initial Commands.publish happens in start(), not __init__.
        Error states are never published at start time; they are only
        emitted in response to failed connection attempts.
    """
    def __init__(self) -> None:
        """
        Create (but do not start) the WifiEventListener singleton.

        The singleton decorator ensures this constructor runs at most once
        per process. Callers must call start() explicitly to begin
        monitoring D-Bus state changes, and may stop()/start() again later
        since the listener is restartable.
        """
        # Singleton D-Bus listener; the same instance this class is bound to.
        self.nm_listener = WifiEventListener()

        # Set by stop(), so a deferred start still waiting for NetworkManager
        # aborts instead of bringing the listener up after shutdown.
        self._stopping = Event()

    def start(self, wait: float = NM_WAIT_TIMEOUT) -> None:
        """
        Start the background WiFi event listener thread and publish the
        current connection state.

        If NetworkManager is already up this behaves exactly as before:
        the listener starts synchronously and this returns once it is
        running. If NetworkManager is not up yet, starting now would only
        make setup() fail on D-Bus and publish a misleading WIFI_DBUS_FAILED,
        losing the listener -- and with it all state reporting and the
        network list -- for the rest of the boot. In that case the start is
        handed to a background thread that waits for NM to appear.

        oradio_control starts after basic.target and the listener is started
        as early as the module initialisation allows, so the margin over NM
        claiming its bus name is only a few seconds and not guaranteed.
        Waiting turns "started too early" from a lost boot into a short delay.

        Idempotent: a no-op if the listener is already running.

        Args:
            wait: Seconds to keep waiting for NetworkManager in the
                  background. Pass 0 to skip starting entirely when NM is
                  absent, which suits tests and stand-alone runs.
        """
        if self.nm_listener.is_alive():
            oradio_log.debug("WiFi event listener thread already running")
            return

        # A previous stop() may have set this; clear it so a restart works
        self._stopping.clear()

        if nm_available():
            self._start_listener()
            return

        if wait <= 0:
            oradio_log.info("NetworkManager not running; WiFi listener not started")
            return

        oradio_log.info("NetworkManager not up yet; deferring WiFi listener start")
        # Daemon thread: exits automatically when the process does.
        Thread(target=self._start_when_nm_ready, args=(wait,), daemon=True).start()

    def _start_when_nm_ready(self, timeout) -> None:
        """
        Wait for NetworkManager to appear, then start the listener.

        Runs on a background thread. Polls rather than watching D-Bus
        NameOwnerChanged, because receiving that signal would itself need a
        running GLib main loop -- which is what the listener provides and is
        precisely what does not exist yet at this point.

        Args:
            timeout: Maximum seconds to wait before giving up and reporting.
        """
        started = monotonic()
        deadline = started + timeout

        while monotonic() < deadline:
            # Checked before sleeping and after waking, so stop() takes
            # effect within one poll interval at worst.
            if self._stopping.is_set():
                oradio_log.debug("Deferred WiFi listener start aborted by stop()")
                return
            if nm_available():
                oradio_log.info(
                    "NetworkManager available after %.1fs; starting WiFi listener",
                    monotonic() - started,
                )
                self._start_listener()
                return
            sleep(NM_POLL_INTERVAL)

        if not self._stopping.is_set():
            # NM never appeared: masked, disabled or failed to start. Unlike
            # the transient absence above, that is worth an incident.
            oradio_log.error("NetworkManager did not appear within %.0fs", timeout)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))

    def _start_listener(self) -> None:
        """
        Bring up the listener thread, start the scan burst and publish state.

        Called once NetworkManager is known to be available, either directly
        from start() or from the deferred _start_when_nm_ready() thread.
        """
        if not self.nm_listener.safe_start():
            oradio_log.error("WiFi event listener thread failed to start")
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            return

        if self.nm_listener.crashed:
            oradio_log.error(
                "WiFi event listener thread crashed during startup: %s", self.nm_listener.exception,
            )
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            return

        oradio_log.info("WiFi event listener thread started")

        # Build the network list now, in the background, so it is already
        # complete by the time the user asks for the access point. Started
        # only here, so the GLib loop is running and the resulting
        # AccessPointAdded signals are actually delivered. Daemon thread:
        # nothing waits on it and it exits when the process does.
        Thread(target=self._build_network_list, daemon=True).start()

        # Publish the current state immediately so subscribers don't have to
        # wait for the first state-change signal from NetworkManager
        Commands.publish(CommandMessage(WIFI_SOURCE, self.get_state()))

    def _build_network_list(self) -> None:
        """
        Populate the network list with a burst of scans at startup.

        The radio cannot be scanned once the access point is up without
        risking the connected client, so the list has to be right before the
        user asks for it. Doing that here rather than at the moment of asking
        is what keeps the delay between the button press and the "access
        point ready" announcement at zero: by then this has long finished and
        the keeper has been maintaining the result.

        Several sweeps rather than one because a single scan misses access
        points that beacon while the radio is on another channel. NM's list
        is cumulative, so each sweep can only add.

        Runs on a background thread; sets the listener's list_ready when done.
        """
        before = {net["ssid"] for net in get_wifi_networks()}
        started = monotonic()
        found = before

        oradio_log.debug(
            "Building network list: %d sweeps (%d networks known)", AP_SCAN_SWEEPS, len(before)
        )

        for sweep in range(1, AP_SCAN_SWEEPS + 1):
            # Without the listener there is nothing to collect the results:
            # the scans would run but no AccessPointAdded signal would arrive.
            if not self.nm_listener.is_alive():
                oradio_log.warning("Listener not running; network list may be incomplete")
                break

            completed = self.nm_listener.scan_and_wait()

            # Measured after the scan is reported complete, so the gain is
            # genuinely this sweep's: a sweep that repeatedly adds +0 means
            # AP_SCAN_SWEEPS can be reduced.
            previous, found = found, {net["ssid"] for net in get_wifi_networks()}
            oradio_log.debug(
                "Sweep %d of %d at %.1fs: %d networks (+%d)%s",
                sweep, AP_SCAN_SWEEPS, monotonic() - started, len(found), len(found - previous),
                "" if completed else " [not confirmed complete]",
            )

        # Set even if the burst was cut short: waiting longer would not help,
        # and blocking the access point indefinitely is worse than an
        # incomplete list.
        self.nm_listener.list_ready.set()

        oradio_log.info(
            "Network list ready: %d networks (+%d) in %.1fs",
            len(found), len(found - before), monotonic() - started,
        )
        if found - before:
            oradio_log.debug("Networks found only by scanning: %s", ", ".join(sorted(found - before)))

    def stop(self) -> None:
        """
        Signal the listener thread to stop and wait for it to exit.

        WifiEventListener.safe_stop() unblocks its own blocking GLib
        loop.run() call before joining.

        Also cancels a deferred start still waiting for NetworkManager, so
        the listener cannot come up after shutdown was requested.
        """
        self._stopping.set()
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

        When ssid is the Oradio access point, that thread first confirms the
        network list is complete (see _wait_for_network_list). That normally
        returns immediately, so the access point comes up without added
        delay; it waits only when the button is pressed before the startup
        scan has finished.

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
        # The list must be complete before the access point takes the radio,
        # since scanning afterwards risks the connected client. It normally
        # already is, so this does not delay activation.
        if network == ACCESS_POINT_SSID:
            self._wait_for_network_list()

        if not _wifi_up(network):
            # Activation failed; clean up the broken profile
            networkmanager_del(network)     # includes its own error logging
        else:
            # Connection is up; WifiEventListener will publish the new state
            oradio_log.info("Connected with '%s'", network)

    def _wait_for_network_list(self) -> None:
        """
        Ensure the network list is complete before the access point starts.

        Normally returns immediately: the list was built by
        _build_network_list() at startup and has been kept accurate by the
        listener's keeper sweeps ever since, so nothing needs to happen here
        and the access point comes up without delay.

        Only when the access point is requested within seconds of power-on,
        before the startup burst has finished, does this wait -- which is the
        one case where a delay is worth it, since the alternative is serving
        the user a list that is empty or half built.

        No scanning happens here. Scanning at this point would put the delay
        back into the path between the button press and the "access point
        ready" announcement, which is exactly what building the list in
        advance avoids.

        The flag is read from the listener singleton, not from this
        WifiService: oradio_control and web_service each construct their own
        WifiService, so a per-instance flag is set on one and read on the
        other, and this would always wait out its full timeout.
        """
        if self.nm_listener.list_ready.is_set():
            oradio_log.debug("Network list already built; starting access point")
            return

        oradio_log.info("Waiting for initial network scan to complete")
        started = monotonic()

        if self.nm_listener.list_ready.wait(AP_LIST_READY_TIMEOUT):
            oradio_log.info("Network list ready after %.1fs", monotonic() - started)
        else:
            # Bounded rather than indefinite: an access point with a partial
            # list is more use than no access point at all.
            oradio_log.warning(
                "Initial scan not complete after %.0fs; starting access point anyway",
                AP_LIST_READY_TIMEOUT,
            )

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
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DISCONNECT_FAILED))
            else:
                # WifiEventListener publishes the WIFI_DISCONNECTED state
                oradio_log.info("Disconnected from: '%s'", active)
        else:
            oradio_log.debug("Already disconnected")

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
    Return the WiFi networks accumulated from NetworkManager D-Bus signals.

    Reads cached state only — it never triggers a scan — so it is safe to
    call while the Oradio access point is serving a client, where scanning
    would drop that client's connection.

    An empty list means either that no networks are in range or that the
    event listener never started (NetworkManager unavailable). Check
    WifiService.nm_listener.is_alive() to tell those apart: retrying only
    helps in the first case.

    Returns:
        A list of {"ssid": str, "type": "open" | "closed"} dicts ordered
        by descending signal strength, excluding the Oradio AP, hidden
        SSIDs, and duplicates.
    """
    # WifiEventListener is a singleton, so this returns the running instance
    # (or constructs an inert one, which correctly reports no networks).
    return WifiEventListener().get_access_points()

def get_wifi_connection() -> str | None:
    """
    Return the SSID of the currently active WiFi connection, if any.

    Queries the kernel directly via iw (with iwgetid as fallback) so the
    result reflects the live radio state independent of NM's view.

    Note: the interface name is fixed by WIFI_INTERFACE; a different adapter
    name will cause this to silently return None.

    Returns:
        The active SSID, or None if the command fails or no connection
        is active.
    """
    cmd = (
        f"iw dev {WIFI_INTERFACE} info | awk '/ssid/ {{print $2}}'"
        f" || iwgetid -r {WIFI_INTERFACE}"
    )
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
