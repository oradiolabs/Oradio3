# !/usr/bin/env python3
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
@summary:       WiFi event listener: watching what NetworkManager is doing.
    This module observes; wifi_service.py acts. Everything here is about finding out the state of the radio and its
    neighbourhood -- the D-Bus subscription, the accumulated list of access points, the startup scan burst and the
    periodic keeper sweep. Nothing here decides to connect, disconnect or host an access point; that is wifi_service's
    job, and it is the only module that imports this one.
    Import rule: this module must never import from wifi_service.py. The dependency runs one way, so anything both
    modules need lives here. That is why nmcli_try() and get_wifi_connection() sit in this file even though they are
    not listener concepts in themselves -- WifiEventListener depends on both, so they cannot live above it. One grep
    for "wifi_service" in this file confirms the rule still holds.
    The access-point list is accumulated from NetworkManager's AccessPointAdded/Removed D-Bus signals rather than by
    scanning on demand, so get_wifi_networks() is a pure cache read. It is built by a burst of scans at startup and
    kept accurate by a periodic keeper sweep, both of which run in the background while the radio is otherwise idle.
    Having the list ready in advance is what lets the access point start without delay: scanning once the access point
    is up stalls beaconing and can drop the very client that is reading the list, so the keeper stands down for as
    long as the access point is active.
    Internet reachability is determined by reading NetworkManager's built-in Connectivity property -- no separate probe.
    Documentation:
        https://networkmanager.dev/
        https://pypi.org/project/nmcli/
        https://blogs.gnome.org/dcbw/2016/05/16/networkmanager-and-wifi-scans/
    Not supported:
        Connecting through a captive portal (detected but not handled).
        Connecting to VPN.
"""
from time import sleep, monotonic
from typing import Any
from threading import Lock, Event
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
)

##### GLOBAL constants ####################################
from constants import ACCESS_POINT_SSID

##### LOCAL constants #####################################

# WiFi interface name. Used for device-level nmcli calls and the kernel queries in get_wifi_connection();
# a different adapter name breaks both.
WIFI_INTERFACE = "wlan0"

# D-Bus names. Collected here because the access-point tracking below uses several of them repeatedly;
# keeping them as constants avoids typo-prone literals scattered through the listener.
NM_BUS_NAME        = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH     = "/org/freedesktop/NetworkManager"
NM_IFACE           = "org.freedesktop.NetworkManager"
NM_DEVICE_IFACE    = "org.freedesktop.NetworkManager.Device"
NM_WIRELESS_IFACE  = "org.freedesktop.NetworkManager.Device.Wireless"
NM_AP_IFACE        = "org.freedesktop.NetworkManager.AccessPoint"
DBUS_PROPS_IFACE   = "org.freedesktop.DBus.Properties"

# NM_DEVICE_TYPE_WIFI: value of the Device DeviceType property for WiFi adapters
NM_DEVICE_TYPE_WIFI = 2

# NM_802_11_AP_FLAGS_PRIVACY: access point requires a key/encryption. An AP counts as open only when
# this bit is clear *and* it advertises neither WPA nor RSN, so all three are checked together.
NM_AP_FLAGS_PRIVACY = 0x1

# Startup scan burst, run in the background as soon as the listener is up (see
# wifi_service.WifiService._build_network_list, which owns the burst; the pacing it depends on is scan_and_wait
# below). A single scan sweep regularly misses access points: the radio can be on another channel when an AP beacons,
# and 5 GHz DFS channels need a longer passive dwell. NetworkManager's access-point list is cumulative, so each extra
# sweep can only add.
#
# Sweeps are paced by waiting for NetworkManager to report each scan complete, not by a fixed interval. A sweep takes
# around nine seconds on this hardware, and a request issued while a scan is running is refused or folded into the
# running one, so any fixed interval shorter than a sweep collapses several requests into a single scan and makes the
# per-sweep gain figures meaningless.
AP_SCAN_SWEEPS = 3              # Number of completed sweeps in the startup burst
SCAN_COMPLETE_TIMEOUT = 20.0    # Max seconds to wait for one sweep to complete
SCAN_POLL_INTERVAL = 0.5        # Seconds between LastScan checks while waiting

# Keeper scan interval. NetworkManager scans by itself while the device is disconnected, but largely stops once
# associated -- which is the Oradio's normal state -- so without this the list slowly ages out exactly when it is
# least being refreshed. One sweep every interval keeps it accurate at the cost of a few hundred milliseconds
# off-channel, which the audio buffer absorbs. Skipped while hosting the access point, where a scan stalls beaconing
# and can drop the connected client.
AP_KEEPER_INTERVAL = 120  # Seconds between keeper sweeps

# NetworkManager device state codes
NM_DISCONNECTED = 30
NM_CONNECTED    = 100
NM_FAILED       = 120

# NetworkManager connectivity assessment codes. NM probes a known URL after each connection attempt and updates this value.
NM_CONNECTIVITY_NONE    = 1   # No network at all
NM_CONNECTIVITY_PORTAL  = 2   # Behind a captive portal (no open internet)
NM_CONNECTIVITY_LIMITED = 3   # IP connectivity, but no internet route
NM_CONNECTIVITY_FULL    = 4   # Full internet access confirmed

# Build the nmcli exception tuple dynamically so it stays correct if the nmcli package adds or renames exception
# classes in a future release. The starred expression unpacks nmcli_exceptions into a flat tuple suitable for use in
# an except clause (requires Python 3.11+).
nmcli_exceptions = tuple(
    exc for exc in vars(nmcli._exception).values()   # pylint: disable=protected-access
    if isinstance(exc, type) and issubclass(exc, Exception)
)

# nmcli._exception is a private module; if a future nmcli release renames or restructures it, the comprehension above
# could silently return an empty tuple, and nmcli_try's except clause would then let every nmcli error propagate
# uncaught from all its call sites instead of being caught, logged, and reported. Fail fast at import time instead of
# failing mysteriously later.
if not nmcli_exceptions:
    oradio_log.error(
        "No nmcli exception classes discovered from nmcli._exception; "
        "nmcli error handling in nmcli_try will not work as intended"
    )
    raise ImportError("Failed to discover nmcli exception classes for error handling")

##### Helpers #############################################

# Must run before the first SystemBus() call anywhere in this process. dbus-python binds a connection to whatever main
# loop is default at the time the connection is constructed, and SystemBus() returns a shared connection.
# nm_available() below opens that connection during WifiService.start(), which is earlier than
# WifiEventListener.setup(); without this, the shared connection would be cached without main-loop integration and the
# listener would silently never receive a single StateChanged or AccessPointAdded signal. setup() calls this again,
# which is harmless: it is idempotent.
DBusGMainLoop(set_as_default=True)

def nm_available() -> bool:
    """
    Report whether NetworkManager is running and owns its D-Bus name.

    NetworkManager.service is a Type=dbus unit declaring NM_BUS_NAME, so ownership of that name is the authoritative
    "NM is up and serving" signal -- the same condition that must hold for WifiEventListener.setup() to succeed.

    Cheap: one call on the already-open shared system bus, with no subprocess and no radio activity.

    Returns:
        True if the NetworkManager daemon holds NM_BUS_NAME on the system bus,
        False if it does not or if the system bus is unreachable.
    """
    try:
        return bool(SystemBus().name_has_owner(NM_BUS_NAME))
    except (DBusException, OSError) as ex_err:
        # Debug, not error: an absent bus is an expected early-boot state here, not a fault worth reporting on the
        # incident bus.
        oradio_log.debug("System D-Bus not reachable: %s", ex_err)
        return False

def nmcli_try(func, *args, ignore=(), **kwargs) -> tuple[bool, Any | None]:
    """
    Call an nmcli function, catching all known nmcli and OS errors.

    On failure, logs the error and publishes WIFI_NMCLI_FAILED on the error bus so subscribers are notified without
    the caller needing to handle it.

    Args:
        func:     The nmcli callable to invoke.
        *args:    Positional arguments forwarded to func.
        ignore:   Exception classes that are not failures. Logged at debug, with no error logged and no incident
                  published, and reported as success with a None result -- so a caller that acts on the success flag
                  cannot tell them from a call that returned nothing, which is the point: the outcome the caller
                  wanted has been achieved by other means. Defaults to an empty tuple, which never matches, so by
                  default every exception is a failure.
        **kwargs: Keyword arguments forwarded to func.

    Returns:
        A (success, result) tuple where success is True if the call completed without error, or raised one of the
        ignored classes, and result holds the return value where there is one. (False, None) on any real failure.
    """
    try:
        result = func(*args, **kwargs)
        return True, result
    # Must precede the general clause below, which would otherwise match first
    except ignore as ex_err:
        oradio_log.debug("nmcli call declined for %s: %s", func.__name__, ex_err)
        return True, None
    # Exceptions built dynamically, so mypy can't verify it's a valid exception tuple statically
    except (*nmcli_exceptions, CalledProcessError, OSError) as ex_err:      # type: ignore[misc]
        oradio_log.error("nmcli call failed for %s: %s", func.__name__, ex_err)
        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_NMCLI_FAILED))
        return False, None

def get_wifi_connection() -> str | None:
    """
    Return the SSID of the currently active WiFi connection, if any.

    Queries the kernel directly via iw (with iwgetid as fallback) so the result reflects the live radio state
    independent of NM's view.

    Note: the interface name is fixed by WIFI_INTERFACE; a different adapter name will cause this to silently return None.

    Returns:
        The active SSID, an empty string when the radio is associated with nothing, or None if the query itself fails.
        Callers test truthiness, so the empty string and None are equivalent to them.
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

@singleton
class WifiEventListener(ThreadTemplate):
    """
    Singleton listener for WiFi state changes via NetworkManager D-Bus signals.

    Connects to the system D-Bus, locates the WiFi device managed by NetworkManager (DeviceType == 2,
    i.e. NM_DEVICE_TYPE_WIFI), and subscribes to the StateChanged signal on that device's interface.

    Internet reachability is determined by reading NetworkManager's own Connectivity property rather than making a
    separate probe, so no additional network round-trip is needed and the captive-portal case is detected correctly.

    Built on ThreadTemplate, so the listener gets restart support and crash detection for free:
        * setup()     - D-Bus connection, signal subscription and keeper timeout; undone by _unsubscribe().
        * do_work()   - runs the GLib main loop. A single blocking call rather than a quick repeated unit of work:
                        GLib.MainLoop.run() only returns once something calls its quit(), which safe_stop() does.
        * safe_stop() - quits the GLib loop so the blocking do_work() call returns, delegates to ThreadTemplate's
                        join-based safe_stop(), then undoes setup()'s registrations so a later start() cannot
                        double them up.

    If no WiFi device is found, or if the D-Bus connection fails, setup() raises. ThreadTemplate then logs and records
    the crash, and the four internal handles (bus, _wifi_path, _nm_props, _loop) are left as None. All other modules
    can still operate normally; WiFi state changes will simply not be reported. Use the inherited crashed / exception
    properties to detect this from the outside.
    """

    def __init__(self) -> None:
        """
        Set up the listener's initial state.

        The singleton decorator ensures this constructor runs at most once per process. Does not start the background
        thread -- call safe_start() (typically via WifiService.start()) explicitly when ready to begin listening. All
        actual D-Bus/GLib work happens in setup(), which then runs on the worker thread.
        """
        super().__init__(name="WifiEventListener")

        # Set by setup(); all four stay None if it fails at any point:
        #   bus        — set once the system bus connection is open
        #   _wifi_path — set once the WiFi device is located on the bus
        #   _nm_props  — set once the NM Properties interface is obtained
        #   _loop      — set once the GLib main loop object is created
        self.bus: SystemBus | None = None
        self._wifi_path: str | None = None
        self._nm_props: Interface | None = None
        self._loop: GLib.MainLoop | None = None

        # Accumulated view of the WiFi neighbourhood, maintained from AccessPointAdded/Removed signals and read by
        # get_wifi_networks(). Keyed by D-Bus object path rather than SSID: paths are unique per BSSID, so removals
        # are unambiguous and one SSID served by several access points is tracked correctly. Deduplication by SSID
        # happens at read time in get_access_points().
        self._access_points: dict[str, dict] = {}
        self._ap_lock = Lock()      # Guards _access_points against the GLib thread

        # Everything setup() registers outside this object, kept so _unsubscribe() can take it all back down again.
        # Both survive the worker thread they were created on -- the bus connection is shared per process and the
        # keeper is attached to the default main context -- so stopping the thread does not clear them.
        self._signal_matches: list = []         # SignalMatch per add_signal_receiver()
        self._keeper_source: int | None = None  # GLib source id of the keeper timeout

        # Set once the startup scan burst has finished. Lives here, on the singleton, rather than on WifiService:
        # oradio_control, web_service and rms_service each construct a WifiService, so a per-instance flag would be
        # set on one object and read on the other, leaving the access point path to wait out its full timeout on a
        # list that is already built. The singleton decorator runs this __init__ exactly once per process, so neither
        # this nor _access_points is ever reset by a later construction.
        self.list_ready = Event()

    def setup(self) -> None:
        """
        Connect to the system bus, find the WiFi device, subscribe to its StateChanged and AccessPointAdded/Removed
        signals, seed the access-point list, schedule the keeper sweep, and create the GLib main loop that do_work() runs.

        Runs once per safe_start() (i.e. again on every restart), on the worker thread. Publishes WIFI_DBUS_FAILED and
        raises on any failure so ThreadTemplate.run() logs and records the crash; the handles above are left as None
        so other methods degrade gracefully (e.g. _get_connectivity() treats a None _nm_props as "no connectivity"
        rather than raising).
        """
        try:
            # Required before the first SystemBus() call: integrates GLib's event loop with dbus-python so signal
            # callbacks are dispatched on the GLib main loop thread rather than the calling thread.
            DBusGMainLoop(set_as_default=True)

            # Connect to the system-wide D-Bus (requires no special privileges)
            self.bus = SystemBus()

            # Start from a clean slate. safe_stop() already unsubscribes, so this normally finds nothing; it matters
            # when a previous setup() raised part-way through and left some of its registrations behind.
            self._unsubscribe()

            # Obtain the top-level NetworkManager object and its primary interface
            nm_object = self.bus.get_object(NM_BUS_NAME, NM_OBJECT_PATH)
            nm_iface = Interface(nm_object, NM_IFACE)

            # Store a Properties interface on the NM object so the signal callback can read the
            # Connectivity property without reopening the bus connection on every state change.
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

            # Register the state-change callback for the specific WiFi device path. Scoping to self._wifi_path
            # avoids receiving spurious StateChanged signals from other network devices (ethernet, VPN, etc.).
            self._signal_matches.append(
                self.bus.add_signal_receiver(
                    self._wifi_state_changed,
                    dbus_interface=NM_DEVICE_IFACE,
                    signal_name="StateChanged",
                    path=self._wifi_path,
                )
            )

            # Track the access points NetworkManager discovers. NM scans on its own whenever the device is idle or
            # disconnected, so subscribing is enough to accumulate the neighbourhood over time without this module
            # ever requesting a scan. That matters because scanning is impossible while hosting the access point
            # and disruptive to playback while associated.
            for signal_name, handler in (
                ("AccessPointAdded",   self._access_point_added),
                ("AccessPointRemoved", self._access_point_removed),
            ):
                self._signal_matches.append(
                    self.bus.add_signal_receiver(
                        handler,
                        dbus_interface=NM_WIRELESS_IFACE,
                        signal_name=signal_name,
                        path=self._wifi_path,
                    )
                )

            # Signals only report changes from now on, so anything NM already knows about would never be seen. Seed
            # from the current property value; subscription happens first so an access point discovered between these
            # two steps arrives as a signal instead of being lost.
            self._seed_access_points()

            # Keep the list accurate for as long as the loop runs. Registered on the same main context the loop below
            # uses, so it needs no thread of its own. That context outlives the loop, so quitting the loop only stops
            # the sweeps firing; the source itself is removed by _unsubscribe().
            self._keeper_source = GLib.timeout_add_seconds(
                AP_KEEPER_INTERVAL, self._keeper_sweep
            )

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

        Unlike a typical ThreadTemplate subclass, this is a single blocking call rather than a quick unit of work
        polled every interval: GLib.MainLoop.run() only returns once something calls its quit(), which safe_stop()
        below does. If run() ever returned on its own (e.g. quit() triggered from elsewhere) while _stop_event is
        clear, ThreadTemplate's loop would call do_work() again, a harmless self-healing restart of the event loop.
        """
        # setup() always runs (and sets self._loop) before ThreadTemplate ever calls do_work(); the assert
        # documents/enforces that invariant for mypy, which can't see across the two methods.
        assert self._loop is not None, "do_work() called before setup() completed"
        self._loop.run()

    def safe_stop(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """
        Stop the listener: unblock the GLib loop, join the thread, and undo the bus and main-context registrations
        setup() made.

        do_work() is parked inside self._loop.run() until something calls quit() on it, so the base implementation's
        _stop_event alone can't interrupt it. _stop_event is set here *before* calling quit() so that once run()
        returns, ThreadTemplate's run() loop sees the stop request immediately instead of calling do_work() (and
        restarting the GLib loop) again.

        Unsubscribing comes last, after the join, so no signal callback can be part-way through when its receiver is
        removed. It runs whether or not the join succeeded: a thread that outstays its timeout is still one whose
        registrations must not survive into the next start().

        Args:
            timeout: Max seconds to wait for the thread to exit.

        Returns:
            True if the thread finished within timeout, or if it was never started.
            False if it's still alive afterward or crashed.
        """
        self._stop_event.set()
        if self._loop is not None:
            self._loop.quit()
        stopped = super().safe_stop(timeout)
        self._unsubscribe()
        return stopped

    def _unsubscribe(self) -> None:
        """
        Remove the D-Bus signal receivers and the keeper timeout.

        setup() runs again on every safe_start(), and what it registers is not owned by the worker thread: SystemBus()
        hands back one shared connection per process, and the keeper is attached to the default main context. Both
        therefore outlive a stop. Without this, each stop/start cycle stacks another set of receivers on top of the
        previous ones, so every StateChanged signal is handled once per cycle and publishes that many duplicate
        messages, while the keeper sweeps the radio that many times per interval.

        Idempotent, and safe to call whether or not setup() completed: it removes whatever is currently registered and
        clears the record, so a setup() that raised part-way through is cleaned up as completely as one that succeeded.
        """
        for match in self._signal_matches:
            try:
                match.remove()
            except DBusException as ex_err:
                # Already gone, or the connection has closed under it; either way the receiver is not registered any
                # more, which is the goal
                oradio_log.debug("Could not remove signal receiver: %s", ex_err)
        self._signal_matches.clear()

        if self._keeper_source is not None:
            GLib.source_remove(self._keeper_source)
            self._keeper_source = None

    def _get_connectivity(self) -> int:
        """
        Return NetworkManager's current connectivity assessment.

        Reads the Connectivity property from the top-level NetworkManager D-Bus object. NM updates this value by
        probing a known URL after each connection attempt, so no additional network round-trip is made here.

        Returns:
            An integer connectivity code:

            * NM_CONNECTIVITY_NONE (1)    — no network at all
            * NM_CONNECTIVITY_PORTAL (2)  — behind a captive portal
            * NM_CONNECTIVITY_LIMITED (3) — IP connectivity, no internet route
            * NM_CONNECTIVITY_FULL (4)    — full internet access confirmed

            Returns NM_CONNECTIVITY_NONE on any D-Bus error so the caller can safely treat an unreadable state as no
            connectivity.
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

        Called once from setup(), immediately after subscribing to the AccessPointAdded/Removed signals,
        because those signals carry only subsequent changes.
        """
        if self.bus is None or self._wifi_path is None:
            oradio_log.warning("Cannot seed access points: D-Bus not set up")
            return

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

        # Access points, not networks: one SSID is commonly served by several (2.4 and 5 GHz radios, mesh nodes), so
        # the network count is lower.
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
        if self.bus is None:
            return          # Not logged: this runs once per access point per scan

        try:
            ap_props = Interface(
                self.bus.get_object(NM_BUS_NAME, ap_path), DBUS_PROPS_IFACE
            ).GetAll(NM_AP_IFACE)
        except DBusException:
            # The access point can disappear between the signal and this read; there is simply nothing to record.
            return

        # Ssid is a byte array, not a string, and is not guaranteed to be valid UTF-8: a hidden network
        # advertises an empty one, and a misconfigured access point can advertise arbitrary bytes.
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

        Ignored while hosting the Oradio access point: no scanning is possible in that mode, so NM keeps ageing
        entries out with nothing to refresh them. Honouring removals would make the network list shown to the
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

        Deduplicates by SSID, keeping the strongest signal where the same network is served by several access points.
        Reads cached state only: no scan, no subprocess, and therefore safe to call while the Oradio access point is
        serving a client.

        Returns:
            A list of {"ssid": str, "type": "open" | "closed"} dicts ordered by descending signal strength.
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

        Goes through nmcli rather than the Device.Wireless RequestScan D-Bus method: that method is gated by the
        polkit action org.freedesktop.NetworkManager.wifi.scan, which is granted to active local sessions only.
        oradio_control runs as a systemd system service and therefore has no session, so every direct call fails with
        "not authorized". The nmcli package invokes the binary under sudo, which is authorized.

        Returns as soon as NM accepts the request; the scan itself completes in the background and its results arrive
        as AccessPointAdded signals.

        Returns:
            True if a scan is now running -- whether this call started it or one was already in progress. False if NM
            will not scan at all, in which case no AccessPointAdded signal is coming and there is nothing to wait for.
        """
        # ScanningNotAllowedException means a scan is already running or one finished very recently. Both mean
        # scanning is happening, which is what the caller wants to know, so it counts as success rather than as a
        # refusal -- see the ignore argument of nmcli_try.
        is_ok, _ = nmcli_try(nmcli.device.wifi_rescan, ignore=(ScanningNotAllowedException,))
        return is_ok

    def last_scan(self) -> int | None:
        """
        Return NetworkManager's LastScan timestamp for the WiFi device.

        The value is CLOCK_BOOTTIME milliseconds at the point the last scan completed, or -1 if no scan has ever
        completed. Only used to detect that a scan has finished, by watching for the value to change, so the clock
        it is measured against does not matter.

        Returns:
            The raw LastScan value, or None if it cannot be read.
        """
        if self.bus is None or self._wifi_path is None:
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

        Waiting for LastScan to advance paces each request behind the previous result, so a sweep is genuinely a sweep
        and the pacing adapts to however long the hardware takes. A fixed interval cannot do that: a full sweep takes
        several seconds (5 GHz DFS channels must be dwelt on passively), and a request issued while one is running is
        either refused or folded into it, so several requests yield a single scan.

        Args:
            timeout: Maximum seconds to wait for completion.

        Returns:
            True if a scan completed within timeout, False on timeout or if LastScan cannot be read.
        """
        baseline = self.last_scan()

        if not self.request_scan():
            # NM will not scan and none is already running: polkit refusal, an nmcli failure, or the radio busy
            # hosting the access point. LastScan cannot advance, so waiting out the timeout costs the caller
            # SCAN_COMPLETE_TIMEOUT per sweep and gains nothing. Returning now is what keeps the startup burst's
            # worst case near zero when scanning is unavailable, rather than AP_SCAN_SWEEPS full timeouts.
            oradio_log.debug("No scan running and NM would not start one; not waiting")
            return False

        if baseline is None:
            # No LastScan to watch (device path missing, or property unreadable):
            # fall back to a fixed wait so the caller still paces itself rather than spinning.
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

        Runs on the GLib main loop thread. NetworkManager scans on its own while the device is disconnected but
        largely stops once associated, so without this the list ages out during exactly the long connected periods
        where nothing else refreshes it.

        Returns:
            True, so GLib keeps rescheduling it. Returning False would cancel the timeout permanently.
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

        Called by the GLib main loop thread whenever the NetworkManager WiFi device transitions between states. Only
        the three terminal states that require an application response are acted upon; intermediate states are ignored
        to avoid spurious messages during connection setup.

        On NM_CONNECTED, the active SSID is checked first to detect AP mode. For all other connections,
        NetworkManager's Connectivity property is read to distinguish full internet access from limited or no
        connectivity — without making a separate network probe.

        Args:
            new_state:  New NM device state code (int).
            _old_state: Previous NM device state code (unused).
            _reason:    NM reason code for the transition (unused).

        Wrapped in a broad except: an uncaught exception here would either crash the whole listener or silently drop
        just this one state transition, and neither would report anything to Incidents.
        """
        try:
            # Transient states such as PREPARE and CONFIG are excluded.
            if new_state not in (NM_CONNECTED, NM_DISCONNECTED, NM_FAILED):
                return

            if new_state == NM_CONNECTED:
                active = get_wifi_connection()
                if active == ACCESS_POINT_SSID:
                    # Connected to the Oradio's own access point (AP mode); connectivity check is not relevant here
                    oradio_log.debug("Publish wifi service message: %s", WIFI_ACCESS_POINT)
                    Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_ACCESS_POINT))
                else:
                    # Read NM's connectivity assessment — it has already probed for internet access so no separate
                    # round-trip is needed here
                    connectivity = self._get_connectivity()
                    if connectivity == NM_CONNECTIVITY_FULL:
                        # External network with confirmed internet access
                        oradio_log.debug("Wifi connected to internet")
                        Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_CONNECTED))
                    else:
                        # PORTAL, LIMITED, or NONE: IP may be assigned but there is no usable internet route
                        oradio_log.debug("Wifi not connected to internet")
                        Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_CONNECT_FAILED))

            elif new_state == NM_DISCONNECTED:
                oradio_log.debug("Wifi disconnected")
                Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_DISCONNECTED))

            else:   # NM_FAILED — NetworkManager could not complete the connection
                oradio_log.debug("Wifi could not complete connection: %s", new_state)
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_CONNECT_FAILED))

        # Broad catch is intentional: this callback must never take down the GLib main loop or the listener thread
        # over a single bad signal delivery.
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Error handling WiFi StateChanged signal (new_state=%s): %s", new_state, ex_err)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))

def get_wifi_networks() -> list:
    """
    Return the WiFi networks accumulated from NetworkManager D-Bus signals.

    Reads cached state only — it never triggers a scan — so it is safe to call while the Oradio access point is
    serving a client, where scanning would drop that client's connection.

    An empty list means either that no networks are in range or that the event listener never started (NetworkManager
    unavailable). Check wifi_service.WifiService.nm_listener.is_alive() to tell those apart: retrying only helps in
    the first case.

    Returns:
        A list of {"ssid": str, "type": "open" | "closed"} dicts ordered by descending signal strength,
        excluding the Oradio AP, hidden SSIDs, and duplicates.
    """
    # WifiEventListener is a singleton, so this returns the running instance (or constructs an inert one, which
    # correctly reports no networks).
    return WifiEventListener().get_access_points()

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports
    from constants import RED, GREEN, YELLOW, NC    # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu() -> None:     # pylint: disable=too-many-branches
        """
        Run an interactive self-test menu for the WiFi event listener.

        Exercises the listener in isolation, without WifiService: start and stop the thread, watch the access-point
        list fill as scans complete, and check what the listener believes the radio is doing. Use the wifi_service
        menu instead to test connecting, disconnecting and the access point.
        """
        input_selection = (
            "Select a function, input the number:\n"
            " 0-Quit\n"
            " 1-Start listener\n"
            " 2-Stop listener\n"
            " 3-Show listener thread status\n"
            " 4-List on air wifi networks\n"
            " 5-Request a scan and wait for it to complete\n"
            " 6-Run a startup scan burst and report the gain per sweep\n"
            " 7-Show active connection as the listener sees it\n"
            "Select: "
        )

        # Construct the listener; its D-Bus thread is not started until safe_start() is called (option 1).
        listener = WifiEventListener()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    listener.safe_stop()  # Ensure nothing is left running on exit
                    break
                case 1:
                    if not nm_available():
                        print(f"\n{RED}NetworkManager is not running; the listener cannot start{NC}\n")
                    elif listener.safe_start():
                        print(f"\n{GREEN}Listener started{NC}\n")
                    else:
                        print(f"\n{RED}Listener failed to start{NC}\n")
                case 2:
                    print("\nStopping listener...\n")
                    listener.safe_stop()
                case 3:
                    print(
                        f"\nis_alive={listener.is_alive()}, "
                        f"crashed={listener.crashed}, "
                        f"exception={listener.exception}, "
                        f"list_ready={listener.list_ready.is_set()}\n"
                    )
                case 4:
                    networks = get_wifi_networks()
                    print(f"\n{len(networks)} networks: {networks}\n")
                case 5:
                    if not listener.is_alive():
                        print(f"\n{YELLOW}Listener not running; results will not be collected{NC}\n")
                    start = monotonic()
                    completed = listener.scan_and_wait()
                    status = f"{GREEN}completed{NC}" if completed else f"{YELLOW}not confirmed{NC}"
                    print(f"\nScan {status} in {monotonic() - start:.1f}s: {len(get_wifi_networks())} networks\n")
                case 6:
                    if not listener.is_alive():
                        print(f"\n{YELLOW}Listener not running; results will not be collected{NC}\n")
                    found = {net["ssid"] for net in get_wifi_networks()}
                    start = monotonic()
                    for sweep in range(1, AP_SCAN_SWEEPS + 1):
                        listener.scan_and_wait()
                        previous, found = found, {net["ssid"] for net in get_wifi_networks()}
                        print(
                            f"  sweep {sweep} of {AP_SCAN_SWEEPS} at {monotonic() - start:5.1f}s: "
                            f"{len(found):3} networks (+{len(found - previous)})"
                        )
                    print(f"\n{GREEN}Burst complete: {len(found)} networks{NC}\n")
                case 7:
                    print(f"\nActive connection: '{get_wifi_connection()}'\n")
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
