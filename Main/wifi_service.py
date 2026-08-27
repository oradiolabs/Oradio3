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
    Import from here, not from wifi_listener: that module is an implementation detail, and the names it owns
    (get_wifi_connection, get_wifi_networks, nm_available) are re-exported below. __all__ lists the supported surface.
    WifiService composes a WifiEventListener (built on ThreadTemplate, utilities.py) and exposes explicit
    start()/stop() methods, so the D-Bus listener thread is only started when the caller asks for it rather than
    as a side effect of construction.
    Access-point timing is owned here: callers start the access point and ask await_access_point() for the verdict
    rather than polling against a deadline of their own.
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

# Maximum seconds to wait for the startup scan burst when the access point is requested before that burst has
# finished. A give-up bound rather than a target: on expiry the access point starts anyway with whatever the list
# holds, because a partial list is more use than no access point.
AP_LIST_READY_TIMEOUT = 35.0

# What a caller must allow for "the access point is up", end to end. Derived rather than chosen, so retuning the
# scan burst moves it without anything else needing to be revisited:
#   AP_LIST_READY_TIMEOUT   worst case before activation is even attempted
# + AP_ACTIVATION_ALLOWANCE nmcli connection up, hostapd beaconing, NM reporting the state change
AP_ACTIVATION_ALLOWANCE = 10.0   # Activation takes ~1.5s; the rest is headroom for a slow or busy radio
AP_START_BUDGET = AP_LIST_READY_TIMEOUT + AP_ACTIVATION_ALLOWANCE

# Granularity of the await_access_point() poll. Sub-second because the access point normally arrives in ~1.5s, and
# the user waits through this at the button press.
AP_STATE_POLL_INTERVAL = 0.25

# Waiting for NetworkManager to appear at startup (see WifiService.start). oradio_control starts after basic.target,
# deliberately ahead of the network being ready, and the listener starts as early as possible so its scan burst
# finishes before anyone can press the button. The margin between NM claiming its D-Bus name and the listener
# starting is under four seconds and is not guaranteed to be positive, so the start waits for NM instead of failing
# and losing the listener for the rest of the boot.
NM_WAIT_TIMEOUT  = 60.0   # Max seconds to wait for NM to claim its bus name
NM_POLL_INTERVAL = 1.0    # Seconds between availability checks while waiting

# How long safe_start() waits for the listener's setup() to signal ready. Longer than ThreadTemplate's own
# JOIN_TIMEOUT default of 5s, which setup() can exceed without having failed: it makes several blocking D-Bus
# round trips and seeds the access-point list, on a cold boot with NetworkManager still settling. Overrunning
# is not free -- _start_listener() then cannot tell a slow start from a failed one -- so the allowance is
# generous. It costs nothing when setup is quick, since safe_start() returns as soon as the thread reports ready.
LISTENER_START_TIMEOUT = 20.0

# Module-level state, shared by every thread in this process. A plain dict behind a threading Lock, so it is
# thread-safe within one process only: a second process gets its own copy and never sees updates made here.
_saved_network = {"network": ""}    # Last successfully connected WiFi SSID
_saved_lock = Lock()                # Guards concurrent reads and writes

##### Helpers #############################################

def _set_saved_network(network) -> None:
    """
    Store the last active WiFi network in a thread-safe manner.

    Stores the SSID string when network is truthy, or an empty string when network is falsy (None, empty string,
    etc.) to signal that no network is saved.

    Args:
        network: The SSID of the network to save, or a falsy value to clear it.
    """
    with _saved_lock:
        _saved_network["network"] = str(network) if network else ""

def _known_ssids() -> set:
    """
    Return the SSIDs currently listed, as a set.

    get_wifi_networks() returns None when there is no maintained list, which is a real distinction for callers
    rendering a network picker but not for _build_network_list, whose only use of the list is to count it: an absent
    list and an empty one both count zero. The sweep loop's own is_alive() check is what reacts to the listener
    being down, and it logs the fact.

    Returns:
        The set of listed SSIDs, empty if there are none or if there is no list.
    """
    networks = get_wifi_networks()
    return {net["ssid"] for net in networks} if networks else set()

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
    added here on the singleton for the same reason: a per-instance copy is written by whichever module acted and
    read as absent by all the others.

    Note:
        The initial Commands.publish happens in start(), not __init__. Error states are never published at start
        time; they are only emitted in response to failed connection attempts.
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

        # Set by stop(), so a deferred start still waiting for NetworkManager aborts instead of bringing the
        # listener up after shutdown.
        self._stopping = Event()

        # Set when starting the access point fails outright, so await_access_point() can answer at once instead of
        # waiting for a state that is never coming. Cleared by wifi_connect() on each new access-point request.
        self._ap_failed = Event()

        # Serialises start(). Its checks and the claim they guard have to be one step, or two callers arriving
        # together both find the listener down and both try to start it. Only oradio_control calls start() in the
        # running Oradio, so the second caller is today either a stand-alone menu or a stop()/start() cycle; the
        # lock is what keeps that from mattering. Held for the decision only, never across the start itself, so a
        # caller is not blocked behind another caller's safe_start(). Always taken before ThreadTemplate's own
        # lifecycle lock, never the other way round.
        self._start_lock = Lock()

        # True from the moment a start() claims the start until that start has finished -- whether it ran here or
        # was handed to the deferred thread, and whether it succeeded, aborted or gave up. It is what makes the
        # window between releasing _start_lock and the listener thread actually being alive safe: without it a
        # second start() in that window sees a listener that is not alive yet and starts a second one.
        self._starting = False

    def start(self, wait: float = NM_WAIT_TIMEOUT) -> None:
        """
        Start the background WiFi event listener thread and publish the current connection state.

        If NetworkManager is already up, the listener starts synchronously and this returns once it is running. If
        NetworkManager is not up yet, starting immediately would only make setup() fail on D-Bus and publish a
        misleading WIFI_DBUS_FAILED, losing the listener -- and with it all state reporting and the network list --
        for the rest of the boot. In that case the start is handed to a background thread that waits for NM to
        appear.

        oradio_control starts after basic.target and the listener starts as early as module initialisation allows,
        so the margin over NM claiming its bus name is only a few seconds and not guaranteed. Waiting turns "started
        too early" into a short delay instead of a lost boot.

        Idempotent: a no-op if the listener is already running, or if another start() is still in progress --
        including one waiting in the background for NetworkManager.

        Args:
            wait: Seconds to keep waiting for NetworkManager in the background. Pass 0 to skip starting entirely
                  when NM is absent, which suits tests and stand-alone runs.
        """
        # The lock covers the decision and the claim, not the work: everything below it can block (safe_start
        # waits up to LISTENER_START_TIMEOUT), and holding the lock through that would make a concurrent start()
        # wait it out rather than return at once on the _starting check.
        with self._start_lock:
            if self.nm_listener.is_alive():
                oradio_log.debug("WiFi event listener thread already running")
                return

            if self._starting:
                oradio_log.debug("WiFi event listener start already in progress")
                return

            # A previous stop() may have set this; clear it so a restart works
            self._stopping.clear()

            # Claimed here, released by _clear_starting() once this start has run its course.
            self._starting = True

        # False until the deferred thread has taken the claim over; it releases it in that case, and the finally
        # below releases it in every other.
        handed_over = False
        try:
            if nm_available():
                self._start_listener()
                return

            if wait <= 0:
                oradio_log.info("NetworkManager not running; WiFi listener not started")
                return

            oradio_log.info("NetworkManager not up yet; deferring WiFi listener start")
            # Daemon thread: exits automatically when the process does.
            Thread(target=self._start_when_nm_ready, args=(wait,), daemon=True).start()
            handed_over = True
        finally:
            if not handed_over:
                self._clear_starting()

    def _clear_starting(self) -> None:
        """
        Release the start claim taken by start().

        Called from whichever context finished the start: start() itself, or the deferred thread it handed over to.
        """
        with self._start_lock:
            self._starting = False

    def _start_when_nm_ready(self, timeout) -> None:
        """
        Wait for NetworkManager to appear, then start the listener.

        Runs on a background thread. Polls rather than watching D-Bus NameOwnerChanged, because receiving that
        signal would itself need a running GLib main loop -- which is what the listener provides and is precisely
        what does not exist yet at this point.

        Args:
            timeout: Maximum seconds to wait before giving up and reporting.
        """
        started = monotonic()
        deadline = started + timeout

        # try/finally so every way out of this thread -- listener started, aborted by stop(), or timed out --
        # releases the claim start() handed over. Leaving it set would make start() a permanent no-op for the
        # rest of the process.
        try:
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
        finally:
            self._clear_starting()

    def _start_listener(self) -> None:
        """
        Bring up the listener thread, run the startup scan burst if it has not run yet, and publish state.

        Called once NetworkManager is known to be available, either directly from start() or from the deferred
        _start_when_nm_ready() thread.
        """
        started_now = self.nm_listener.safe_start(LISTENER_START_TIMEOUT)

        if not started_now:
            # safe_start() answers False to three different questions, and only one of them is an incident. The
            # thread object itself is the discriminator: is_alive() is False only in the last case.
            #   * The listener is already running -- another caller got there first. Nothing to report: the thread
            #     that matters is up. Falling through republishes the current state, which is harmless.
            #   * setup() did not signal ready within LISTENER_START_TIMEOUT. Unlikely at that allowance, but a
            #     thread that is merely slow is still coming up: treating it as a failure would publish
            #     WIFI_DBUS_FAILED prematurely and, worse, skip the scan burst -- leaving list_ready clear, so every
            #     access-point request waits out the full AP_LIST_READY_TIMEOUT. Falling through does not lose a
            #     real failure either: setup() publishes WIFI_DBUS_FAILED itself on every path that raises.
            #   * Thread.start() raised -- the OS would not give us a thread. Nothing is coming up, and this is the
            #     real failure.
            if not self.nm_listener.is_alive():
                oradio_log.error("WiFi event listener thread failed to start")
                Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
                return

            oradio_log.debug("WiFi event listener thread already running or still completing setup")

        # Re-checked here, not just before the start: safe_start() blocks while setup() runs, and a stop() arriving
        # in that window would otherwise be overtaken by the listener it was meant to prevent. Checked after the
        # thread is up rather than before, so what gets torn down is a listener that exists.
        if self._stopping.is_set():
            oradio_log.debug("WiFi listener start overtaken by stop(); stopping again")
            self.nm_listener.safe_stop()
            return

        if self.nm_listener.crashed:
            oradio_log.error(
                "WiFi event listener thread crashed during startup: %s", self.nm_listener.exception,
            )
            Incidents.publish(IncidentMessage(WIFI_SOURCE, WIFI_DBUS_FAILED))
            return

        if started_now:
            oradio_log.info("WiFi event listener thread started")

        # Build the network list in the background, so it is already complete by the time the user asks for the
        # access point. Daemon thread: nothing waits on it and it exits when the process does.
        #
        # Once per start/stop cycle. Within a cycle the list is maintained by the keeper sweep and by
        # AccessPointAdded, so a second start() has nothing to build; stop() clears the flag, because a listener
        # that has been down was maintaining nothing and the list it left behind cannot be trusted.
        if self.nm_listener.list_building.is_set():
            oradio_log.debug("Startup scan burst already running or run; keeping the existing network list")
        else:
            self.nm_listener.list_building.set()
            Thread(target=self._build_network_list, daemon=True).start()

        # Publish the current state immediately so subscribers don't have to wait for the first state-change signal
        # from NetworkManager
        Commands.publish(CommandMessage(WIFI_SOURCE, self.get_state()))

    def _build_network_list(self) -> None:
        """
        Populate the network list with a burst of scans at startup.

        The radio cannot be scanned once the access point is up without risking the connected client, so the list
        has to be right before the user asks for it. Doing that here rather than at the moment of asking is what
        keeps the delay between the button press and the "access point ready" announcement at zero.

        Completeness is not this method's job: listener entries age out on a timescale of hours (AP_ENTRY_TTL), so
        anything missed here is added by a later keeper sweep and then stays. What this method owns is getting most
        of the list up before anyone can press the button.

        Sweeps are paced by scan_and_wait(), which returns when NetworkManager reports the scan complete, so they
        run back to back rather than on a fixed interval.

        Runs on a background thread; sets the listener's list_ready when done, unless stop() cleared list_building
        while it was sweeping, in which case this run has been superseded and reports nothing.
        """
        before = _known_ssids()
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
            # repeatedly adds +0 means AP_SCAN_SWEEPS can be reduced -- but only when read from a cold
            # NetworkManager. NM keeps its access-point list across a restart of this service, so after
            # `systemctl restart` the seed already holds what the previous process just found and every sweep
            # reports +0 regardless of merit. Judge this figure from reboots, or after restarting NetworkManager
            # alongside oradio.
            previous, found = found, _known_ssids()
            oradio_log.debug(
                "Sweep %d of %d at %.1fs: %d networks (+%d)%s",
                sweep, AP_SCAN_SWEEPS, monotonic() - started, len(found), len(found - previous),
                "" if completed else " [not confirmed complete]",
            )

        # Set even if the burst was cut short: waiting longer would not help, and blocking the access point
        # indefinitely is worse than an incomplete list. Not set if stop() cleared list_building while this was
        # sweeping, though: that run belongs to a torn-down listener, and the next start() means to build the
        # list again rather than serve what this one happened to reach.
        if not self.nm_listener.list_building.is_set():
            oradio_log.debug("Startup scan burst superseded by stop(); network list not reported ready")
            return

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

        Clears the startup-burst flags, so the next start() builds the network list again. A listener that has
        been stopped was maintaining nothing while it was down -- no keeper sweeps, no AccessPointAdded -- and
        NetworkManager may have been restarted underneath it, so what the list holds on the way back up is not
        evidence of what is on air now.
        """
        self._stopping.set()
        self.nm_listener.safe_stop()

        # After safe_stop(), so the burst thread has already seen the listener go down and broken out of its
        # sweep loop. It checks list_building before setting list_ready, so a sweep still in flight cannot
        # report readiness for a list this stop has just invalidated.
        self.nm_listener.list_building.clear()
        self.nm_listener.list_ready.clear()

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
        _wait_for_network_list), and await_access_point() reports whether the access point came up.

        Args:
            ssid: SSID of the network to connect to.
            pswd: Password for the network; empty string for open networks.
        """
        active = get_wifi_connection()

        # Cleared before anything can fail, so a waiter that arrives late sees this request's outcome rather than
        # the previous one's.
        if ssid == ACCESS_POINT_SSID:
            self._ap_failed.clear()

        # Remember the last non-AP, non-empty connection so it can be restored later
        if active and active != ACCESS_POINT_SSID:
            oradio_log.info("Remember connection '%s'", active)
            _set_saved_network(active)

        # Ensure the NetworkManager profile exists and has the correct credentials
        if not networkmanager_add(ssid, pswd):
            oradio_log.error("Publish wifi service error")
            # Nothing will be activated, so anyone waiting on the access point is waiting for nothing.
            if ssid == ACCESS_POINT_SSID:
                self._ap_failed.set()
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
        is_access_point = network == ACCESS_POINT_SSID

        # The list must be complete before the access point takes the radio, since scanning afterwards risks the
        # connected client. It normally already is, so this does not delay activation.
        if is_access_point:
            self._wait_for_network_list()

        if not _wifi_up(network):
            # Activation failed; clean up the broken profile
            networkmanager_del(network)     # includes its own error logging
            # Told rather than inferred from a timeout, so await_access_point() answers now instead of sitting out
            # the whole of AP_START_BUDGET waiting for a state this thread knows is not coming.
            if is_access_point:
                self._ap_failed.set()
        else:
            # Connection is up; WifiEventListener will publish the new state
            oradio_log.info("Connected with '%s'", network)

    def await_access_point(self, timeout: float = AP_START_BUDGET) -> bool:
        """
        Block until the Oradio access point is up, and report whether it made it.

        This is all a caller needs to know about access-point timing. Starting the access point is asynchronous --
        wifi_connect() returns as soon as the profile exists and hands activation to a thread -- and that thread may
        spend most of its time deliberately waiting for the network list to finish building (see
        _wait_for_network_list). A caller polling for the state itself cannot distinguish that wait from a failure,
        so the module doing the waiting is the one that answers for it.

        Three ways out, in order of how quickly they arrive:
            * the access point is already up, or comes up   -> True, typically within ~1.5s
            * activation failed outright                    -> False, as soon as the connect thread reports it
            * neither happened within timeout               -> False

        Safe to call without a preceding wifi_connect(): it reports on whatever the radio is doing, so a caller that
        finds the access point already running is not made to wait for a request it never sent.

        Args:
            timeout: Seconds to wait. Defaults to AP_START_BUDGET, which is derived from the list wait plus an
                     activation allowance and is the value callers should use unless they have a reason not to.

        Returns:
            True if the access point is up, False on failure or timeout.
        """
        deadline = monotonic() + timeout
        started = monotonic()

        while True:
            if self.get_state() == WIFI_ACCESS_POINT:
                oradio_log.debug("Access point up after %.1fs", monotonic() - started)
                return True

            # Checked after the state, so an access point that came up despite a reported failure counts as up.
            if self._ap_failed.is_set():
                oradio_log.error("Access point failed to start")
                return False

            if monotonic() >= deadline:
                oradio_log.error("Access point not up within %.0fs", timeout)
                return False

            sleep(AP_STATE_POLL_INTERVAL)

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

        The flag is read from the listener singleton rather than from this WifiService, so every caller sees the
        same one (see WifiEventListener.list_ready).
        """
        if self.nm_listener.list_ready.is_set():
            oradio_log.debug("Network list already built; starting access point")
            return

        oradio_log.info("Waiting for initial network scan to complete")
        started = monotonic()

        if self.nm_listener.list_ready.wait(AP_LIST_READY_TIMEOUT):
            oradio_log.info("Network list ready after %.1fs", monotonic() - started)
        else:
            # Bounded rather than indefinite: an access point with a partial list is more use than no access point
            # at all.
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

    Written by wifi_connect() when it replaces a live non-AP connection, so it stays empty until a connect has
    actually displaced one.

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
        The password string, empty for an open network that has a profile but no passphrase, or None if the profile
        is not found or the command fails.
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
    already exist. For all other SSIDs, adds a new profile or modifies the existing one with the supplied
    credentials.

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
        AP mode, direct NetworkManager profile management, and the saved-network and stored-password lookups. One
        option per entry in __all__, so a name added there without an option here shows up as a gap. Use the
        wifi_listener menu to exercise the listener on its own, including scanning: no option here requests a scan,
        since the startup burst is the service's own business and runs on start().
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
                    # later, with nothing on screen to connect it to this key press. At a prompt, where NM is either
                    # up or not coming, saying so at once is more use.
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
                    # None and [] mean different things (see get_wifi_networks): no maintained list, versus a list
                    # that is genuinely empty.
                    networks = get_wifi_networks()
                    if networks is None:
                        print(f"\n{RED}WiFi monitor is not running; no network list available{NC}\n")
                    elif not networks:
                        print(f"\n{YELLOW}No networks in range{NC}\n")
                    else:
                        print(f"\nActive wifi networks: {networks}\n")
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
                    # Blocking, unlike the other connect options: this is the path web_service takes, where the
                    # caller gets a verdict rather than a timing constant of its own.
                    wifi_service.wifi_connect(ACCESS_POINT_SSID, None)
                    print(f"\nStarting access point '{ACCESS_POINT_SSID}'...\n")
                    if wifi_service.await_access_point():
                        print(f"\n{GREEN}Access point '{ACCESS_POINT_SSID}' is up{NC}\n")
                    else:
                        print(f"\n{RED}Access point '{ACCESS_POINT_SSID}' failed to start{NC}\n")
                case 10:
                    print("\nDisconnecting. Check messages for result\n")
                    wifi_service.wifi_disconnect()
                case 11:
                    listener = wifi_service.nm_listener
                    print(
                        f"\nis_alive={listener.is_alive()}, "
                        f"crashed={listener.crashed}, "
                        f"exception={listener.exception}, "
                        f"nm_connected={listener.nm_connected}\n"
                    )
                case 12:
                    if nm_available():
                        print(f"\n{GREEN}NetworkManager is available{NC}\n")
                    else:
                        print(f"\n{RED}NetworkManager is not available{NC}\n")
                case 13:
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
