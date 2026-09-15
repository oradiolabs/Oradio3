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
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       5
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides two classes for managing the Oradio web interface:

    UvicornServerThread — wraps a FastAPI/ASGI application in a background
    daemon thread and exposes start/stop control.  Readiness is determined by
    polling uvicorn.Server.started, which Uvicorn sets once the server is
    accepting connections.  A fresh Server instance is created on each call to
    start() so that internal Uvicorn state (started, should_exit) is always
    clean.

    WebService — orchestrates the full Captive Portal lifecycle: brings up a
    WiFi access point, configures iptables HTTP port-forwarding and dnsmasq DNS
    redirection, starts the Uvicorn server, and tears everything down cleanly
    on stop.  An internal queue thread relays requests from the web API (e.g.
    WiFi connect, portal stop) to the service without blocking the ASGI event
    loop.

    References:
        https://www.uvicorn.org/
        https://fastapi.tiangolo.com/
        https://captivebehavior.wballiance.com/
        https://superfastpython.com/multiprocessing-in-python/
"""
import time
from typing import Any
from pathlib import Path
from multiprocessing import Queue
from threading import Thread, RLock

##### Oradio modules ######################################
from log_service import oradio_log, ORADIO_LOG_LEVEL
from utilities import run_shell_script
from wifi_service import WifiService, get_wifi_connection
from messaging import (
    safe_get,
    Commands,
    Incidents,
    CommandMessage,
    IncidentMessage,
    WIFI_DISCONNECTED,
    WIFI_CONNECTED,
    WIFI_ACCESS_POINT,
    WEB_SOURCE,
    WEB_IDLE,
    WEB_ACTIVE,
    WEB_SERVER_FAILED,
    WEB_START_FAILED,
    WEB_STOP_FAILED,
)

##### GLOBAL constants ####################################
from constants import (
    DNS_REDIRECT_CONF,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    REQUEST_CONNECT,
    REQUEST_STOP,
)

##### LOCAL constants #####################################
# Seconds to wait for the Uvicorn server to become ready.
SERVER_READY_TIMEOUT = 15

# Seconds to wait for the WiFi interface to settle after the access point is torn
# down on stop(): association, WPA handshake and DHCP against the user's router.
# A false timeout here publishes WEB_STOP_FAILED for a reconnect that actually
# succeeded, so the value is generous; waiting longer costs nothing on success,
# since _wait_for_wifi_state() returns as soon as the state arrives.
#
# Covers the reconnect on stop() and nothing else. The access point coming up on
# start() is wifi_service.await_access_point()'s wait, so this value can be
# tuned on its own without checking anything in wifi_service.
RECONNECT_TIMEOUT = 45

SOCKET_TIMEOUT = 3   # WebSocket ping interval/timeout in seconds; safe for small devices and networks

# iptables NAT rule that redirects inbound HTTP (port 80) to the portal port.
# The string uses the iptables-save -A (append) format, which is what
# _get_nat_rules() returns, so start() and stop() can check presence with a
# simple substring test.  The actual deletion command uses -D instead of -A.
_IPTABLES_REDIRECT_RULE = (
    f"-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
)

# dnsmasq config file that resolves all hostnames to the captive portal address.
_DNS_REDIRECT_CONF = Path(DNS_REDIRECT_CONF)

##### Deferred web stack ##################################
# uvicorn and the FastAPI application are imported on first use instead of at
# module import, and they are the reason this indirection exists at all.
#
# Measured cold on the device, they cost 6.0 of the 11 seconds the Oradio spends
# importing before a single line of its own start-up code runs: fastapi 2.6s,
# building the app in web_server 1.5s, pydantic 0.6s, uvicorn 0.4s. Everything
# else the Oradio does at start-up -- every subsystem, every thread -- adds up
# to 0.7s. So this is more than half the time between power-on and the tune,
# spent on a captive portal that opens after a long press: minutes later, or
# never.
#
# Module globals rather than parameters, so the code below reads the same as it
# did when they were ordinary imports. They are None until _load_web_stack()
# fills them in.
# Lower-case on purpose: these stand in for imported names, not for constants.
# Typed Any because None has neither a Config nor a state attribute, and without
# it every use below is a type error.
uvicorn: Any = None      # pylint: disable=invalid-name
api_app: Any = None      # pylint: disable=invalid-name

def _load_web_stack() -> bool:
    """
    Import uvicorn and the FastAPI app, once, on first use.

    Returns:
        True when both are available. False when the import failed, which is
        reported as an incident: moving the import off the start-up path also
        moves the moment a missing package is noticed, from "the Oradio will
        not start" to "the portal does not open", and the second is only
        useful if it says so out loud.
    """
    global uvicorn, api_app   # pylint: disable=global-statement

    if uvicorn is not None and api_app is not None:
        return True

    try:
        # pylint: disable=import-outside-toplevel
        import uvicorn as uvicorn_module
        from web_server import api_app as api_app_object
    except ImportError as ex_err:
        oradio_log.error("Failed to import the web stack: %s", ex_err)
        Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_SERVER_FAILED))
        return False

    uvicorn = uvicorn_module
    api_app = api_app_object
    return True

class UvicornServerThread:
    """
    Manage a Uvicorn ASGI server running in a background daemon thread.

    Uses uvicorn.Server.run() as the thread target directly, and polls
    server.started (set by Uvicorn once the server is accepting connections)
    to determine readiness.

    A fresh Server instance is created on each call to start() so that
    internal Uvicorn state (started, should_exit) is always clean.  The
    Config object is built once in __init__ and reused across restarts because
    it is immutable after construction.
    """
    def __init__(self, app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, level=ORADIO_LOG_LEVEL):
        """
        Initialise the manager without starting the server.

        Args:
            app:         ASGI application instance to serve.
            host (str):  Network interface to bind.
            port (int):  TCP port to listen on.
            level (str): Uvicorn log level string. Accepted values (passed
                         through to Uvicorn): "trace", "debug", "info",
                         "warning", "error", "critical".
        """
        self._config = uvicorn.Config(
            app,
            host=host,
            port=port,
            lifespan="off",   # Disable ASGI lifespan events (startup/shutdown hooks)
            log_config=None,  # Uvicorn logging is handled by oradio_log
            log_level=level,
            ws_ping_timeout=SOCKET_TIMEOUT,
            ws_ping_interval=SOCKET_TIMEOUT,
        )
        self._server = None
        self._thread = None
        # RLock, not Lock: is_running acquires it too, and is_running is called from
        # inside start()/stop(), which already hold the lock. A plain Lock would deadlock.
        self._lock = RLock()

    def start(self) -> bool:
        """
        Start the server if not already running.

        Creates a fresh Server instance, launches it on a daemon thread, then
        polls server.started until Uvicorn signals it is accepting connections
        or SERVER_READY_TIMEOUT seconds elapse.

        Returns:
            bool: True if the server is ready, False on thread start failure
                or timeout.
        """
        with self._lock:
            if self.is_running:
                oradio_log.debug("Uvicorn server already running")
                return True

            oradio_log.info("Starting Uvicorn server...")
            self._server = uvicorn.Server(self._config)

            self._thread = Thread(target=self._server.run, daemon=True)
            try:
                self._thread.start()
                oradio_log.info("Uvicorn server started")
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Uvicorn server failed to start: %s", ex_err)
                return False

            # Poll server.started — set by Uvicorn once the server is accepting connections.
            deadline = time.time() + SERVER_READY_TIMEOUT
            while not self._server.started:
                if time.time() > deadline:
                    oradio_log.warning("Uvicorn server did not become ready in time")
                    return False
                time.sleep(0.1)

            oradio_log.info("Uvicorn server running")
            return True

    def stop(self) -> bool:
        """
        Stop the running server and wait for the thread to exit.

        Returns:
            bool: True if stopped cleanly, False if the thread did not exit
                within SERVER_READY_TIMEOUT seconds.
        """
        with self._lock:
            if not self.is_running:
                oradio_log.debug("Uvicorn server already stopped")
                return True

            oradio_log.debug("Stopping Uvicorn server...")
            self._server.should_exit = True
            self._server.force_exit  = True
            self._thread.join(timeout=SERVER_READY_TIMEOUT)

            if self._thread.is_alive():
                oradio_log.warning("Uvicorn server thread did not exit cleanly")
                return False

            oradio_log.info("Uvicorn server stopped")
            return True

    @property
    def is_running(self) -> bool:
        """
        Whether the server is actively accepting connections.

        True when the server thread is alive, the server has started, and
        shutdown has not been requested.

        Reads self._thread / self._server under self._lock so a caller from
        an unrelated thread (e.g. WebService.state) always sees a
        consistent snapshot rather than attributes mid-transition from a
        concurrent start()/stop().

        Returns:
            bool: True if the server is actively accepting connections,
                False in all other states (not started, stopping, stopped).
        """
        with self._lock:
            return (
                self._thread is not None and
                self._thread.is_alive() and
                self._server is not None and
                self._server.started and
                not self._server.should_exit
            )

class WebService:
    """
    Manage the Captive Portal web interface over WiFi or a hosted access point.

    Coordinates the full lifecycle of the portal:

    1. Bring up a WiFi access point (via WifiService).
    2. Redirect port-80 HTTP traffic to the portal port (iptables PREROUTING).
    3. Redirect all DNS queries to the portal host (dnsmasq config file).
    4. Start the Uvicorn web server.
    5. Reverse all of the above on stop.

    Private helper methods handle each system operation (iptables, DNS, WiFi
    polling) in isolation so that start() and stop() read as straightforward
    sequences of steps rather than inline shell-script management code.
    Each helper publishes its own error message on failure; callers do not
    re-publish.

    An internal daemon thread (_check_server_messages) drains a Queue that the
    FastAPI routes write to, and translates queue messages into service actions
    (WiFi connect, portal stop) without blocking the ASGI event loop.
    """
    def __init__(self):
        """
        Initialise the WebService and start the background message listener.

        Sets up the shared queue, wires it into the FastAPI application state,
        creates the Uvicorn wrapper, and starts the message-listener thread,
        which runs for the full lifetime of the process and has no stop mechanism.
        Logs an error and publishes WEB_SERVER_FAILED if either the Uvicorn
        wrapper or the listener thread fails to initialise. Publishes WEB_IDLE
        to the message bus so the controller starts from a known baseline.

        uvicorn_server is pre-assigned to None before initialisation so that
        the state property and start/stop methods can safely check for
        initialisation failure with a simple None guard.
        """
        # Shared queue: FastAPI route handlers post plain dicts here;
        # _check_server_messages() reads and dispatches them.
        self.request_queue = Queue()

        self.wifi_service = WifiService()

        # Guards _create_server(). The warm-up thread oradio_control starts
        # after the tune and a long press arriving before it finished both call
        # it, and without this they would each build a server object -- two
        # UvicornServerThreads over one port. The second caller waits and then
        # finds the work done.
        self._create_lock = RLock()

        # Left None until start() needs it. Building it here would import the
        # web stack, and this constructor runs at Oradio start-up while the
        # portal it serves may never be opened -- see _load_web_stack() above.
        #
        # state(), start() and stop() already guard on None, so the only thing
        # that changes for them is that None now also means "not started yet"
        # rather than only "failed to initialise".
        self.uvicorn_server = None

        # Daemon thread: drains request_queue and dispatches to service methods.
        # Exits automatically when the main process exits.
        self.server_listener = Thread(target=self._check_server_messages, daemon=True)

        try:
            self.server_listener.start()
            oradio_log.info("Web server started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Web server failed to start: %s", ex_err)
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_SERVER_FAILED))

        # Announce initial state so the controller starts from a known baseline.
        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

##### Helpers #############################################

    def _get_nat_rules(self) -> str | None:
        """
        Return the current iptables NAT table as a string.

        Returns:
            str | None: Rule dump on success, None if the command fails.
        """
        result, output = run_shell_script('sudo bash -c "iptables-save -t nat"')
        if not result:
            oradio_log.error("Failed to read iptables NAT rules: %s", output)
            return None
        return output

    def _ensure_port_redirect(self) -> bool:
        """
        Add the iptables PREROUTING redirect rule if not already present.

        Returns:
            bool: True if the rule is (or was already) in place, False on error.
        """
        rules = self._get_nat_rules()
        if rules is None:
            oradio_log.error("Failed to get NAT rules")
            return False
        if _IPTABLES_REDIRECT_RULE in rules:
            return True  # Already present; nothing to do.
        cmd = (
            f"sudo iptables -t nat -A PREROUTING "
            f"-p tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
        )
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Failed to add port redirect rule: %s", error)
            return False
        return True

    def _remove_port_redirect(self) -> bool:
        """
        Delete the iptables PREROUTING redirect rule if present.

        Returns:
            bool: True if the rule is (or was already) absent, False on error.
        """
        rules = self._get_nat_rules()
        if rules is None:
            oradio_log.error("Failed to get NAT rules")
            return False
        if _IPTABLES_REDIRECT_RULE not in rules:
            return True  # Already absent; nothing to do.
        cmd = (
            f"sudo iptables -t nat -D PREROUTING "
            f"-p tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
        )
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Failed to remove port redirect rule: %s", error)
            return False
        return True

    def _ensure_dns_redirect(self) -> bool:
        """
        Write the dnsmasq wildcard redirect config if it does not already exist.

        Returns:
            bool: True if the file is (or was already) in place, False on error.
        """
        if _DNS_REDIRECT_CONF.exists():
            return True  # Already present; nothing to do.
        cmd = f'sudo bash -c \'echo "address=/#/{ACCESS_POINT_HOST}" > {_DNS_REDIRECT_CONF}\''
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Failed to write DNS redirect config: %s", error)
            return False
        return True

    def _remove_dns_redirect(self) -> bool:
        """
        Remove the dnsmasq redirect config file if it exists.

        Returns:
            bool: True if the file is (or was already) absent, False on error.
        """
        if not _DNS_REDIRECT_CONF.exists():
            return True  # Already absent; nothing to do.
        result, error = run_shell_script(f"sudo rm -f {_DNS_REDIRECT_CONF}")
        if not result:
            oradio_log.error("Failed to remove DNS redirect config: %s", error)
            return False
        return True

    def _wait_for_wifi_state(self, target_states) -> bool:
        """
        Poll until the WiFi interface reaches one of the expected states.

        Used only by stop(), for the reconnect that follows tearing down the
        access point. The access point coming up on start() is not waited for
        here: wifi_service.await_access_point() owns that, because only
        wifi_service knows whether the delay is a failure or a deliberate wait
        for the network list.

        Note:
            Polling is used instead of subscribing to WiFi messages because
            the caller must block until the transition completes before
            proceeding. A 1-second sleep between polls reduces CPU usage.

        Args:
            target_states (set): Acceptable WifiService state values to wait for.

        Returns:
            bool: True if a target state was reached, False on timeout.
        """
        deadline = time.time() + RECONNECT_TIMEOUT
        while self.wifi_service.get_state() not in target_states:
            if time.time() > deadline:
                oradio_log.error("Timeout waiting for WiFi state in %s", target_states)
                return False
            time.sleep(1)
        return True

    def _check_server_messages(self) -> None:
        """
        Drain the incoming queue and act on API requests indefinitely.

        Runs on a daemon thread started in __init__. Blocks on safe_get()
        until a message arrives, consuming no CPU while idle, with fatal-exit
        handling for broken queues. There is no stopping condition: the thread
        is intentionally kept alive for the full lifetime of the process so
        that API requests are never dropped. Unrecognised request types are
        logged as warnings.

        Note:
            request_queue carries plain dicts posted by FastAPI route handlers,
            not CommandMessage objects. message.get("request") is used rather
            than message.message for this reason.

        Recognised request types:
        - REQUEST_CONNECT: extract SSID and optional password, call
          WifiService.wifi_connect(), then stop the Captive Portal.
        - REQUEST_STOP: stop the Captive Portal directly.

        stop() is dispatched on its own daemon thread rather than called
        inline: UvicornServerThread.stop() blocks on a join() of up to
        SERVER_READY_TIMEOUT seconds, and _wait_for_wifi_state() can block
        for up to RECONNECT_TIMEOUT seconds on top of that. Calling it
        inline here would leave this listener -- the only thing draining
        request_queue -- unresponsive to new messages for that entire
        window. stop() is idempotent (guarded by is_running checks), so
        concurrent or overlapping calls from multiple such threads are safe.

        Dispatch is wrapped in a broad try/except: this thread has no
        supervisor and is never restarted, so an uncaught exception here
        would silently end all future message processing for the life of the
        process (the portal could be left stuck running or stuck stopped,
        with no incident reported). Catching, logging, publishing
        WEB_SERVER_FAILED, and continuing keeps the listener alive to
        handle the next message instead.
        """
        while True:
            message = safe_get(self.request_queue)
            oradio_log.debug("Message received: '%s'", message)

            try:
                # Guard against wrong message type
                if not isinstance(message, dict):
                    oradio_log.warning("Unexpected message type %s: %s", type(message).__name__, message)
                    continue

                request = message.get("request")

                if request == REQUEST_CONNECT:
                    if ssid := message.get("ssid"):
                        # Password is optional; None is passed for open networks.
                        pswd = message.get("pswd")
                        self.wifi_service.wifi_connect(ssid, pswd)
                        # Tear down the Captive Portal after handing off to the
                        # new network. Runs on its own thread so a slow stop()
                        # doesn't block this loop from draining new messages.
                        Thread(target=self.stop, daemon=True).start()

                elif request == REQUEST_STOP:
                    Thread(target=self.stop, daemon=True).start()

                else:
                    oradio_log.warning("Unrecognised request: %s", request)

            # Broad catch is intentional: this loop must never die, since
            # nothing else drains request_queue or restarts this thread.
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Error handling server message '%s': %s", message, ex_err)
                Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_SERVER_FAILED))

##### Public API ##########################################

    @property
    def state(self) -> str:
        """
        Current operational state of the web service.

        Returns:
            str: WEB_ACTIVE if the Uvicorn server is running, WEB_IDLE otherwise.
        """
        if self.uvicorn_server is None:
            return WEB_IDLE
        return WEB_ACTIVE if self.uvicorn_server.is_running else WEB_IDLE

    def _create_server(self) -> bool:
        """
        Import the web stack and build the Uvicorn server, on first start.

        Split out of __init__ so the import cost is paid when the user opens
        the portal rather than at every Oradio start-up. Runs once: start()
        only calls it while uvicorn_server is None, and a failure leaves it
        None so the next start() tries again -- an ImportError is usually a
        broken install, but a caller pressing the button twice deserves the
        second attempt rather than a permanent refusal.

        Returns:
            True when the server is ready to be started. False when the web
            stack could not be imported or the server could not be built;
            both are logged here, and start() publishes the incident.
        """
        with self._create_lock:
            # Re-checked inside the lock: a caller that queued behind the
            # warm-up gets its result instead of repeating it.
            if self.uvicorn_server is not None:
                return True

            if not _load_web_stack():
                return False

            # Give the FastAPI app a reference to the queue so route handlers
            # can enqueue requests without importing this module.
            api_app.state.queue = self.request_queue

            try:
                self.uvicorn_server = UvicornServerThread(api_app)
            except Exception as ex_err:     # pylint: disable=broad-exception-caught
                oradio_log.error("Failed to initialize UvicornServerThread: %s", ex_err)
                return False

            return True

    def preload(self) -> bool:
        """
        Do everything start() can do ahead of time, so a long press does not.

        Importing uvicorn and the FastAPI app costs about six seconds cold --
        more than half of what the whole Oradio start-up costs -- and start()
        runs on the command handler thread while holding the state machine
        lock. Paying it there means the Oradio ignores every button, knob and
        event for those seconds, right after the user asked it for something.

        So it is paid here instead, on a thread nobody is waiting on, once the
        Oradio is up. Safe to call more than once: _create_server() is a no-op
        after the first success.

        Also moves the moment a broken install is noticed back to start-up: a
        missing package now publishes its incident shortly after boot rather
        than the first time someone reaches for the portal.

        Returns:
            True when the web stack is loaded and the server object is built.
        """
        if self.uvicorn_server is not None:
            return True

        return self._create_server()

    def start(self) -> bool:
        """
        Start the Captive Portal service.

        Performs the following steps in order:

        1. Switch WiFi into access point mode (transition is asynchronous;
           confirmation is deferred to step 5 so the server can start in
           parallel).
        2. Ensure the iptables port-redirect rule is in place.
        3. Ensure the dnsmasq DNS redirect config is in place.
        4. Start the Uvicorn web server.
        5. Confirm the WiFi transition started in step 1 reached the access
           point, via wifi_service.await_access_point(), which owns the
           timing for that path.

        Two hard preconditions (the web stack failing to load, already
        running) return immediately since there is nothing meaningful to
        accumulate status over in either case. Once past those, each remaining step is
        skipped (via the status guard) if an earlier one already failed, so
        later steps never run against a known-bad state -- but there is still
        only one return statement for the whole step sequence, matching stop().

        All helper methods only log and return False on failure; start() is
        solely responsible for publishing to Incidents, so each failure is
        reported exactly once. Commands.publish(self.state) is only called on
        full success -- a Commands-only subscriber therefore never sees a
        "success" state announced after a failed start().

        Returns:
            bool: True if the portal started successfully, False otherwise.
        """
        # Check running state before committing to any side-effecting steps.
        # uvicorn_server is None until the first start, and None is not running.
        if self.uvicorn_server is not None and self.uvicorn_server.is_running:
            oradio_log.debug("Web service already running")
            return True

        # wifi_connect is non-blocking; the AP transition is confirmed in step 5.
        self.wifi_service.wifi_connect(ACCESS_POINT_SSID, None)

        status = True

        # Import the web stack and build the server, AFTER the access point has
        # been asked for. Both take time and neither waits on the other, so
        # doing this second hides part of the import behind the radio switching
        # mode -- which step 5 waits for anyway.
        #
        # On a warmed-up Oradio this is instant: the import already happened on
        # the background thread oradio_control starts after the tune. This
        # ordering is what keeps a long press that arrives before that finished
        # from paying the full cost in series.
        if self.uvicorn_server is None and not self._create_server():
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_START_FAILED))
            status = False

        if status and (not self._ensure_port_redirect() or not self._ensure_dns_redirect()):
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_START_FAILED))
            status = False

        # Reset the inactivity timer (auto-stops the portal after no client
        # activity) before the server accepts connections, and cancel any
        # lingering timer task from a previous session.
        #
        # api_app.state.timer_task is an asyncio.Task that belongs to the
        # uvicorn event loop, which runs on a different OS thread than this
        # method. Task.cancel() is not documented as thread-safe when called
        # from outside the loop's own thread. This is only safe here because
        # is_running (checked above) guarantees the previous UvicornServerThread
        # has already been stop()'d and joined -- so if timer_task is not None,
        # it belongs to an event loop that is no longer running. The guard below
        # makes that invariant explicit and refuses to proceed if it's ever
        # violated, rather than silently racing on live asyncio internals.
        if status and self.uvicorn_server.is_running:
            oradio_log.error(
                "Refusing to reset keep-alive timer state: uvicorn server unexpectedly still running"
            )
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_START_FAILED))
            status = False

        if status:
            # timer_task is set by the FastAPI app and may not exist on first run,
            # so getattr is used rather than a direct attribute access.
            if getattr(api_app.state, "timer_task", None) is not None:
                api_app.state.timer_task.cancel()
                api_app.state.timer_task = None
            api_app.state.timer_started = False

            if not self.uvicorn_server.start():
                oradio_log.error("Uvicorn server failed to start")
                Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_START_FAILED))
                status = False

        # Step 5: confirm the access point requested in step 1 actually came up.
        # wifi_service owns the timing here -- it is the only module that knows
        # whether a slow start is a failure or a deliberate wait for the network
        # list -- so this asks for a verdict rather than polling against a
        # locally chosen deadline. Left last so the redirects and the web server
        # come up in parallel with the radio, as before.
        if status and not self.wifi_service.await_access_point():
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_START_FAILED))
            status = False

        # Commands only on full success -- Incidents already reported any
        # individual failure above, so a Commands-only subscriber never sees
        # a "success" state announced after a failed start().
        if status:
            Commands.publish(CommandMessage(WEB_SOURCE, self.state))

        return status

    def stop(self) -> bool:
        """
        Stop the Captive Portal service.

        Reverses the steps performed by start() in order:

        1. Disconnect the access point (if currently active) so connected
           clients are dropped before the server stops.
        2. Stop the Uvicorn web server.
        3. Remove the iptables port-redirect rule.
        4. Remove the dnsmasq DNS redirect config file.
        5. Wait for the WiFi interface to reach WIFI_DISCONNECTED or WIFI_CONNECTED.

        Step 2 publishes WEB_STOP_FAILED directly in stop() if the Uvicorn server
        fails to stop. Steps 3 and 4 publish WEB_STOP_FAILED internally in their
        helper methods. All failures continue so that remaining teardown steps
        are still attempted.

        Returns:
            bool: True if every teardown step succeeded, False if any of them
            failed. Since failures do not abort the sequence, a False here means
            teardown was attempted in full but at least one step did not
            complete, and one WEB_STOP_FAILED was published per failed step.
        """
        status = True

        # Disconnect WiFi first so clients are dropped gracefully before the
        # server stops accepting connections.
        if self.wifi_service.get_state() == WIFI_ACCESS_POINT:
            self.wifi_service.wifi_disconnect()

        if self.uvicorn_server is None:
            oradio_log.error("Uvicorn server not initialized")
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_STOP_FAILED))
            status = False
        else:
            if not self.uvicorn_server.stop():
                Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_STOP_FAILED))
                status = False

        if not self._remove_port_redirect():
            status = False
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_STOP_FAILED))

        if not self._remove_dns_redirect():
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_STOP_FAILED))
            status = False

        if not self._wait_for_wifi_state({WIFI_DISCONNECTED, WIFI_CONNECTED}):
            Incidents.publish(IncidentMessage(WEB_SOURCE, WEB_STOP_FAILED))
            status = False

        # Commands only on full success -- Incidents already reported any
        # individual failure above, so a Commands-only subscriber never
        # sees a "success" state announced after a partially failed stop().
        if status:
            Commands.publish(CommandMessage(WEB_SOURCE, self.state))

        return status

##### Stand-alone entry point #############################

if __name__ == '__main__':

    import requests
    import subprocess
    from utilities import input_prompt          # pylint: disable=ungrouped-imports
    from constants import RED, YELLOW, NC       # pylint: disable=ungrouped-imports
    from messaging import DebugMessageHandler   # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern; pylint flags it as duplicate code across modules.
    # pylint: disable=duplicate-code

    def interactive_menu():  # pylint: disable=too-many-branches
        """
        Run an interactive command-line menu for manual WebService testing.

        Creates a WebService instance and presents a numbered menu that lets a
        developer exercise start, stop, WiFi connect, and state inspection
        without running the full Oradio application stack.

        The too-many-branches pylint warning is suppressed because the match
        statement has one branch per menu option, which is unavoidable here.
        """
        web_service = WebService()

        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-show ANY web service state\n"
            " 2-start web service (emulate long-press-AAN)\n"
            " 3-stop web service (emulate any-press-UIT)\n"
            " 4-start and right away stop web service (test robustness)\n"
            " 5-emulate web interface submit network\n"
            " 6-get wifi state and connection\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    # Use ss to check whether a process is listening on the configured host:port.
                    proc = subprocess.run(
                        f"ss -tuln | grep {WEB_SERVER_HOST}:{WEB_SERVER_PORT}",
                        shell=True, check=False, stdout=subprocess.DEVNULL
                    )
                    if proc.returncode == 0:
                        print("\nActive web service found\n")
                    else:
                        print("\nNo active web service found\n")
                case 2:
                    print("\nStarting the web service...\n")
                    web_service.start()
                case 3:
                    print("\nStopping the web service...\n")
                    web_service.stop()
                case 4:
                    print("\nStarting the web service...\n")
                    web_service.start()
                    print("\nStopping the web service...\n")
                    web_service.stop()
                case 5:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        print("\nStarting the web service...\n")
                        web_service.start()
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                        url = f"http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}/wifi_connect"
                        try:
                            requests.post(url, json={"ssid": name, "pswd": pswrd}, timeout=SERVER_READY_TIMEOUT)
                        except requests.exceptions.RequestException:
                            print(f"{RED}Failed to connect. Make sure you have an active web server{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    # WiFi state may lag during transitions; re-run this option once settled if needed.
                    print(f"{YELLOW}Careful: state may not be correct if wifi is still processing: check messages and run again when in doubt{NC}")
                    wifi_state = web_service.wifi_service.get_state()
                    if wifi_state == WIFI_DISCONNECTED:
                        print(f"\nWiFi state: '{wifi_state}'\n")
                    else:
                        print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
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

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
