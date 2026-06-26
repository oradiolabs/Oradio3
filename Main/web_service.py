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
from pathlib import Path
from multiprocessing import Queue
from threading import Thread, Lock
import uvicorn

##### oradio modules ################
from log_service import oradio_log, ORADIO_LOG_LEVEL
from utilities import run_shell_script
from web_server import api_app
from wifi_service import WifiService, get_wifi_connection
from messaging import (
    Errors,
    Commands,
    safe_get,
    ErrorMessage,
    CommandMessage,
    WIFI_DISCONNECTED,
    WIFI_CONNECTED,
    WIFI_ACCESS_POINT,
    WEB_SOURCE,
    WEB_IDLE,
    WEB_ACTIVE,
    WEB_ERROR_START,
    WEB_ERROR_STOP,
    WEB_ERROR_SERVICE,
)

##### GLOBAL constants ##############
from constants import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_REQUEST_CONNECT,
    MESSAGE_REQUEST_STOP,
)

##### LOCAL constants ###############
# Seconds to wait for the Uvicorn server to become ready.
SERVER_READY_TIMEOUT = 15

# Seconds to wait for WiFi state transitions (access point up, disconnect, reconnect).
WIFI_STATE_TIMEOUT = 15

SOCKET_TIMEOUT = 3   # WebSocket ping interval/timeout in seconds; safe for small devices and networks

# iptables NAT rule that redirects inbound HTTP (port 80) to the portal port.
# The string uses the iptables-save -A (append) format, which is what
# _get_nat_rules() returns, so start() and stop() can check presence with a
# simple substring test.  The actual deletion command uses -D instead of -A.
_IPTABLES_REDIRECT_RULE = (
    f"-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
)

# dnsmasq config file that resolves all hostnames to the captive portal address.
_DNS_REDIRECT_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/redirect.conf")

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
        self._lock = Lock()

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
                Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_SERVICE))
                return False

            # Poll server.started — set by Uvicorn once the server is accepting connections.
            deadline = time.time() + SERVER_READY_TIMEOUT
            while not self._server.started:
                if time.time() > deadline:
                    oradio_log.warning("Uvicorn server did not become ready in time")
                    Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_SERVICE))
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
                Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_SERVICE))
                return False

            oradio_log.info("Uvicorn server stopped")
            return True

    @property
    def is_running(self) -> bool:
        """
        True if the server thread is alive, the server has started, and
        shutdown has not been requested.

        Returns:
            bool: True if the server is actively accepting connections,
                False in all other states (not started, stopping, stopped).
        """
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
        Logs an error and publishes WEB_ERROR_SERVICE if either the Uvicorn
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

        # Give the FastAPI app a reference to the queue so route handlers can
        # enqueue requests without importing this module.
        api_app.state.queue = self.request_queue

        # Pre-assign to None so state, start(), and stop() can check for
        # initialisation failure with a simple None guard rather than hasattr.
        self.uvicorn_server = None
        try:
            self.uvicorn_server = UvicornServerThread(api_app)
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            oradio_log.error("Failed to initialize UvicornServerThread: %s", ex_err)
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_SERVICE))

        # Daemon thread: drains request_queue and dispatches to service methods.
        # Exits automatically when the main process exits.
        self.server_listener = Thread(target=self._check_server_messages, daemon=True)

        try:
            self.server_listener.start()
            oradio_log.info("Web server started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("Web server failed to start: %s", ex_err)
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_SERVICE))

        # Announce initial state so the controller starts from a known baseline.
        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

##### Private helpers ###############

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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
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
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
            return False
        return True

    def _wait_for_wifi_state(self, target_states, error_type) -> bool:
        """
        Poll until the WiFi interface reaches one of the expected states.

        Note:
            Polling is used instead of subscribing to WiFi messages because
            the caller must block until the transition completes before
            proceeding. A 1-second sleep between polls reduces CPU usage.

        Args:
            target_states (set): Acceptable WifiService state values to wait for.
            error_type:          Error message constant to publish on timeout
                                 (e.g. WEB_ERROR_START or WEB_ERROR_STOP).

        Returns:
            bool: True if a target state was reached, False on timeout.
        """
        deadline = time.time() + WIFI_STATE_TIMEOUT
        while self.wifi_service.get_state() not in target_states:
            if time.time() > deadline:
                oradio_log.error("Timeout waiting for WiFi state in %s", target_states)
                Errors.publish(ErrorMessage(WEB_SOURCE, error_type))
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
        - MESSAGE_REQUEST_CONNECT: extract SSID and optional password, call
          WifiService.wifi_connect(), then stop the Captive Portal.
        - MESSAGE_REQUEST_STOP: stop the Captive Portal directly.
        """
        while True:
            message = safe_get(self.request_queue)
            oradio_log.debug("WebService: message received: '%s'", message)

            # Guard against wrong message type
            if not isinstance(message, dict):
                oradio_log.warning("Unexpected message type %s: %s", type(message).__name__, message)
                continue

            request = message.get("request")

            if request == MESSAGE_REQUEST_CONNECT:
                if ssid := message.get("ssid"):
                    # Password is optional; None is passed for open networks.
                    pswd = message.get("pswd")
                    self.wifi_service.wifi_connect(ssid, pswd)
                    # Tear down the Captive Portal after handing off to the new network.
                    self.stop()

            elif request == MESSAGE_REQUEST_STOP:
                self.stop()

            else:
                oradio_log.warning("WebService: unrecognised request: %s", request)

##### Public interface ##############

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

    def start(self) -> None:
        """
        Start the Captive Portal service.

        Performs the following steps in order, aborting and publishing a
        WEB_ERROR_START error if any step fails:

        1. Switch WiFi into access point mode (transition is asynchronous;
           confirmation is deferred to step 5 so the server can start in
           parallel).
        2. Ensure the iptables port-redirect rule is in place.
        3. Ensure the dnsmasq DNS redirect config is in place.
        4. Start the Uvicorn web server.
        5. Wait for the WiFi transition (started in step 1) to reach
           WIFI_ACCESS_POINT state.

        Error messages are published by the helper methods; start() does not
        re-publish them. Does nothing if the service is already running.
        On success, publishes WEB_ACTIVE to the message bus.
        """
        if self.uvicorn_server is None:
            oradio_log.error("Uvicorn server not initialized")
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
            return

        # Check running state before committing to any side-effecting steps.
        if self.uvicorn_server.is_running:
            oradio_log.debug("Web service already running")
            return

        # wifi_connect is non-blocking; the AP transition is confirmed in step 5.
        self.wifi_service.wifi_connect(ACCESS_POINT_SSID, None)

        if not self._ensure_port_redirect():
            return
        if not self._ensure_dns_redirect():
            return

        # Reset the inactivity timer (auto-stops the portal after no client
        # activity) before the server accepts connections and cancel any
        # lingering timer task from a previous session.
        # timer_task is set by the FastAPI app and may not exist on first run,
        # so getattr is used rather than a direct attribute access.
        if getattr(api_app.state, "timer_task", None) is not None:
            api_app.state.timer_task.cancel()
            api_app.state.timer_task = None
        api_app.state.timer_started = False

        if not self.uvicorn_server.start():
            oradio_log.error("Uvicorn server failed to start")
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
            return

        if not self._wait_for_wifi_state({WIFI_ACCESS_POINT}, WEB_ERROR_START):
            return

        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

    def stop(self) -> None:
        """
        Stop the Captive Portal service.

        Reverses the steps performed by start() in order:

        1. Disconnect the access point (if currently active) so connected
           clients are dropped before the server stops.
        2. Stop the Uvicorn web server.
        3. Remove the iptables port-redirect rule.
        4. Remove the dnsmasq DNS redirect config file.
        5. Wait for the WiFi interface to reach WIFI_DISCONNECTED or WIFI_CONNECTED.

        Steps 3 and 4 publish WEB_ERROR_STOP internally on failure but always
        continue so that remaining teardown steps are still attempted.
        On completion, publishes WEB_IDLE to the message bus.
        """
        # Disconnect WiFi first so clients are dropped gracefully before the
        # server stops accepting connections.
        if self.wifi_service.get_state() == WIFI_ACCESS_POINT:
            self.wifi_service.wifi_disconnect()

        if self.uvicorn_server is None:
            oradio_log.error("Uvicorn server not initialized")
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
        else:
            if not self.uvicorn_server.stop():
                Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))

        # Helper methods publish WEB_ERROR_STOP internally on failure;
        # stop() does not re-publish so each error is reported exactly once.
        self._remove_port_redirect()
        self._remove_dns_redirect()

        self._wait_for_wifi_state({WIFI_DISCONNECTED, WIFI_CONNECTED}, WEB_ERROR_STOP)

        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

##### Stand-alone entry point #######

if __name__ == '__main__':

    import requests
    import subprocess
    from constants import RED, YELLOW, NC       # pylint: disable=ungrouped-imports,wrong-import-position
    from messaging import DebugMessageHandler   # pylint: disable=ungrouped-imports,wrong-import-position

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

            # Safely parse integer input; treat non-numeric input as an unrecognised selection.
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
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

    # Subscribe to command and error topics so published messages are printed to console
    cmd_handler = DebugMessageHandler(Commands.subscribe())
    err_handler = DebugMessageHandler(Errors.subscribe())

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    # Stop receiving messages
    Commands.unsubscribe(cmd_handler.get_queue())
    Errors.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    cmd_handler.stop()
    err_handler.stop()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
