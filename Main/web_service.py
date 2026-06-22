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
@summary:
    Provides two classes for managing the Oradio web interface:

    UvicornServerThread — wraps a FastAPI/ASGI application in a background
    daemon thread and exposes start/stop control.  Readiness is determined by
    polling uvicorn.Server.started, which Uvicorn sets once sockets are bound.

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

##### oradio modules ####################
from oradio_logging import oradio_log, ORADIO_LOG_LEVEL
from oradio_utils import run_shell_script
from fastapi_server import api_app
from wifi_service import WifiService, get_wifi_connection
from messaging import (
    CommandMessage,
    Commands.publish,
    ErrorMessage,
    Errors.publish,
    WIFI_DISCONNECTED,
    WIFI_CONNECTED,
    WIFI_ACCESS_POINT,
    WEB_SOURCE,
    WEB_IDLE,
    WEB_ACTIVE,
    WEB_ERROR_START,
    WEB_ERROR_STOP,
)

##### GLOBAL constants ####################
from oradio_const import (
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_REQUEST_CONNECT,
    MESSAGE_REQUEST_STOP,
)

##### LOCAL constants ####################
READY_TIMEOUT  = 15  # Seconds to wait for server readiness or WiFi state transitions
SOCKET_TIMEOUT = 3   # WebSocket ping interval/timeout in seconds; safe for small devices and networks

# iptables NAT rule that redirects inbound HTTP (port 80) to the portal port.
# Defined once so start() and stop() always reference the identical string.
_IPTABLES_REDIRECT_RULE = (
    f"-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
)

# dnsmasq config file that resolves all hostnames to the captive portal address.
_DNS_REDIRECT_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/redirect.conf")


class UvicornServerThread:
    """
    Manage a Uvicorn ASGI server running in a background daemon thread.

    Uses uvicorn.Server.run() as the thread target directly, and polls
    server.started (set by Uvicorn once sockets are bound) to determine readiness.

    A fresh Server instance is created on each call to start() so that
    internal Uvicorn state (started, should_exit) is always clean.
    """

    def __init__(self, app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, level=ORADIO_LOG_LEVEL):
        """
        Initialise the manager without starting the server.

        Args:
            app:        ASGI application instance to serve.
            host (str): Network interface to bind.
            port (int): TCP port to listen on.
            level (str): Uvicorn log level string.
        """
        # Config is immutable between restarts, so build it once here
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
        self._lock   = Lock()

    def start(self):
        """
        Start the server if not already running.

        Creates a fresh Server instance, launches it on a daemon thread, then
        polls server.started until Uvicorn signals it is accepting connections
        or READY_TIMEOUT seconds elapse.

        Returns:
            bool: True if the server is ready, False on timeout.
        """
        with self._lock:
            if self.is_running:
                oradio_log.debug("Uvicorn server already running")
                return True

            oradio_log.info("Starting Uvicorn server...")
            self._server = uvicorn.Server(self._config)

            self._thread = Thread(target=self._server.run, daemon=True)
            self._thread.start()

            # Poll server.started — set by Uvicorn once sockets are bound
            deadline = time.time() + READY_TIMEOUT
            while not self._server.started:
                if time.time() > deadline:
                    oradio_log.warning("Uvicorn server did not become ready in time")
                    return False
                time.sleep(0.1)

            oradio_log.info("Uvicorn server running")
            return True

    def stop(self):
        """
        Stop the running server and wait for the thread to exit.

        Returns:
            bool: True if stopped cleanly, False if the thread did not exit
                within READY_TIMEOUT seconds.
        """
        with self._lock:
            if not self.is_running:
                oradio_log.debug("Uvicorn server already stopped")
                return True

            oradio_log.debug("Stopping Uvicorn server...")
            self._server.should_exit = True
            self._server.force_exit  = True
            self._thread.join(timeout=READY_TIMEOUT)

            if self._thread.is_alive():
                oradio_log.warning("Uvicorn server thread did not exit cleanly")
                return False

            oradio_log.info("Uvicorn server stopped")
            return True

    @property
    def is_running(self):
        """
        True if the thread is alive and the server has not been asked to exit.

        Returns:
            bool
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

    An internal daemon thread (_check_server_messages) drains a Queue that the
    FastAPI routes write to, and translates queue messages into service actions
    (WiFi connect, portal stop) without blocking the ASGI event loop.
    """

    def __init__(self):
        """
        Initialise the WebService and start the background message listener.

        Sets up the shared queue, wires it into the FastAPI application state,
        creates the Uvicorn wrapper, starts the message-listener thread, and
        publishes the initial WEB_IDLE state to the message bus.
        """
        # Shared queue: FastAPI route handlers post requests here;
        # _check_server_messages() reads and acts on them
        self.fa_queue = Queue()

        self.wifi_service = WifiService()

        # Give the FastAPI app a reference to the queue so route handlers can
        # enqueue requests without importing this module
        api_app.state.queue = self.fa_queue

        self.uvicorn_server = UvicornServerThread(api_app)

        # Daemon thread: drains fa_queue and dispatches to service methods
        self.server_listener = Thread(target=self._check_server_messages, daemon=True)
        self.server_listener.start()

        # Announce initial state so the controller starts from a known baseline
        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

    ##### Private helpers #####

    def _get_nat_rules(self):
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

    def _ensure_port_redirect(self):
        """
        Add the iptables PREROUTING redirect rule if not already present.

        Returns:
            bool: True if the rule is (or was already) in place, False on error.
        """
        rules = self._get_nat_rules()
        if rules is None:
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
            return False
        if _IPTABLES_REDIRECT_RULE in rules:
            return True  # Already present; nothing to do
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

    def _remove_port_redirect(self):
        """
        Delete the iptables PREROUTING redirect rule if present.

        Returns:
            bool: True if the rule is (or was already) absent, False on error.
        """
        rules = self._get_nat_rules()
        if rules is None:
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
            return False
        if _IPTABLES_REDIRECT_RULE not in rules:
            return True  # Already absent; nothing to do
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

    def _ensure_dns_redirect(self):
        """
        Write the dnsmasq wildcard redirect config if it does not already exist.

        Returns:
            bool: True if the file is (or was already) in place, False on error.
        """
        if _DNS_REDIRECT_CONF.exists():
            return True  # Already present; nothing to do
        cmd = f'sudo bash -c \'echo "address=/#/{ACCESS_POINT_HOST}" > {_DNS_REDIRECT_CONF}\''
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Failed to write DNS redirect config: %s", error)
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
            return False
        return True

    def _remove_dns_redirect(self):
        """
        Remove the dnsmasq redirect config file if it exists.

        Returns:
            bool: True if the file is (or was already) absent, False on error.
        """
        if not _DNS_REDIRECT_CONF.exists():
            return True  # Already absent; nothing to do
        result, error = run_shell_script(f"sudo rm -f {_DNS_REDIRECT_CONF}")
        if not result:
            oradio_log.error("Failed to remove DNS redirect config: %s", error)
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))
            return False
        return True

    def _wait_for_wifi_state(self, target_states, error_type):
        """
        Poll until the WiFi interface reaches one of the expected states.

        Polling is simpler than subscribing to WiFi messages here because the
        caller needs to block until the transition completes before proceeding.

        Args:
            target_states (set): Acceptable WifiService state values to wait for.
            error_type:          Error message constant to publish on timeout
                                 (e.g. WEB_ERROR_START or WEB_ERROR_STOP).

        Returns:
            bool: True if a target state was reached, False on timeout.
        """
        deadline = time.time() + READY_TIMEOUT
        while self.wifi_service.get_state() not in target_states:
            if time.time() > deadline:
                oradio_log.error("Timeout waiting for WiFi state in %s", target_states)
                Errors.publish(ErrorMessage(WEB_SOURCE, error_type))
                return False
            time.sleep(1)  # 1-second polling interval avoids busy-waiting
        return True

    def _check_server_messages(self):
        """
        Drain the incoming queue and act on API requests indefinitely.

        Runs on a daemon thread started in __init__.  Blocks on Queue.get()
        with no timeout so it consumes no CPU when idle.

        Recognised request types:

        - MESSAGE_REQUEST_CONNECT: extract SSID and optional password, call
          WifiService.wifi_connect(), then stop the Captive Portal.
        - MESSAGE_REQUEST_STOP: stop the Captive Portal directly.

        The thread exits only when the process terminates (daemon lifecycle).
        """
        while True:
            # Block indefinitely; daemon thread exits automatically with the process
            message = self.fa_queue.get(block=True, timeout=None)
            oradio_log.debug("WebService: message received: '%s'", message)

            request = message.get("request")

            if request == MESSAGE_REQUEST_CONNECT:
                if ssid := message.get("ssid"):
                    pswd = message.get("pswd", "")  # Password is optional for open networks
                    self.wifi_service.wifi_connect(ssid, pswd)
                    self.stop()  # Tear down the Captive Portal after handing off to the new network

            elif request == MESSAGE_REQUEST_STOP:
                self.stop()

    ##### Public interface #####

    @property
    def state(self):
        """
        Current operational state of the web service.

        Returns:
            str: WEB_ACTIVE if the Uvicorn server is running, WEB_IDLE otherwise.
        """
        if self.uvicorn_server and self.uvicorn_server.is_running:
            return WEB_ACTIVE
        return WEB_IDLE

    def start(self):
        """
        Start the Captive Portal service.

        Performs the following steps in order, aborting and publishing a
        WEB_ERROR_START error if any step fails:

        1. Switch WiFi into access point mode.
        2. Ensure the iptables port-redirect rule is in place.
        3. Ensure the dnsmasq DNS redirect config is in place.
        4. Start the Uvicorn web server.
        5. Wait for the WiFi interface to reach WIFI_ACCESS_POINT state.

        Does nothing if the service is already running.
        On success, publishes WEB_ACTIVE to the message bus.
        """
        if self.uvicorn_server.is_running:
            oradio_log.debug("Web service already running")
            return

        self.wifi_service.wifi_connect(ACCESS_POINT_SSID, None)

        if not self._ensure_port_redirect():
            return
        if not self._ensure_dns_redirect():
            return

        # Reset the inactivity timer flag before the server accepts connections
        api_app.state.timer_started = False
        if not self.uvicorn_server.start():
            oradio_log.error("Uvicorn server failed to start")
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_START))
            return

        if not self._wait_for_wifi_state({WIFI_ACCESS_POINT}, WEB_ERROR_START):
            return

        Commands.publish(CommandMessage(WEB_SOURCE, self.state))

    def stop(self):
        """
        Stop the Captive Portal service.

        Reverses the steps performed by start() in order:

        1. Disconnect the access point (if currently active).
        2. Stop the Uvicorn web server.
        3. Remove the iptables port-redirect rule.
        4. Remove the dnsmasq DNS redirect config file.
        5. Wait for the WiFi interface to reach WIFI_DISCONNECTED or WIFI_CONNECTED.

        Each step that fails publishes a WEB_ERROR_STOP error and continues so
        that remaining teardown steps are still attempted.
        On completion, publishes WEB_IDLE to the message bus.
        """
        if self.wifi_service.get_state() == WIFI_ACCESS_POINT:
            self.wifi_service.wifi_disconnect()

        if not self.uvicorn_server.stop():
            Errors.publish(ErrorMessage(WEB_SOURCE, WEB_ERROR_STOP))

        self._remove_port_redirect()
        self._remove_dns_redirect()

        self._wait_for_wifi_state({WIFI_DISCONNECTED, WIFI_CONNECTED}, WEB_ERROR_STOP)

        Commands.publish(CommandMessage(WEB_SOURCE, self.state))


# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when running stand-alone
    import requests
    import subprocess
    from messaging import Topic, Commands.subscribe, Errors.subscribe   # pylint: disable=ungrouped-imports,wrong-import-position
    from oradio_const import RED, YELLOW, NC                            # pylint: disable=ungrouped-imports,wrong-import-position

    # Most stand-alone entry points share this pattern; pylint flags it as duplicate code across modules.
    # pylint: disable=duplicate-code

    def topic_handler(message, topic) -> None:
        """
        Print any message received on a subscribed message bus topic.

        Passed as a callback to Commands.subscribe() and Errors.subscribe()
        so that all bus traffic is visible during interactive testing.

        Args:
            message: The CommandMessage or ErrorMessage received from the bus.
            topic:   The bus topic on which the message arrived; used as a
                     label in the printed output.
        """
        print(f"[{topic}] - Message received: {message!r}")


    def interactive_menu():  # pylint: disable=too-many-branches
        """
        Run an interactive command-line menu for manual WebService testing.

        Creates a WebService instance and presents a numbered menu that lets a
        developer exercise start, stop, WiFi connect, and state inspection
        without running the full Oradio application stack.

        The too-many-branches pylint warning is suppressed because the match
        statement necessarily has one branch per menu option.
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
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1  # Treat non-integer input as an invalid selection

            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    break
                case 1:
                    # Use ss to check whether a process is listening on the configured host:port
                    proc = subprocess.run(
                        f"ss -tuln | grep {WEB_SERVER_HOST}:{WEB_SERVER_PORT}",
                        shell=True, check=False, stdout=subprocess.DEVNULL
                    )
                    if not proc.returncode:
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
                            requests.post(url, json={"ssid": name, "pswd": pswrd}, timeout=READY_TIMEOUT)
                        except requests.exceptions.RequestException:
                            print(f"{RED}Failed to connect. Make sure you have an active web server{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    # WiFi state may lag during transitions; the warning prompts the user to re-check
                    print(f"{YELLOW}Careful: state may not be correct if wifi is still processing: check messages and run again when in doubt{NC}")
                    wifi_state = web_service.wifi_service.get_state()
                    if wifi_state == WIFI_DISCONNECTED:
                        print(f"\nWiFi state: '{wifi_state}'\n")
                    else:
                        print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Subscribe before constructing WebService so no bus messages published
    # during initialisation are missed by the test handler
    Commands.subscribe(topic_handler, (Topic.COMMAND,))
    Errors.subscribe(topic_handler, (Topic.ERROR,))

    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
