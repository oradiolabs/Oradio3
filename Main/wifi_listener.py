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
    This module observes; wifi_service.py acts. It owns the D-Bus subscription, the accumulated list of access
    points, the startup scan burst and the periodic keeper sweep. Nothing here decides to connect, disconnect or
    host an access point; that is wifi_service's job, and it is the only module that imports this one.
    Import rule: this module must never import from wifi_service.py. Anything both modules need lives here, which
    is why nmcli_try() and get_wifi_connection() sit in this file. One grep for "wifi_service" confirms the rule.
    The access-point list is accumulated from NetworkManager's AccessPointAdded D-Bus signals rather than by
    scanning on demand, so get_wifi_networks() is close to a pure cache read. Entries age out on this module's own
    schedule (AP_ENTRY_TTL), not NM's: NM's list is what the radio can hear right now, while the Oradio needs what
    the neighbourhood contains. The keeper sweep stands down for as long as
    the Oradio access point is active, because scanning then can drop the client reading the list.
    Internet reachability is read from NetworkManager's Connectivity property -- no separate probe.
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
from utilities import ThreadTemplate, JOIN_TIMEOUT
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

# WiFi interface name. Used for device-level nmcli calls and to resolve the NM device path in
# _get_wifi_device_path(); a different adapter name breaks both.
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

# D-Bus error names meaning "NetworkManager or the WiFi device is not there yet" rather than "something is wrong".
# oradio_control starts after basic.target, deliberately ahead of the network, so every one of these is an ordinary
# early-boot state on the way up -- see nm_available(), which treats an absent bus the same way. Logged at debug so a
# normal boot does not produce warnings that a support reader has to learn to ignore.
NM_NOT_READY_ERRORS = (
    "org.freedesktop.DBus.Error.ServiceUnknown",     # NetworkManager not running
    "org.freedesktop.DBus.Error.NameHasNoOwner",     # bus up, NM has not claimed its name
    "org.freedesktop.NetworkManager.UnknownDevice",  # NM running, wlan0 not registered yet
)

# NM_802_11_AP_FLAGS_PRIVACY: access point requires a key/encryption. An AP counts as open only when
# this bit is clear *and* it advertises neither WPA nor RSN, so all three are checked together.
NM_AP_FLAGS_PRIVACY = 0x1

# Startup scan burst, run in the background as soon as the listener is up (see
# wifi_service.WifiService._build_network_list, which owns the burst; the pacing it depends on is scan_and_wait
# below). A single scan sweep regularly misses access points: the radio can be on another channel when an AP beacons,
# and 5 GHz DFS channels need a longer passive dwell. NetworkManager's access-point list is cumulative, so each extra
# sweep can only add.
#
# Two rather than three, on measurement. Over four cold boots the sweeps contributed +6, +3 and +2 networks, but the
# final totals were 14, 16, 13 and 15 under identical conditions: the run-to-run spread is larger than everything the
# second and third sweeps add together. The burst samples the neighbourhood, it does not converge on it, so paying a
# third sweep for that last increment buys less than the noise floor.
#
# What makes two sufficient is that the burst no longer has to be complete. Entries now age out on this module's own
# schedule (AP_ENTRY_TTL) instead of being culled by NM within minutes, so anything a boot misses is picked up by a
# later keeper sweep and then stays. The burst's remaining job is to get most of the list up quickly, and sweep one
# takes the largest share of that in every boot measured.
#
# The cost side is what a third sweep would delay. list_ready is set only when the burst ends, and _wait_for_network_list
# holds the access point until then, so each sweep extends the one window where a long press can actually wait: about
# 20s for three sweeps against 12s for two.
#
# Sweeps are paced by waiting for NetworkManager to report each scan complete, not by a fixed interval. A sweep takes
# around nine seconds on this hardware -- the ~4s first sweep seen at boot is an artifact of riding a scan NM was
# already running while associating, not a property of the first sweep -- and a request issued while a scan is running
# is refused or folded into the running one, so any fixed interval shorter than a sweep collapses several requests
# into a single scan and makes the per-sweep gain figures meaningless.
AP_SCAN_SWEEPS = 2              # Number of completed sweeps in the startup burst
SCAN_COMPLETE_TIMEOUT = 20.0    # Max seconds to wait for one sweep to complete
SCAN_POLL_INTERVAL = 0.5        # Seconds between LastScan checks while waiting

# Keeper scan interval. NetworkManager scans by itself while the device is disconnected, but largely stops once
# associated -- which is the Oradio's normal state -- so without this the list ages out during exactly the long
# connected periods where nothing else refreshes it.
#
# This is a staleness bound, not a freshness target. What matters is how out of date the list can be at the single
# moment it is read -- when the user starts the web service -- not how fresh it is on average. In a fixed
# installation the neighbourhood changes on the scale of weeks (a neighbour's new router, someone moving out), so
# fifteen minutes is already far tighter than the thing being tracked and there is nothing to buy by going lower.
#
# What a lower value does cost is log volume, since every sweep writes a line at DEBUG: the interval effectively
# decides how much of the rotation budget the keeper consumes. At the previous 120s it consumed nearly all of it,
# leaving roughly a day of history; at fifteen minutes it is a rounding error.
#
# Each sweep also costs a few hundred milliseconds off-channel. Local USB playback does not notice, and a webradio
# buffer absorbs it. Skipped entirely while hosting the access point, where a scan stalls beaconing and can drop the
# client currently reading the list.
AP_KEEPER_INTERVAL = 15 * 60  # Seconds between keeper sweeps

# Signal-strength survey. Temporary instrumentation, not a permanent feature: it exists to supply the numbers needed
# to choose a strength threshold for get_access_points(), which cannot be picked from theory because NM's Strength is
# a 0-100 driver quality figure whose mapping to dBm varies by chipset.
#
# Two lines per keeper sweep at most -- one listing every network and its strength, one listing what appeared and
# vanished since the previous sweep, and the second only when something actually changed. The churn line is the one
# that matters: an SSID that repeatedly vanishes at 18 tells you where the flapping lives, and therefore where to
# cut. Roughly 400 bytes per sweep, so about 40 KB/day at the interval above -- affordable against the rotation
# budget for the days it takes to collect, and meant to be switched off once a threshold is chosen.
SIGNAL_SURVEY = True

# How long an access point stays in the list after NetworkManager last confirmed it exists.
#
# This module keeps its own ageing policy rather than following NM's, because the two lists are different objects:
# NM's is a live view of what the radio can hear right now, and it culls anything not seen by a recent scan. Once
# associated, NM largely stops scanning, so within minutes it culls everything except the access point it is
# associated with. Following that took the list from 16 networks to 1 between two keeper sweeps, which is what the
# portal would then have shown.
#
# The Oradio needs the other thing: an accumulated view of the neighbourhood, valid at the one moment the user opens
# the web service. An access point vanishing from NM's list is not evidence it is gone -- only that nothing has
# scanned lately -- so absence is timed out here instead, on a scale where it does mean something.
#
# A day is long enough to be completely independent of scan cadence, and short enough that a genuinely departed
# network is gone by tomorrow. The cost is listing a network that has since disappeared; in a fixed installation
# that is rare and harmless next to showing the user one network.
AP_ENTRY_TTL = 24 * 60 * 60  # Seconds an unconfirmed access point stays listed

# NetworkManager device state codes
NM_DISCONNECTED = 30
NM_CONNECTED    = 100
NM_FAILED       = 120

# NetworkManager connectivity assessment codes. NM probes a known URL after each connection attempt and
# updates this value.
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

    Cheap: one call on the shared system bus, with no subprocess and no radio activity.

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

    On failure, logs the error and publishes WIFI_NMCLI_FAILED on the error bus.

    Args:
        func:     The nmcli callable to invoke.
        *args:    Positional arguments forwarded to func.
        ignore:   Exception classes to treat as success with a None result: logged at debug, no error logged and no
                  incident published. Defaults to an empty tuple, so by default every exception is a failure.
        **kwargs: Keyword arguments forwarded to func.

    Returns:
        A (success, result) tuple. success is True if the call completed without error or raised one of the ignored
        classes; result holds the return value where there is one. (False, None) on any real failure.
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

def _get_wifi_device_path(bus) -> str:
    """
    Return the D-Bus object path of WIFI_INTERFACE.

    Raises DBusException (UnknownDevice) if NM does not manage an interface by that name.
    """
    nm_iface = Interface(bus.get_object(NM_BUS_NAME, NM_OBJECT_PATH), NM_IFACE)
    return str(nm_iface.GetDeviceByIpIface(WIFI_INTERFACE))

def get_wifi_connection() -> str | None:
    """
    Return the SSID of the currently active WiFi connection, if any.

    Three D-Bus round-trips on the shared system bus, no subprocess, because this is called from the GLib callback
    path (see _access_point_added) as well as from get_state().

    The interface name is fixed by WIFI_INTERFACE, so a different adapter name makes this return None. A hidden
    network advertises an empty SSID and is therefore indistinguishable here from not being associated.

    Returns:
        The active SSID, an empty string when the radio is associated with nothing, or None if the query itself
        fails. Callers test truthiness, so the empty string and None are equivalent to them.
    """
    try:
        bus = SystemBus()
        wifi_props = Interface(
            bus.get_object(NM_BUS_NAME, _get_wifi_device_path(bus)), DBUS_PROPS_IFACE
        )
        ap_path = str(wifi_props.Get(NM_WIRELESS_IFACE, "ActiveAccessPoint"))

        # "/" is NM's null object path: no association, or association still in progress.
        if ap_path == "/":
            return ""

        ap_props = Interface(bus.get_object(NM_BUS_NAME, ap_path), DBUS_PROPS_IFACE)
        # Ssid is a byte array and is not guaranteed to be valid UTF-8; same handling as _access_point_added.
        return bytes(ap_props.Get(NM_AP_IFACE, "Ssid")).decode("utf-8", errors="replace")

    except DBusException as ex_err:
        # An absent NM or an unregistered wlan0 is expected while the system is still coming up; anything else is a
        # genuine fault worth a warning. Both return None, so callers are unaffected by the distinction.
        if ex_err.get_dbus_name() in NM_NOT_READY_ERRORS:
            oradio_log.debug("WiFi device not available yet: %s", ex_err.get_dbus_message())
        else:
            oradio_log.warning("Could not determine active WiFi connection: %s", ex_err.get_dbus_message())
        return None
    except OSError as ex_err:
        # System bus unreachable -- same early-boot state nm_available() logs at debug.
        oradio_log.debug("System D-Bus not reachable: %s", ex_err)
        return None

@singleton
# Eleven instance attributes against a max-attributes of 10. The eleventh is _last_survey, which exists only for
# the temporary signal survey (see SIGNAL_SURVEY); removing that instrumentation puts the count back at 10 and
# makes this disable unnecessary. Kept local rather than raising max-attributes, so the exemption expires with
# the thing that caused it instead of loosening the limit for every class in the project.
class WifiEventListener(ThreadTemplate):    # pylint: disable=too-many-instance-attributes
    """
    Singleton listener for WiFi state changes via NetworkManager D-Bus signals.

    Connects to the system bus, locates the WiFi device managed by NetworkManager (DeviceType == 2), and subscribes to
    its StateChanged and AccessPointAdded/Removed signals. Internet reachability is read from NetworkManager's own
    Connectivity property, so no separate probe is made.

    Built on ThreadTemplate:
        * setup()     - D-Bus connection, signal subscription and keeper timeout; undone by _unsubscribe().
        * do_work()   - runs the GLib main loop; blocks until safe_stop() quits it.
        * safe_stop() - quits the GLib loop, joins the thread, then undoes setup()'s registrations.

    If no WiFi device is found, or if the D-Bus connection fails, setup() raises. ThreadTemplate then logs and records
    the crash, and the four internal handles (bus, _wifi_path, _nm_props, _loop) are left as None. All other modules
    can still operate normally; WiFi state changes will simply not be reported. Use the inherited crashed / exception
    properties to detect this from the outside.
    """

    def __init__(self) -> None:
        """
        Set up the listener's initial state.

        Runs at most once per process (singleton). Does not start the background thread -- call safe_start(), typically
        via WifiService.start(). All D-Bus and GLib work happens in setup(), on the worker thread.
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

        # Accumulated view of the WiFi neighbourhood, maintained from AccessPointAdded signals and read by
        # get_wifi_networks(). Keyed by BSSID (the access point's hardware address) rather than by D-Bus object path:
        # NM allocates a fresh path every time it rediscovers an access point it had culled, so path keys would
        # accumulate one dead entry per rediscovery per access point for as long as the entry survives. The BSSID is
        # stable across those cycles, so a rediscovery updates the entry in place.
        #
        # Each entry carries the path it was last seen at (needed to read Strength) and a monotonic last_seen used by
        # _expire_access_points(). Deduplication by SSID happens at read time in get_access_points().
        #
        # AccessPointRemoved is deliberately not subscribed: see AP_ENTRY_TTL for why NM's removals are not evidence
        # of absence.
        self._access_points: dict[str, dict] = {}
        self._ap_lock = Lock()      # Guards _access_points against the GLib thread

        # Everything setup() registers outside this object, kept so _unsubscribe() can take it all back down again.
        # Both survive the worker thread they were created on -- the bus connection is shared per process and the
        # keeper is attached to the default main context -- so stopping the thread does not clear them.
        self._signal_matches: list = []         # SignalMatch per add_signal_receiver()
        self._keeper_source: int | None = None  # GLib source id of the keeper timeout

        # Whether the radio is currently hosting the Oradio access point. Seeded in setup() and maintained from
        # _wifi_state_changed, which already has the answer: the transition into AP mode is precisely the signal it
        # handles. Read by _keeper_sweep and _refresh_signal_strengths on the GLib main loop thread, where querying
        # it live would mean three blocking D-Bus round-trips each time -- and a blocking call from inside a signal
        # callback lets dbus-python pump the loop and dispatch another AccessPointAdded re-entrantly, part-way
        # through the one already running. Reading a bool removes both the cost and the reentrancy.
        #
        # A plain attribute needs no lock: every writer and every reader runs on the GLib main loop thread.
        self._hosting_ap = False

        # Previous sweep's ssid -> strength map, used by _log_signal_survey() to report what appeared and vanished.
        # Same threading argument as above: written and read only from the keeper sweep on the main loop thread.
        self._last_survey: dict[str, int] = {}

        # Set once the startup scan burst has finished. Lives here, on the singleton, rather than on WifiService:
        # oradio_control, web_service and rms_service each construct a WifiService, so a per-instance flag would be
        # set on one object and read on the other, leaving the access point path to wait out its full timeout on a
        # list that is already built. The singleton decorator runs this __init__ exactly once per process, so neither
        # this nor _access_points is ever reset by a later construction.
        self.list_ready = Event()

    def setup(self) -> None:
        """
        Connect to the system bus, find the WiFi device, subscribe to its StateChanged and AccessPointAdded
        signals, seed the access-point list, schedule the keeper sweep, and create the GLib main loop do_work() runs.

        Runs on the worker thread, once per safe_start(). Publishes WIFI_DBUS_FAILED and raises on any failure, so
        ThreadTemplate records the crash and the handles above are left as None for other methods to degrade on.
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

            # Seed the cached AP-mode flag from the live state. _wifi_state_changed maintains it from here on, but
            # signals only report transitions: after a stop/start cycle with the access point already up, nothing
            # would ever announce it and the flag would sit at its False default. That is the one direction that
            # matters, since a false negative lets a keeper sweep scan the radio out from under a connected client.
            self._hosting_ap = get_wifi_connection() == ACCESS_POINT_SSID

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
            #
            # AccessPointAdded only. AccessPointRemoved is not subscribed: NM culls access points it has not seen in
            # a recent scan, and once associated it barely scans, so its removals say nothing about whether the
            # access point still exists. Ageing is owned here instead -- see AP_ENTRY_TTL.
            for signal_name, handler in (
                ("AccessPointAdded", self._access_point_added),
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

        A single blocking call rather than a quick unit of work polled every interval: GLib.MainLoop.run() only returns
        once something calls its quit(), which safe_stop() does. If it ever returned on its own while _stop_event is
        clear, ThreadTemplate's loop would call do_work() again, restarting the event loop.
        """
        # setup() always runs (and sets self._loop) before ThreadTemplate ever calls do_work(); the assert
        # documents/enforces that invariant for mypy, which can't see across the two methods.
        assert self._loop is not None, "do_work() called before setup() completed"
        self._loop.run()

    def safe_stop(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """
        Stop the listener: unblock the GLib loop, join the thread, and undo the registrations setup() made.

        _stop_event is set before quit() so that once run() returns, ThreadTemplate's loop sees the stop request instead
        of calling do_work() and restarting the GLib loop. Unsubscribing comes last, after the join, so no
        signal callback
        can be part-way through when its receiver is removed, and it runs whether or not the join succeeded.

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

        SystemBus() hands back one shared connection per process and the keeper is attached to the default main context,
        so both outlive a stop. Without this, every stop/start cycle stacks another set of receivers on the
        previous ones.

        Idempotent, and safe to call whether or not setup() completed.
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

        Reads the Connectivity property from the top-level NetworkManager D-Bus object. NM maintains this
        value by probing
        after each connection attempt, so no additional network round-trip is made here.

        Returns:
            An integer connectivity code:

            * NM_CONNECTIVITY_NONE (1)    — no network at all
            * NM_CONNECTIVITY_PORTAL (2)  — behind a captive portal
            * NM_CONNECTIVITY_LIMITED (3) — IP connectivity, no internet route
            * NM_CONNECTIVITY_FULL (4)    — full internet access confirmed

            NM_CONNECTIVITY_NONE on any D-Bus error, so an unreadable state reads as no connectivity.
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

        Called once from setup(), immediately after subscribing, because the signals carry only subsequent changes.
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
        # the network count is lower. Counted from the cache rather than from ap_paths, since hidden networks, the
        # Oradio's own access point and any entry without a BSSID are skipped on the way in.
        with self._ap_lock:
            stored = len(self._access_points)

        oradio_log.debug(
            "Seeded %d access points (%d networks)", stored, len(self.get_access_points())
        )

    def _access_point_added(self, ap_path) -> None:
        """
        Record an access point NetworkManager has discovered.

        Runs on the GLib main loop thread, once per AccessPointAdded signal and once per entry from
        _seed_access_points().

        Keyed on BSSID rather than object path, so a rediscovery of an access point NM had culled updates the existing
        entry -- refreshing its path, strength and last_seen -- instead of adding a second entry for the same radio.

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

        # HwAddress is the BSSID. Without one there is no stable key, so the entry is dropped rather than keyed on
        # something that would duplicate on every rediscovery.
        bssid = str(ap_props.get("HwAddress", ""))
        if not bssid:
            return

        secured = bool(
            ap_props.get("Flags", 0) & NM_AP_FLAGS_PRIVACY
            or ap_props.get("WpaFlags", 0)
            or ap_props.get("RsnFlags", 0)
        )

        with self._ap_lock:
            self._access_points[bssid] = {
                "ssid":      ssid,
                "type":      "closed" if secured else "open",
                "signal":    int(ap_props.get("Strength", 0)),
                "path":      str(ap_path),
                "last_seen": monotonic(),
            }

    def _expire_access_points(self) -> None:
        """
        Drop access points NetworkManager has not confirmed within AP_ENTRY_TTL.

        The ageing this module actually wants. NM's own AccessPointRemoved fires as soon as an access point misses a
        couple of scans, which while associated means almost immediately and means nothing about whether it is still
        there; this instead drops an entry only after a full TTL has passed with no confirmation from any scan.

        last_seen is set by _access_point_added() and refreshed by _refresh_signal_strengths() -- a successful read of
        the Strength property proves NM still holds an object for that access point, which is the liveness signal.

        Runs on whichever thread called the read, so the lock covers the whole rebuild.
        """
        cutoff = monotonic() - AP_ENTRY_TTL

        with self._ap_lock:
            expired = [
                bssid for bssid, access_point in self._access_points.items()
                if access_point["last_seen"] < cutoff
            ]
            for bssid in expired:
                ssid = self._access_points.pop(bssid)["ssid"]
                oradio_log.debug("Access point expired after %.0fh: %s (%s)", AP_ENTRY_TTL / 3600, ssid, bssid)

    def _refresh_signal_strengths(self) -> None:
        """
        Re-read the Strength property of every access point in the cache, and use the outcome as a liveness check.

        _access_point_added() captures Strength once, at discovery, and no PropertiesChanged signal is subscribed, so
        without this a stored strength stays frozen at first sight. _strongest_by_ssid() keeps the maximum across an
        SSID's access points and nothing decays, so each SSID would drift toward its historical peak over a long uptime.

        A successful read also proves NM still has an object at that path, so it refreshes last_seen. A failure means
        NM has culled it -- which is not evidence the access point is gone, only that nothing has scanned lately -- so
        the entry is kept with its previous strength and its clock left running, and _expire_access_points() decides.

        No scan and no radio activity: one D-Bus property read per known access point. Skipped while hosting the access
        point, where NM's values can only be stale or zero, so the cache keeps whatever the last refresh left behind.

        The lock is taken only around the dict accesses, never across a D-Bus call: this can run on the GLib main loop
        thread, where a blocking call lets dbus-python pump the loop and dispatch an AccessPointAdded re-entrantly.
        """
        if self.bus is None or self._hosting_ap:
            return

        with self._ap_lock:
            known = [(bssid, access_point["path"]) for bssid, access_point in self._access_points.items()]

        for bssid, ap_path in known:
            try:
                strength = int(
                    Interface(
                        self.bus.get_object(NM_BUS_NAME, ap_path), DBUS_PROPS_IFACE
                    ).Get(NM_AP_IFACE, "Strength")
                )
            except DBusException:
                # Culled by NM. Keep the entry and its last strength; only the TTL removes it.
                continue

            with self._ap_lock:
                access_point = self._access_points.get(bssid)
                if access_point is not None:
                    access_point["signal"] = strength
                    access_point["last_seen"] = monotonic()

        self._expire_access_points()

    def _strongest_by_ssid(self) -> dict[str, dict]:
        """
        Deduplicate the access-point cache by SSID, keeping the strongest entry for each.

        One SSID is commonly served by several access points (2.4 and 5 GHz radios, mesh nodes), each with its own D-Bus
        path and strength. Shared by get_access_points() and _log_signal_survey() so both report the same view.

        Returns:
            A dict of ssid -> access-point entry, one entry per SSID.
        """
        with self._ap_lock:
            access_points = list(self._access_points.values())

        strongest: dict[str, dict] = {}
        for access_point in access_points:
            ssid = access_point["ssid"]
            if ssid not in strongest or access_point["signal"] > strongest[ssid]["signal"]:
                strongest[ssid] = access_point
        return strongest

    def _log_signal_survey(self) -> None:
        """
        Log the strengths behind the network list, and what changed since the previous sweep.

        Temporary instrumentation for choosing a strength threshold (see SIGNAL_SURVEY). Called from the keeper sweep
        before request_scan(), so it describes what the previous sweep found rather than the scan just requested.
        """
        current = {ssid: entry["signal"] for ssid, entry in self._strongest_by_ssid().items()}

        if SIGNAL_SURVEY:
            oradio_log.debug(
                "Signal survey (%d networks): %s",
                len(current),
                ", ".join(
                    f"{ssid}={strength}"
                    for ssid, strength in sorted(current.items(), key=lambda item: item[1], reverse=True)
                ),
            )
        else:
            oradio_log.debug("Keeper sweep requested (%d networks known)", len(current))

        # Logged only on an actual change, so a stable neighbourhood produces nothing at all here.
        appeared = {ssid: strength for ssid, strength in current.items() if ssid not in self._last_survey}
        vanished = {ssid: strength for ssid, strength in self._last_survey.items() if ssid not in current}
        if appeared or vanished:
            oradio_log.debug(
                "Network list churn: appeared [%s], vanished [%s]",
                ", ".join(f"{ssid}={strength}" for ssid, strength in appeared.items()),
                ", ".join(f"{ssid}={strength}" for ssid, strength in vanished.items()),
            )

        self._last_survey = current

    def get_access_points(self) -> list:
        """
        Return the accumulated access points, strongest first.

        Refreshes the stored strengths first (see _refresh_signal_strengths), then deduplicates by SSID.

        Requests no scan and causes no radio activity, so it stays safe to call while the Oradio access point
        is serving a
        client -- the refresh is skipped in that state, making the call a pure cache read there. Not free of
        D-Bus traffic
        otherwise: one property read per known access point.

        Returns:
            A list of {"ssid": str, "type": "open" | "closed"} dicts ordered by descending signal strength.
        """
        self._refresh_signal_strengths()

        return [
            {"ssid": ap["ssid"], "type": ap["type"]}
            for ap in sorted(self._strongest_by_ssid().values(), key=lambda ap: ap["signal"], reverse=True)
        ]

    def request_scan(self) -> bool:
        """
        Ask NetworkManager to scan for access points.

        Goes through nmcli rather than the Device.Wireless RequestScan D-Bus method: that method is gated by the polkit
        action org.freedesktop.NetworkManager.wifi.scan, granted to active local sessions only, and
        oradio_control runs as
        a systemd system service with no session. The nmcli package invokes the binary under sudo, which is authorized.

        Returns as soon as NM accepts the request; the scan completes in the background and its results arrive as
        AccessPointAdded signals.

        Returns:
            True if a scan is now running -- whether this call started it or one was already in progress.
            False if NM will
            not scan at all, in which case no AccessPointAdded signal is coming and there is nothing to wait for.
        """
        # ScanningNotAllowedException means a scan is already running or one finished very recently. Both mean
        # scanning is happening, which is what the caller wants to know, so it counts as success rather than as a
        # refusal -- see the ignore argument of nmcli_try.
        is_ok, _ = nmcli_try(nmcli.device.wifi_rescan, ignore=(ScanningNotAllowedException,))
        return is_ok

    def last_scan(self) -> int | None:
        """
        Return NetworkManager's LastScan timestamp for the WiFi device.

        CLOCK_BOOTTIME milliseconds at the point the last scan completed, or -1 if no scan has ever completed. Only used
        to detect that a scan has finished, by watching for the value to change, so the clock does not matter.

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

        Waiting for LastScan to advance paces each request behind the previous result, so the pacing adapts to however
        long the hardware takes. A full sweep takes several seconds (5 GHz DFS channels must be dwelt on
        passively), and a
        request issued while one is running is either refused or folded into it.

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

        Runs on the GLib main loop thread. NetworkManager scans on its own while the device is disconnected but largely
        stops once associated, so without this the list ages out during long connected periods.

        Strengths are refreshed and surveyed before the scan is requested, not after, because request_scan() returns as
        soon as NM accepts it and the results arrive later as AccessPointAdded signals. Everything logged here therefore
        describes the state the previous sweep left behind.

        Returns:
            True, so GLib keeps rescheduling it. Returning False would cancel the timeout permanently.
        """
        # Never while hosting the access point: a scan there stalls beaconing and can drop the client currently
        # reading the list. Read from the cached flag (see __init__) rather than queried, so a sweep that does
        # nothing costs nothing.
        if self._hosting_ap:
            return True

        self._refresh_signal_strengths()
        self._log_signal_survey()
        self.request_scan()
        return True

    def _wifi_state_changed(self, new_state, _old_state, _reason) -> None:
        """
        Handle a StateChanged D-Bus signal from the WiFi device.

        Runs on the GLib main loop thread. Only the three terminal states that require an application response are acted
        upon; intermediate states are ignored to avoid spurious messages during connection setup. On NM_CONNECTED the
        active SSID is checked first to detect AP mode; otherwise NetworkManager's Connectivity property distinguishes
        full internet access from limited or none.

        Args:
            new_state:  New NM device state code (int).
            _old_state: Previous NM device state code (unused).
            _reason:    NM reason code for the transition (unused).

        Wrapped in a broad except: an uncaught exception here would either crash the whole listener or
        silently drop this
        one state transition, and neither would report anything to Incidents.
        """
        try:
            # Transient states such as PREPARE and CONFIG are excluded.
            if new_state not in (NM_CONNECTED, NM_DISCONNECTED, NM_FAILED):
                return

            if new_state == NM_CONNECTED:
                active = get_wifi_connection()

                # Refresh the cached AP-mode flag read by _keeper_sweep and _refresh_signal_strengths. Only on a
                # successful query: None means the query failed, and the previous value is a better guess than
                # False, since guessing False is what would let a sweep run while hosting.
                if active is not None:
                    self._hosting_ap = active == ACCESS_POINT_SSID

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
                # The radio is no longer associated with anything, so it is certainly not hosting.
                self._hosting_ap = False
                oradio_log.debug("Wifi disconnected")
                Commands.publish(CommandMessage(WIFI_SOURCE, WIFI_DISCONNECTED))

            else:   # NM_FAILED — NetworkManager could not complete the connection
                self._hosting_ap = False
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

    Reads cached state only -- it never triggers a scan -- so it is safe to call while the Oradio access point is
    serving a client, where scanning would drop that client's connection.

    An empty list means either that no networks are in range or that the event listener never started. Check
    wifi_service.WifiService.nm_listener.is_alive() to tell those apart: retrying only helps in the first case.

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
        list fill as scans complete, run a keeper sweep on demand rather than waiting out AP_KEEPER_INTERVAL, and
        check what the listener believes the radio is doing. Use the wifi_service menu instead to test connecting,
        disconnecting and the access point.
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
            " 7-Run a primed pair of keeper sweeps (refresh strengths, log survey and churn)\n"
            " 8-Show active connection as the listener sees it\n"
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
                    # list_ready is set by WifiService._build_network_list, which does not run in this menu, so it
                    # stays clear here however many scans option 5 or 6 completes. Labelled rather than hidden: the
                    # flag lives on this singleton and seeing it stuck at False without explanation reads as a fault.
                    print(
                        f"\nis_alive={listener.is_alive()}, "
                        f"crashed={listener.crashed}, "
                        f"exception={listener.exception}, "
                        f"list_ready={listener.list_ready.is_set()} (set by wifi_service, never by this menu)\n"
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
                    # Normally fires once every AP_KEEPER_INTERVAL (15 minutes), so in production every sweep
                    # surveys a cache the previous sweep's scan finished filling long ago. Called twice here, paced
                    # by scan_and_wait, because one call cannot show that: a sweep surveys first and requests its
                    # scan last, so a single press logs the stale cache it started with and the results land unseen.
                    # The second sweep is the one that reports what the first one's scan found, and the only one
                    # with a readable churn line -- on the first press _last_survey is empty, so every network is
                    # logged as newly appeared.
                    # _keeper_sweep is private by design (GLib is its only other caller); calling it is the point.
                    if listener.bus is None:
                        print(f"\n{YELLOW}Listener not set up; strengths will not be refreshed{NC}\n")
                    print("\nSweep 1 of 2 (priming; its survey describes the cache as found)...\n")
                    # A no-op while the radio hosts the access point -- expected, not a failure: scanning there
                    # would drop the connected client, so neither sweep logs anything.
                    listener._keeper_sweep()    # pylint: disable=protected-access
                    if not listener.scan_and_wait():
                        print(f"{YELLOW}Scan not confirmed complete; sweep 2 may find nothing new{NC}\n")
                    print("Sweep 2 of 2 (reports what sweep 1's scan found)...\n")
                    listener._keeper_sweep()    # pylint: disable=protected-access
                    print(
                        f"{GREEN}Keeper sweeps done{NC}: {len(get_wifi_networks())} networks. "
                        "Signal survey and churn are logged at DEBUG, not printed here.\n"
                    )
                case 8:
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
