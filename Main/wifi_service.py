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
@summary:       WiFi connectivity service: the public face of WiFi for the rest of the Oradio.
    This module acts; wifi_listener.py observes. Connecting, disconnecting, hosting the access point and managing
    NetworkManager connection profiles all live here, as does every name other modules are meant to import. State
    changes are reported on the messaging bus by the WifiEventListener that this module starts and stops.
    This is the facade: import from here, not from wifi_listener. The listener module is an implementation detail,
    and the names it owns (get_wifi_connection, get_wifi_networks, nm_available) are re-exported below so callers
    never need to know it exists. __all__ lists the supported surface.
    WifiService composes a WifiEventListener (built on ThreadTemplate, utilities.py) and exposes explicit
    start()/stop() methods, so the D-Bus listener thread is only started when the caller asks for it rather than
    as a side effect of construction.
    Documentation:
        https://networkmanager.dev/
        https://pypi.org/project/nmcli/
        https://superfastpython.com/multiprocessing-in-python/
    Not supported:
        Connecting through a captive portal (detected but not handled).
        Connecting to VPN.
"""
from time import sleep, monotonic
from threading import Thread, Lock, Event
import nmcli

##### Oradio modules ######################################
from singleton import singleton
from log_service import oradio_log
from utilities import run_shell_script
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
    WIFI_DISCONNECT_FAILED,
)
from wifi_listener import (
    AP_SCAN_SWEEPS,
    WifiEventListener,
    nm_available,
    nmcli_try,
    get_wifi_connection,
    get_wifi_networks,
)

##### GLOBAL constants ####################################
from constants import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
)

# Supported import surface. get_wifi_connection, get_wifi_networks and nm_available are implemented in wifi_listener
# and re-exported here so callers have one module to import from; listing them keeps pylint from reporting the
# re-export as an unused import.
__all__ = [
    "WifiService",
    "get_saved_network",
    "get_wifi_networks",
    "get_wifi_connection",
    "get_wifi_password",
    "nm_available",
    "networkmanager_list",
    "networkmanager_add",
    "networkmanager_del",
]

##### LOCAL constants #####################################

# Maximum seconds to wait for the startup burst when the access point is requested before it has finished (button
# pressed seconds after power-on). This is a give-up bound: on expiry the access point starts anyway with whatever
# the list holds, because a partial list is more use than no access point.
#
# Burst duration, for reference when tuning. Sweeps run back to back with no gap between them, and SCAN_POLL_INTERVAL
# only sets how finely each wait is sampled, so it adds nothing on top:
#   typical   AP_SCAN_SWEEPS * ~9s                    = ~18s   each sweep completes and the next starts at once
#   worst     AP_SCAN_SWEEPS * SCAN_COMPLETE_TIMEOUT  = ~40s   every sweep is accepted but never completes
# The worst case needs a wedged driver: a sweep whose scan request is refused outright returns at once rather than
# waiting (see scan_and_wait), so an unavailable radio costs no time here at all.
#
# Three values have to stay in this order, or the access point is reported as failed while it is merely waiting:
#   typical burst (~18s)  <  AP_LIST_READY_TIMEOUT (35s)  <  WIFI_STATE_TIMEOUT (45s, web_service.py)
# This one sits above the typical burst so the list is normally complete before the access point starts, and below
# WIFI_STATE_TIMEOUT with room for the ~1.5s the access point itself takes to come up and the 1s poll granularity on
# that side. Changing AP_SCAN_SWEEPS or SCAN_COMPLETE_TIMEOUT moves the first figure, so revisit all three together.
# Dropping from three sweeps to two widened the margin here rather than narrowing it; the value is left at 35s
# because it is a give-up bound, not a target, and the room now absorbs a slow sweep instead of a missing one.
AP_LIST_READY_TIMEOUT = 35.0

# Waiting for NetworkManager to appear at startup (see WifiService.start). oradio_control starts after basic.target,
# deliberately ahead of the network being ready, and the listener starts as early as possible so its scan burst
# finishes before anyone can press the button. The margin between NM claiming its D-Bus name and the listener starting
# is under four seconds and is not guaranteed to be positive, so the start waits for NM instead of failing and losing
# the listener for the rest of the boot.
NM_WAIT_TIMEOUT  = 60.0   # Max seconds to wait for NM to claim its bus name
NM_POLL_INTERVAL = 1.0    # Seconds between availability checks while waiting

# Module-level state, shared by every thread in this process. A plain dict behind a threading Lock, so it is
# thread-safe within one process only: a second process gets its own copy and never sees updates made here.
_saved_network = {"network": ""}    # Last successfully connected WiFi SSID
_saved_lock = Lock()                # Guards concurrent reads and writes

##### Helpers #############################################

def _set_saved_network(network) -> None:
    """
    Store the last active WiFi network in a thread-safe manner.

    Stores the SSID string when network is truthy, or an empty string when network is falsy (None, empty string, etc.)
    to signal that no network is saved.

    Args:
        network: The SSID of the network to save, or a falsy value to clear it.
    """
    with _saved_lock:
        _saved_network["network"] = str(network) if network else ""

def _wifi_up(network) -> bool:
    """
    Activate a NetworkManager connection by SSID.

    Args:
        network: SSID of the connection profile to bring up.

    Returns:
        True if activation succeeded, False otherwise.
    """
    oradio_log.debug("Activate '%s'", network)
    is_ok, _ = nmcli_try(nmcli.connection.up, network)
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
    is_ok, _ = nmcli_try(nmcli.connection.down, network)
    return is_ok

@singleton
class WifiService:
    """
    Manage WiFi connection state and expose connect/disconnect operations.

    Tracks four possible states: connected with internet, connected to the Oradio access point, disconnected, and
    connection failed. State changes are reported on the command message bus by the WifiEventListener singleton;
    this class handles the active operations that trigger them.

    Construction only sets up state; the background D-Bus listener thread is not started until start() is called.

    Singleton, because oradio_control, web_service and rms_service each construct one and they must not diverge: the
    network list, its readiness flag and the listener thread describe one radio, not one per caller. Keep any state
    added here on the singleton for the same reason: a per-instance copy is written by whichever module acted and read
    as absent by all the others.

    Note:
        The initial Commands.publish happens in start(), not __init__. Error states are never published at start time;
        they are only emitted in response to failed connection attempts.
    """
    def __init__(self) -> None:
        """
        Create (but do not start) the WifiEventListener singleton.

        The singleton decorator ensures this constructor runs at most once per process. Callers must call start()
        explicitly to begin monitoring D-Bus state changes, and may stop()/start() again later since the listener
        is restartable.
        """
        # Singleton D-Bus listener; every WifiService shares this one instance.
        self.nm_listener = WifiEventListener()

        # Set by stop(), so a deferred start still waiting for NetworkManager aborts instead of bringing the listener
        # up after shutdown.
        self._stopping = Event()

    def start(self, wait: float = NM_WAIT_TIMEOUT) -> None:
        """
        Start the background WiFi event listener thread and publish the current connection state.

        If NetworkManager is already up, the listener starts synchronously and this returns once it is running. If
        NetworkManager is not up yet, starting immediately would only make setup() fail on D-Bus and publish a
        misleading WIFI_DBUS_FAILED, losing the listener -- and with it all state reporting and the network list --
        for the rest of the boot. In that case the start is handed to a background thread that waits for NM to appear.

        oradio_control starts after basic.target and the listener starts as early as module initialisation allows, so
        the margin over NM claiming its bus name is only a few seconds and not guaranteed. Waiting turns "started too
        early" into a short delay instead of a lost boot.

        Idempotent: a no-op if the listener is already running.

        Args:
            wait: Seconds to keep waiting for NetworkManager in the background. Pass 0 to skip starting entirely when
                  NM is absent, which suits tests and stand-alone runs.
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

        Runs on a background thread. Polls rather than watching D-Bus NameOwnerChanged, because receiving that signal
        would itself need a running GLib main loop -- which is what the listener provides and is precisely what does
        not exist yet at this point.

        Args:
            timeout: Maximum seconds to wait before giving up and reporting.
        """
        started = monotonic()
        deadline = started + timeout

        while monotonic() < deadline:
            # Checked before sleeping and after waking, so stop() takes effect within one poll interval at worst.
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
            # NM never appeared: masked, disabled or failed to start. Unlike the transient absence above, that is
            # worth an incident.
            oradio_log.error("NetworkManager did not appear within %.0fs", timeout)
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))

    def _start_listener(self) -> None:
        """
        Bring up the listener thread, start the scan burst and publish state.

        Called once NetworkManager is known to be available, either directly from start() or from the deferred
        _start_when_nm_ready() thread.
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

        # Build the network list now, in the background, so it is already complete by the time the user asks for the
        # access point. Started only here, so the GLib loop is running and the resulting AccessPointAdded signals are
        # actually delivered. Daemon thread: nothing waits on it and it exits when the process does.
        Thread(target=self._build_network_list, daemon=True).start()

        # Publish the current state immediately so subscribers don't have to wait for the first state-change signal
        # from NetworkManager
        Commands.publish(CommandMessage(WIFI_SOURCE, self.get_state()))

    def _build_network_list(self) -> None:
        """
        Populate the network list with a burst of scans at startup.

        The radio cannot be scanned once the access point is up without risking the connected client, so the list has
        to be right before the user asks for it. Doing that here rather than at the moment of asking is what keeps the
        delay between the button press and the "access point ready" announcement at zero.

        Two sweeps rather than one because a single scan misses access points that beacon while the radio is on
        another channel; not more than two because the burst samples the neighbourhood rather than converging on it
        (see AP_SCAN_SWEEPS). Completeness is no longer this method's job: listener entries age out on a timescale of
        hours (AP_ENTRY_TTL), so anything missed here is added by a later keeper sweep and then stays. What this
        method owns is getting most of the list up before anyone can press the button.

        Runs on a background thread; sets the listener's list_ready when done.
        """
        before = {net["ssid"] for net in get_wifi_networks()}
        started = monotonic()
        found = before

        oradio_log.debug(
            "Building network list: %d sweeps (%d networks known)", AP_SCAN_SWEEPS, len(before)
        )

        for sweep in range(1, AP_SCAN_SWEEPS + 1):
            # Without the listener there is nothing to collect the results: the scans would run but no
            # AccessPointAdded signal would arrive.
            if not self.nm_listener.is_alive():
                oradio_log.warning("Listener not running; network list may be incomplete")
                break

            completed = self.nm_listener.scan_and_wait()

            # Measured after the scan is reported complete, so the gain is genuinely this sweep's. A sweep that
            # repeatedly adds +0 means AP_SCAN_SWEEPS can be reduced -- but only when read from a cold NetworkManager.
            # NM keeps its access-point list across a restart of this service, so after `systemctl restart` the seed
            # already holds what the previous process just found and every sweep reports +0 regardless of merit. Judge
            # this figure from reboots, or after restarting NetworkManager alongside oradio.
            previous, found = found, {net["ssid"] for net in get_wifi_networks()}
            oradio_log.debug(
                "Sweep %d of %d at %.1fs: %d networks (+%d)%s",
                sweep, AP_SCAN_SWEEPS, monotonic() - started, len(found), len(found - previous),
                "" if completed else " [not confirmed complete]",
            )

        # Set even if the burst was cut short: waiting longer would not help, and blocking the access point
        # indefinitely is worse than an incomplete list.
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

        WifiEventListener.safe_stop() unblocks its own blocking GLib loop.run() call before joining.

        Also cancels a deferred start still waiting for NetworkManager, so the listener cannot come up
        after shutdown was requested.
        """
        self._stopping.set()
        self.nm_listener.safe_stop()

    def get_state(self) -> str:
        """
        Return the current WiFi connection state.

        Performs a direct check of the active connection rather than relying on any cached state.

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

        Saves the current connection (if any, and not the AP) so it can be restored later, then starts a
        daemon Thread to activate the profile so the blocking nmcli call does not stall the caller.

        When ssid is the Oradio access point, that thread first confirms the network list is complete (see
        _wait_for_network_list). That normally returns immediately, so the access point comes up without
        added delay; it waits only when the button is pressed before the startup scan has finished.

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

        # Offload the blocking activation to a daemon thread so the caller is not stalled by it
        Thread(target=self._wifi_connect_thread, args=(ssid,), daemon=True).start()
        oradio_log.info("Connecting to '%s' started", ssid)

    def _wifi_connect_thread(self, network) -> None:
        """
        Activate the given network profile (runs in a background thread).

        On failure the broken profile is removed from NetworkManager;
        on success WifiEventListener publishes the resulting WiFi state.

        Args:
            network: SSID of the NetworkManager connection profile to activate.
        """
        # The list must be complete before the access point takes the radio, since scanning afterwards risks the
        # connected client. It normally already is, so this does not delay activation.
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

        Normally returns immediately: the list was built by _build_network_list() at startup and has been kept
        accurate by the listener's keeper sweeps ever since, so nothing needs to happen here and the access point
        comes up without delay.

        Only when the access point is requested within seconds of power-on, before the startup burst has finished,
        does this wait -- which is the one case where a delay is worth it, since the alternative is serving the user
        a list that is empty or half built.

        No scanning happens here. Scanning at this point would put the delay back into the path between the button
        press and the "access point ready" announcement, which is exactly what building the list in advance avoids.

        The flag is read from the listener singleton rather than from this WifiService, so every caller sees the same
        one (see WifiEventListener.list_ready).
        """
        if self.nm_listener.list_ready.is_set():
            oradio_log.debug("Network list already built; starting access point")
            return

        oradio_log.info("Waiting for initial network scan to complete")
        started = monotonic()

        if self.nm_listener.list_ready.wait(AP_LIST_READY_TIMEOUT):
            oradio_log.info("Network list ready after %.1fs", monotonic() - started)
        else:
            # Bounded rather than indefinite: an access point with a partial list is more use than no access point at
            # all.
            oradio_log.warning(
                "Initial scan not complete after %.0fs; starting access point anyway",
                AP_LIST_READY_TIMEOUT,
            )

    def wifi_disconnect(self) -> None:
        """
        Disconnect the currently active WiFi connection, if any.

        WifiEventListener will publish WIFI_DISCONNECTED once the state-change signal arrives.
        Does nothing if already disconnected.
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
    Return the last active WiFi network in a thread-safe manner.

    Returns:
        The SSID of the last saved network, or an empty string if none has been saved yet
        or the saved value was cleared.
    """
    with _saved_lock:
        return _saved_network["network"]

def get_wifi_password(network) -> str | None:
    """
    Return the stored password for a NetworkManager connection profile.

    Args:
        network: SSID of the connection profile as stored in NetworkManager.

    Returns:
        The password string, or None if the profile is not found or the command fails.
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
        A list of connection name strings (one per WiFi profile),
        or an empty list if the query fails or none are configured.
    """
    oradio_log.debug("Get connections from NetworkManager")

    is_ok, result = nmcli_try(nmcli.connection)

    if not is_ok or result is None:
        return []

    # Filter to WiFi-type connections only; other types (ethernet, VPN) are not relevant
    return [connection.name for connection in result if connection.conn_type == "wifi"]

def networkmanager_add(network, password=None) -> bool:
    """
    Add or update a WiFi connection profile in NetworkManager.

    For the Oradio access point SSID, creates an AP-mode profile with a shared IPv4 configuration if one does not
    already exist. For all other SSIDs, adds a new profile or modifies the existing one with the supplied credentials.

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
        is_ok, _ = nmcli_try(nmcli.connection.add, "wifi", options, "*", ACCESS_POINT_SSID, False)
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
        is_ok, _ = nmcli_try(nmcli.connection.modify, network, options)
        return is_ok

    # Profile does not exist; create a new one
    oradio_log.debug("Add '%s' to NetworkManager", network)
    is_ok, _ = nmcli_try(nmcli.connection.add, "wifi", options, "*", network, True)
    return is_ok

def networkmanager_del(network) -> bool:
    """
    Remove a WiFi connection profile from NetworkManager.

    Used after a failed connection attempt so no broken profile is left behind,
    and available to callers that need to forget a network.

    Args:
        network: SSID of the connection profile to delete.

    Returns:
        True if deletion succeeded, False otherwise.
    """
    oradio_log.debug("Remove '%s' from NetworkManager", network)
    is_ok, _ = nmcli_try(nmcli.connection.delete, network)
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

        Loops until the user selects quit (0). Covers every name in __all__: start/stop, connecting, disconnecting,
        AP mode, direct NetworkManager profile management, and the saved-network and stored-password lookups.
        Everything here goes through this module's public surface, including the names re-exported from
        wifi_listener, so it doubles as a check that the facade is complete -- one option per entry in __all__, so
        a name added there without an option here shows up as a gap. Use the wifi_listener menu to exercise the
        listener on its own, including scanning: no option here requests a scan, since the startup burst is the
        service's own business and runs on start().
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
            " 12-show whether NetworkManager is available\n"
            " 13-show saved (last connected) network\n"
            " 14-show stored password for a network\n"
            "Select: "
        )

        # Construct the service; WifiEventListener's D-Bus listener thread is not started until wifi_service.start()
        # is called (option 1).
        wifi_service = WifiService()

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    wifi_service.stop()  # Ensure nothing is left running on exit
                    break
                case 1:
                    # Checked here rather than left to start(), which would hand an absent NetworkManager to a
                    # background thread that waits NM_WAIT_TIMEOUT and then publishes WIFI_DBUS_FAILED -- a minute
                    # later, with nothing on screen to connect it to this key press. On the Oradio that wait is the
                    # right behaviour (oradio_control starts deliberately ahead of the network); at a prompt, where
                    # NM is either up or not coming, saying so at once is more use.
                    if not nm_available():
                        print(f"\n{RED}NetworkManager is not running; WiFi monitor not started{NC}\n")
                    else:
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
                    wifi_service.wifi_connect(ACCESS_POINT_SSID, None)
                    print(f"\nStarting access point '{ACCESS_POINT_SSID}'. Check messages for result\n")
                case 10:
                    print("\nDisconnecting. Check messages for result\n")
                    wifi_service.wifi_disconnect()
                case 11:
                    listener = wifi_service.nm_listener
                    print(
                        f"\nis_alive={listener.is_alive()}, "
                        f"crashed={listener.crashed}, "
                        f"exception={listener.exception}\n"
                    )
                case 12:
                    if nm_available():
                        print(f"\n{GREEN}NetworkManager is available{NC}\n")
                    else:
                        print(f"\n{RED}NetworkManager is not available{NC}\n")
                case 13:
                    # Written by wifi_connect() when it replaces a live non-AP connection, so it stays empty until a
                    # connect has actually displaced one. Module-level state, shared per process only.
                    saved = get_saved_network()
                    print(f"\nSaved network: '{saved}'\n" if saved else f"\n{YELLOW}No network saved{NC}\n")
                case 14:
                    name = input("Enter SSID of the network to look up: ")
                    if name:
                        password = get_wifi_password(name)
                        if password is None:
                            print(f"\n{RED}No stored password for '{name}'{NC}\n")
                        else:
                            # Empty is a legitimate answer: an open network has a profile but no passphrase.
                            print(f"\nPassword for '{name}': '{password}'\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
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
