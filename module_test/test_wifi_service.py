#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on August 27, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Unit tests for wifi_service.
    Runs anywhere: no NetworkManager, no D-Bus, no radio and no message bus. Everything wifi_service talks to
    on the far side of its imports is replaced by a stub installed in sys.modules before the module is imported
    (see _install_stubs), so what is under test is wifi_service's own logic -- who may start the listener, when
    the scan burst runs, what gets published -- and nothing else.

    FakeListener stands in for WifiEventListener. It records the calls wifi_service makes rather than doing
    anything, and its list_ready / list_building Events are real, since the code under test coordinates on them.

    The singleton decorator is deliberately bypassed for most tests: _new_service() builds a fresh WifiService
    per test so one test's listener state cannot leak into the next. TestSingleton covers the decorated
    behaviour on its own.

    Run:
        python3 -m unittest test_wifi_service -v
        python3 test_wifi_service.py
"""
import sys
import types
import unittest
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from unittest.mock import patch

# The modules under test sit next to this file
sys.path.insert(0, str(Path(__file__).resolve().parent))

##### Stubs for everything outside wifi_service ###########

# Access point identity used throughout the tests. Matches constants.ACCESS_POINT_SSID in shape only; the tests
# always refer to wifi_service.ACCESS_POINT_SSID rather than to this literal.
STUB_AP_SSID = "OradioAP"
STUB_AP_HOST = "192.168.4.1"


class StubMessage:
    """Stand-in for CommandMessage / IncidentMessage: keeps what it was told, nothing else."""

    def __init__(self, source, value) -> None:
        self.source = source
        self.value = value

    def __repr__(self) -> str:
        return f"StubMessage({self.source!r}, {self.value!r})"


class StubBus:
    """Stand-in for Commands / Incidents: records published messages for assertions."""

    def __init__(self) -> None:
        self.messages = []
        self._lock = Lock()

    def publish(self, message) -> None:
        """Record a published message."""
        with self._lock:
            self.messages.append(message)

    def clear(self) -> None:
        """Forget everything published so far."""
        with self._lock:
            self.messages.clear()

    def values(self) -> list:
        """The value of every message published, in order."""
        with self._lock:
            return [message.value for message in self.messages]


COMMANDS = StubBus()
INCIDENTS = StubBus()


class StubNmcliConnection:
    """Callable stand-in for nmcli.connection, with the sub-calls wifi_service uses."""

    def __init__(self) -> None:
        self.profiles = []      # objects with .name and .conn_type, returned by the call itself
        self.calls = []         # (operation, args) in order

    def __call__(self):
        self.calls.append(("list", ()))
        return self.profiles

    def up(self, name) -> None:
        """Record an activation."""
        self.calls.append(("up", (name,)))

    def down(self, name) -> None:
        """Record a deactivation."""
        self.calls.append(("down", (name,)))

    def add(self, *args) -> None:
        """Record a profile creation."""
        self.calls.append(("add", args))

    def modify(self, *args) -> None:
        """Record a profile update."""
        self.calls.append(("modify", args))

    def delete(self, name) -> None:
        """Record a profile removal."""
        self.calls.append(("delete", name))


class FakeListener:
    """
    Stand-in for WifiEventListener.

    Records what wifi_service asks of it and reports whatever the test has set up. The two Events are real,
    because the burst and the access-point path coordinate on them.
    """

    def __init__(self) -> None:
        self.list_ready = Event()
        self.list_building = Event()

        # What the tests set
        self.safe_start_result = None   # None: succeed and go alive. True/False: return that, leave alive alone
        self.safe_start_delay = 0.0     # Seconds safe_start() blocks, to widen races on purpose
        self.crashed = False
        self.exception = None

        # What the tests read
        self.safe_start_timeouts = []   # One entry per safe_start() call, holding the timeout it was passed
        self.safe_stop_calls = 0
        self.scan_calls = 0

        self._alive = False
        self._lock = Lock()

    def is_alive(self) -> bool:
        """Whether the fake thread is running."""
        return self._alive

    def safe_start(self, timeout=5.0) -> bool:
        """Record the call and report the result the test asked for."""
        with self._lock:
            self.safe_start_timeouts.append(timeout)
        if self.safe_start_delay:
            sleep(self.safe_start_delay)
        if self.safe_start_result is None:
            self._alive = True
            return True
        return self.safe_start_result

    def safe_stop(self, timeout=5.0) -> bool:      # pylint: disable=unused-argument
        """Record the call and go not-alive."""
        with self._lock:
            self.safe_stop_calls += 1
        self._alive = False
        return True

    def scan_and_wait(self, timeout=None) -> bool:  # pylint: disable=unused-argument
        """Count a sweep."""
        with self._lock:
            self.scan_calls += 1
        return True

    # --- test helpers ---

    def set_alive(self, alive: bool) -> None:
        """Force the alive state without going through safe_start()/safe_stop()."""
        self._alive = alive


def _install_stubs() -> None:
    """
    Put stand-ins for wifi_service's hardware-facing imports into sys.modules.

    Called before wifi_service is imported, so its `from x import y` lines pick these up. singleton is imported
    for real: its behaviour is part of what TestSingleton checks. constants is used for real when it imports
    cleanly, and stubbed when it does not, so this file still runs off a Raspberry Pi.
    """
    log_service = types.ModuleType("log_service")
    import logging                                  # pylint: disable=import-outside-toplevel
    logger = logging.getLogger("test_wifi_service")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    log_service.oradio_log = logger
    sys.modules["log_service"] = log_service

    utilities = types.ModuleType("utilities")
    utilities.run_shell_script = lambda cmd: (True, "")
    sys.modules["utilities"] = utilities

    messaging = types.ModuleType("messaging")
    messaging.Commands = COMMANDS
    messaging.Incidents = INCIDENTS
    messaging.CommandMessage = StubMessage
    messaging.IncidentMessage = StubMessage
    messaging.WIFI_SOURCE = "wifi"
    messaging.WIFI_CONNECTED = "wifi connected"
    messaging.WIFI_DISCONNECTED = "wifi disconnected"
    messaging.WIFI_ACCESS_POINT = "wifi access point"
    messaging.WIFI_DBUS_FAILED = "wifi dbus failed"
    messaging.WIFI_DISCONNECT_FAILED = "wifi disconnect failed"
    sys.modules["messaging"] = messaging

    nmcli = types.ModuleType("nmcli")
    nmcli.connection = StubNmcliConnection()
    sys.modules["nmcli"] = nmcli

    listener = types.ModuleType("wifi_listener")
    listener.AP_SCAN_SWEEPS = 3
    listener.WifiEventListener = FakeListener
    listener.nm_available = lambda: True
    listener.get_wifi_connection = lambda: None
    listener.get_wifi_networks = lambda: []

    def nmcli_try(func, *args, ignore=(), **kwargs):
        """Minimal stand-in: call it, report success unless it raises."""
        try:
            return True, func(*args, **kwargs)
        except ignore:
            return True, None
        except Exception:                           # pylint: disable=broad-exception-caught
            return False, None

    listener.nmcli_try = nmcli_try
    sys.modules["wifi_listener"] = listener

    try:
        import constants                            # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except Exception:                               # pylint: disable=broad-exception-caught
        constants = types.ModuleType("constants")
        constants.ACCESS_POINT_HOST = STUB_AP_HOST
        constants.ACCESS_POINT_SSID = STUB_AP_SSID
        sys.modules["constants"] = constants


_install_stubs()

import wifi_service                                 # noqa: E402  pylint: disable=wrong-import-position

##### Helpers #############################################

# Every test waits on a background thread at some point; 2s is far longer than any of them needs.
WAIT_TIMEOUT = 2.0


def wait_until(predicate, timeout=WAIT_TIMEOUT) -> bool:
    """
    Poll until predicate() is true.

    Args:
        predicate: Callable returning something truthy when the wait is over.
        timeout: Maximum seconds to wait.

    Returns:
        True if the predicate became true within timeout, False otherwise.
    """
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.005)
    return bool(predicate())


def new_service() -> "wifi_service.WifiService":
    """
    Build a WifiService that is not the singleton instance.

    The decorator patches __new__ and __init__ in place, and __init__ returns early once _initialized is in the
    instance dict. Allocating with object.__new__ bypasses the patched __new__, and the fresh dict means the
    patched __init__ runs the real one. Each test therefore gets its own service and its own FakeListener.

    Returns:
        A freshly initialised WifiService.
    """
    service = object.__new__(wifi_service.WifiService)
    wifi_service.WifiService.__init__(service)
    return service


class WifiServiceTestCase(unittest.TestCase):
    """Common setup: a fresh service, empty buses, and timings short enough to test with."""

    def setUp(self) -> None:
        COMMANDS.clear()
        INCIDENTS.clear()

        self.nm_up = True                       # Read by the patched nm_available
        self.connection = None                  # Read by the patched get_wifi_connection
        self.networks = []                      # Read by the patched get_wifi_networks

        self._patch(wifi_service, "nm_available", lambda: self.nm_up)
        self._patch(wifi_service, "get_wifi_connection", lambda: self.connection)
        self._patch(wifi_service, "get_wifi_networks", lambda: self.networks)

        # Real values are seconds to minutes; the logic under test does not care how long they are.
        self._patch(wifi_service, "NM_POLL_INTERVAL", 0.01)
        self._patch(wifi_service, "AP_STATE_POLL_INTERVAL", 0.01)
        self._patch(wifi_service, "AP_SCAN_SWEEPS", 3)

        self.service = new_service()
        self.listener = self.service.nm_listener
        self.addCleanup(self._quiesce)

    def _patch(self, target, name, value) -> None:
        """Patch an attribute for the duration of the test."""
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _quiesce(self) -> None:
        """Let any daemon thread this test started notice it is done before the patches come off."""
        self.service._stopping.set()            # pylint: disable=protected-access
        self.listener.set_alive(False)
        sleep(0.05)

    # --- assertions ---

    def assert_no_incidents(self) -> None:
        """Fail if anything was published on the incident bus."""
        self.assertEqual(INCIDENTS.values(), [], "unexpected incident published")

    def wait_for_burst(self) -> None:
        """Block until the scan burst has reported the list ready."""
        self.assertTrue(
            self.listener.list_ready.wait(WAIT_TIMEOUT), "scan burst did not finish"
        )


##### Tests ###############################################

class TestStart(WifiServiceTestCase):
    """start(): who gets to start the listener, and how often."""

    def test_start_runs_listener_burst_and_publishes_state(self) -> None:
        """A first start with NetworkManager up starts the listener, sweeps, and publishes the state."""
        self.service.start()

        self.assertEqual(len(self.listener.safe_start_timeouts), 1)
        self.assertTrue(self.listener.list_building.is_set())
        self.wait_for_burst()
        self.assertEqual(self.listener.scan_calls, wifi_service.AP_SCAN_SWEEPS)
        self.assertEqual(COMMANDS.values(), [wifi_service.WIFI_DISCONNECTED])
        self.assert_no_incidents()

    def test_start_passes_the_listener_start_timeout(self) -> None:
        """safe_start gets LISTENER_START_TIMEOUT, not ThreadTemplate's shorter default."""
        self.service.start()

        self.assertEqual(
            self.listener.safe_start_timeouts, [wifi_service.LISTENER_START_TIMEOUT]
        )

    def test_second_start_while_running_is_a_no_op(self) -> None:
        """Starting again when the listener is alive starts nothing and sweeps nothing extra."""
        self.service.start()
        self.wait_for_burst()
        sweeps_after_first = self.listener.scan_calls

        self.service.start()

        self.assertEqual(len(self.listener.safe_start_timeouts), 1)
        self.assertEqual(self.listener.scan_calls, sweeps_after_first)
        self.assert_no_incidents()

    def test_concurrent_starts_start_the_listener_once(self) -> None:
        """Eight callers arriving together produce one listener and one burst."""
        self.listener.safe_start_delay = 0.05   # Widen the window between the claim and the thread going alive

        threads = [Thread(target=self.service.start) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT_TIMEOUT)

        self.assertEqual(len(self.listener.safe_start_timeouts), 1)
        self.wait_for_burst()
        self.assertEqual(self.listener.scan_calls, wifi_service.AP_SCAN_SWEEPS)
        self.assert_no_incidents()

    def test_start_without_networkmanager_and_no_wait_starts_nothing(self) -> None:
        """wait=0 with NM absent gives up at once, and leaves start() usable afterwards."""
        self.nm_up = False

        self.service.start(wait=0)

        self.assertEqual(self.listener.safe_start_timeouts, [])
        self.assertFalse(self.service._starting)        # pylint: disable=protected-access
        self.assert_no_incidents()

        # The claim was released, so a later start still works
        self.nm_up = True
        self.service.start()
        self.assertEqual(len(self.listener.safe_start_timeouts), 1)


class TestDeferredStart(WifiServiceTestCase):
    """start() when NetworkManager has not claimed its bus name yet."""

    def test_listener_starts_once_networkmanager_appears(self) -> None:
        """The deferred thread waits for NM, then starts the listener and releases the claim."""
        self.nm_up = False
        self.service.start(wait=WAIT_TIMEOUT)

        self.assertEqual(self.listener.safe_start_timeouts, [])
        self.assertTrue(self.service._starting)         # pylint: disable=protected-access

        self.nm_up = True

        self.assertTrue(wait_until(lambda: self.listener.is_alive()))
        self.wait_for_burst()
        self.assertTrue(wait_until(lambda: not self.service._starting))  # pylint: disable=protected-access
        self.assert_no_incidents()

    def test_second_start_while_deferred_does_not_add_a_waiter(self) -> None:
        """Callers arriving during the wait are turned away, so NM's arrival starts one listener."""
        self.nm_up = False
        for _ in range(5):
            self.service.start(wait=WAIT_TIMEOUT)

        self.nm_up = True

        self.assertTrue(wait_until(lambda: self.listener.is_alive()))
        self.wait_for_burst()
        sleep(0.1)      # Long enough for a second waiter to have acted, had there been one
        self.assertEqual(len(self.listener.safe_start_timeouts), 1)
        self.assert_no_incidents()

    def test_stop_cancels_the_deferred_start(self) -> None:
        """A stop during the wait means the listener never comes up, even once NM appears."""
        self.nm_up = False
        self.service.start(wait=WAIT_TIMEOUT)

        self.service.stop()
        self.nm_up = True

        sleep(0.1)
        self.assertEqual(self.listener.safe_start_timeouts, [])
        self.assertTrue(wait_until(lambda: not self.service._starting))  # pylint: disable=protected-access
        self.assert_no_incidents()

    def test_giving_up_on_networkmanager_publishes_an_incident(self) -> None:
        """NM never appearing is worth reporting, and must not leave the claim held."""
        self.nm_up = False

        self.service.start(wait=0.05)

        self.assertTrue(wait_until(lambda: INCIDENTS.values() == [wifi_service.WIFI_DBUS_FAILED]))
        self.assertTrue(wait_until(lambda: not self.service._starting))  # pylint: disable=protected-access
        self.assertEqual(self.listener.safe_start_timeouts, [])


class TestStartListener(WifiServiceTestCase):
    """_start_listener(): telling a failed start from a start that has already happened."""

    def test_failure_to_start_a_thread_is_an_incident(self) -> None:
        """safe_start False with no thread alive is the real failure: report it and build nothing."""
        self.listener.safe_start_result = False
        self.listener.set_alive(False)

        self.service._start_listener()                  # pylint: disable=protected-access

        self.assertEqual(INCIDENTS.values(), [wifi_service.WIFI_DBUS_FAILED])
        self.assertFalse(self.listener.list_building.is_set())
        self.assertEqual(COMMANDS.values(), [])

    def test_already_running_is_not_an_incident(self) -> None:
        """safe_start False on a live thread means someone else won the race; carry on quietly."""
        self.listener.safe_start_result = False
        self.listener.set_alive(True)

        self.service._start_listener()                  # pylint: disable=protected-access

        self.assert_no_incidents()
        self.assertTrue(self.listener.list_building.is_set())
        self.wait_for_burst()

    def test_crash_during_startup_is_an_incident(self) -> None:
        """A listener that crashed in setup() is reported, and no burst is started behind it."""
        self.listener.crashed = True
        self.listener.exception = RuntimeError("no such device")

        self.service._start_listener()                  # pylint: disable=protected-access

        self.assertEqual(INCIDENTS.values(), [wifi_service.WIFI_DBUS_FAILED])
        self.assertFalse(self.listener.list_building.is_set())

    def test_stop_during_startup_takes_the_listener_back_down(self) -> None:
        """A stop that lands while safe_start is blocked is not overtaken by the listener it cancelled."""
        self.service._stopping.set()                    # pylint: disable=protected-access

        self.service._start_listener()                  # pylint: disable=protected-access

        self.assertEqual(self.listener.safe_stop_calls, 1)
        self.assertFalse(self.listener.list_building.is_set())
        self.assertEqual(COMMANDS.values(), [])
        self.assert_no_incidents()


class TestNetworkListBurst(WifiServiceTestCase):
    """_build_network_list(): the startup scan burst and the flags around it."""

    def test_burst_sweeps_and_reports_ready(self) -> None:
        """A full burst runs AP_SCAN_SWEEPS sweeps and then reports the list ready."""
        self.listener.set_alive(True)
        self.listener.list_building.set()

        self.service._build_network_list()              # pylint: disable=protected-access

        self.assertEqual(self.listener.scan_calls, wifi_service.AP_SCAN_SWEEPS)
        self.assertTrue(self.listener.list_ready.is_set())

    def test_burst_stops_sweeping_when_the_listener_goes_down(self) -> None:
        """Without a listener there is nothing to collect results, so the burst gives up and reports anyway."""
        self.listener.set_alive(False)
        self.listener.list_building.set()

        self.service._build_network_list()              # pylint: disable=protected-access

        self.assertEqual(self.listener.scan_calls, 0)
        self.assertTrue(self.listener.list_ready.is_set())

    def test_superseded_burst_does_not_report_ready(self) -> None:
        """A burst whose flag stop() cleared belongs to a torn-down listener; it reports nothing."""
        self.listener.set_alive(True)
        self.listener.list_building.clear()

        self.service._build_network_list()              # pylint: disable=protected-access

        self.assertFalse(self.listener.list_ready.is_set())

    def test_restart_after_stop_builds_the_list_again(self) -> None:
        """stop() clears both flags, so the next start sweeps again rather than trusting a stale list."""
        self.service.start()
        self.wait_for_burst()
        sweeps_before = self.listener.scan_calls

        self.service.stop()

        self.assertFalse(self.listener.list_building.is_set())
        self.assertFalse(self.listener.list_ready.is_set())

        self.service.start()
        self.wait_for_burst()
        self.assertEqual(self.listener.scan_calls, sweeps_before + wifi_service.AP_SCAN_SWEEPS)


class TestAccessPointTiming(WifiServiceTestCase):
    """_wait_for_network_list() and await_access_point()."""

    def test_wait_returns_at_once_when_the_list_is_ready(self) -> None:
        """The normal case costs nothing: the list was built at startup."""
        self.listener.list_ready.set()
        started = monotonic()

        self.service._wait_for_network_list()           # pylint: disable=protected-access

        self.assertLess(monotonic() - started, 0.1)

    def test_wait_gives_up_rather_than_blocking_forever(self) -> None:
        """An unfinished burst delays the access point, but only to a bound."""
        self._patch(wifi_service, "AP_LIST_READY_TIMEOUT", 0.05)
        started = monotonic()

        self.service._wait_for_network_list()           # pylint: disable=protected-access

        self.assertGreaterEqual(monotonic() - started, 0.05)

    def test_await_reports_the_access_point_up(self) -> None:
        """The state says access point, so the answer is yes."""
        self.connection = wifi_service.ACCESS_POINT_SSID

        self.assertTrue(self.service.await_access_point(timeout=WAIT_TIMEOUT))

    def test_await_answers_at_once_on_a_reported_failure(self) -> None:
        """A told failure beats waiting out the budget for a state that is not coming."""
        self.service._ap_failed.set()                   # pylint: disable=protected-access
        started = monotonic()

        self.assertFalse(self.service.await_access_point(timeout=WAIT_TIMEOUT))
        self.assertLess(monotonic() - started, 0.5)

    def test_await_times_out_when_nothing_happens(self) -> None:
        """No access point and no reported failure ends as a timeout."""
        self.assertFalse(self.service.await_access_point(timeout=0.05))

    def test_await_sees_an_access_point_that_arrives_late(self) -> None:
        """The poll picks up a state change that happens while it is waiting."""
        def bring_up() -> None:
            sleep(0.05)
            self.connection = wifi_service.ACCESS_POINT_SSID

        Thread(target=bring_up, daemon=True).start()

        self.assertTrue(self.service.await_access_point(timeout=WAIT_TIMEOUT))


class TestState(WifiServiceTestCase):
    """get_state(): what the active connection means."""

    def test_no_connection_is_disconnected(self) -> None:
        """Nothing active means disconnected."""
        self.connection = None
        self.assertEqual(self.service.get_state(), wifi_service.WIFI_DISCONNECTED)

    def test_own_access_point_is_reported_as_such(self) -> None:
        """The Oradio's own access point is not an internet connection."""
        self.connection = wifi_service.ACCESS_POINT_SSID
        self.assertEqual(self.service.get_state(), wifi_service.WIFI_ACCESS_POINT)

    def test_any_other_network_is_connected(self) -> None:
        """Any other active connection counts as connected."""
        self.connection = "Home"
        self.assertEqual(self.service.get_state(), wifi_service.WIFI_CONNECTED)


class TestConnect(WifiServiceTestCase):
    """wifi_connect() and the thread it hands activation to."""

    def setUp(self) -> None:
        super().setUp()
        self.added = []
        self.deleted = []
        self.activated = []
        self.add_result = True
        self.up_result = True

        def networkmanager_add(ssid, pswd=None) -> bool:
            self.added.append((ssid, pswd))
            return self.add_result

        def networkmanager_del(ssid) -> bool:
            self.deleted.append(ssid)
            return True

        def wifi_up(ssid) -> bool:
            self.activated.append(ssid)
            return self.up_result

        self._patch(wifi_service, "networkmanager_add", networkmanager_add)
        self._patch(wifi_service, "networkmanager_del", networkmanager_del)
        self._patch(wifi_service, "_wifi_up", wifi_up)
        wifi_service._set_saved_network(None)           # pylint: disable=protected-access

    def test_connect_activates_the_profile(self) -> None:
        """The profile is added and then activated on a background thread."""
        self.service.wifi_connect("Home", "secret")

        self.assertTrue(wait_until(lambda: self.activated == ["Home"]))
        self.assertEqual(self.added, [("Home", "secret")])
        self.assertEqual(self.deleted, [])

    def test_previous_connection_is_remembered(self) -> None:
        """Displacing a live network saves it, so it can be restored later."""
        self.connection = "Home"

        self.service.wifi_connect("Cafe", "")

        self.assertEqual(wifi_service.get_saved_network(), "Home")

    def test_access_point_is_not_remembered(self) -> None:
        """The Oradio's own access point is not something to restore."""
        self.connection = wifi_service.ACCESS_POINT_SSID

        self.service.wifi_connect("Home", "secret")

        self.assertEqual(wifi_service.get_saved_network(), "")

    def test_failed_activation_removes_the_broken_profile(self) -> None:
        """A profile that will not come up is not left behind."""
        self.up_result = False

        self.service.wifi_connect("Home", "wrong")

        self.assertTrue(wait_until(lambda: self.deleted == ["Home"]))

    def test_access_point_failure_is_told_not_inferred(self) -> None:
        """A failed access point makes await_access_point answer at once instead of waiting out the budget."""
        self.up_result = False
        self.listener.list_ready.set()      # So the connect thread does not wait for the burst

        self.service.wifi_connect(wifi_service.ACCESS_POINT_SSID, None)

        self.assertTrue(wait_until(lambda: self.service._ap_failed.is_set()))  # pylint: disable=protected-access
        self.assertFalse(self.service.await_access_point(timeout=WAIT_TIMEOUT))

    def test_profile_that_cannot_be_added_fails_the_access_point(self) -> None:
        """Nothing will be activated, so nobody should be left waiting for it."""
        self.add_result = False

        self.service.wifi_connect(wifi_service.ACCESS_POINT_SSID, None)

        self.assertTrue(self.service._ap_failed.is_set())   # pylint: disable=protected-access
        self.assertEqual(self.activated, [])

    def test_new_access_point_request_clears_the_previous_verdict(self) -> None:
        """A late waiter must see this request's outcome, not the last one's."""
        self.service._ap_failed.set()                       # pylint: disable=protected-access
        self.listener.list_ready.set()

        self.service.wifi_connect(wifi_service.ACCESS_POINT_SSID, None)

        self.assertTrue(wait_until(lambda: self.activated == [wifi_service.ACCESS_POINT_SSID]))
        self.assertFalse(self.service._ap_failed.is_set())  # pylint: disable=protected-access

    def test_access_point_waits_for_the_network_list(self) -> None:
        """Scanning after the access point takes the radio risks the client, so the list comes first."""
        self._patch(wifi_service, "AP_LIST_READY_TIMEOUT", WAIT_TIMEOUT)

        self.service.wifi_connect(wifi_service.ACCESS_POINT_SSID, None)

        sleep(0.05)
        self.assertEqual(self.activated, [], "activated before the list was ready")

        self.listener.list_ready.set()
        self.assertTrue(wait_until(lambda: self.activated == [wifi_service.ACCESS_POINT_SSID]))


class TestDisconnect(WifiServiceTestCase):
    """wifi_disconnect()."""

    def test_disconnect_brings_the_active_connection_down(self) -> None:
        """The active connection is deactivated."""
        self.connection = "Home"
        brought_down = []
        self._patch(wifi_service, "_wifi_down", lambda ssid: brought_down.append(ssid) or True)

        self.service.wifi_disconnect()

        self.assertEqual(brought_down, ["Home"])
        self.assert_no_incidents()

    def test_failure_to_disconnect_is_an_incident(self) -> None:
        """A disconnect that does not take is worth reporting."""
        self.connection = "Home"
        self._patch(wifi_service, "_wifi_down", lambda ssid: False)

        self.service.wifi_disconnect()

        self.assertEqual(INCIDENTS.values(), [wifi_service.WIFI_DISCONNECT_FAILED])

    def test_disconnect_with_nothing_active_does_nothing(self) -> None:
        """Already disconnected is not a failure."""
        self.connection = None
        self._patch(wifi_service, "_wifi_down", lambda ssid: self.fail("should not disconnect"))

        self.service.wifi_disconnect()

        self.assert_no_incidents()


class TestStop(WifiServiceTestCase):
    """stop()."""

    def test_stop_stops_the_listener_and_clears_the_burst_flags(self) -> None:
        """Everything the next start() needs to see reset, is reset."""
        self.service.start()
        self.wait_for_burst()

        self.service.stop()

        self.assertEqual(self.listener.safe_stop_calls, 1)
        self.assertFalse(self.listener.is_alive())
        self.assertFalse(self.listener.list_building.is_set())
        self.assertFalse(self.listener.list_ready.is_set())
        self.assertTrue(self.service._stopping.is_set())    # pylint: disable=protected-access

    def test_stop_before_start_is_harmless(self) -> None:
        """Stopping something that was never started is not a failure."""
        self.service.stop()

        self.assertEqual(self.listener.safe_stop_calls, 1)
        self.assert_no_incidents()


class TestSingleton(unittest.TestCase):
    """The decorated class, rather than the per-test instances the other cases use."""

    def test_every_construction_returns_the_same_service(self) -> None:
        """oradio_control and WebService must not end up with two of these."""
        self.assertIs(wifi_service.WifiService(), wifi_service.WifiService())

    def test_construction_does_not_re_initialise(self) -> None:
        """A second construction leaves the listener and its state alone."""
        first = wifi_service.WifiService()
        listener = first.nm_listener
        listener.list_ready.set()

        second = wifi_service.WifiService()

        self.assertIs(second.nm_listener, listener)
        self.assertTrue(second.nm_listener.list_ready.is_set())

    def test_construction_starts_nothing(self) -> None:
        """Constructing is not starting: no listener thread, no sweep, no timer."""
        service = wifi_service.WifiService()

        self.assertFalse(service.nm_listener.is_alive())
        self.assertEqual(service.nm_listener.safe_start_timeouts, [])
        self.assertEqual(service.nm_listener.scan_calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
