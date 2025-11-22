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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for web interface and Captive Portal
    :Note
    :Install
    :Documentation
        https://www.uvicorn.org/
        https://fastapi.tiangolo.com/
        https://captivebehavior.wballiance.com/
        https://superfastpython.com/multiprocessing-in-python/
"""
import time
import socket
from pathlib import Path
from threading import Thread, Lock
from multiprocessing import Process, Queue
from asyncio import CancelledError
import uvicorn

##### oradio modules ####################
from oradio_logging import oradio_log, ORADIO_LOG_LEVEL
from oradio_utils import safe_put, run_shell_script
from fastapi_server import api_app
from wifi_service import WifiService, get_wifi_connection

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    STATE_WIFI_IDLE,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_WEB_SERVICE_SOURCE,
    STATE_WEB_SERVICE_IDLE,
    STATE_WEB_SERVICE_ACTIVE,
    STATE_WEB_SERVICE_STOP,
    MESSAGE_WEB_SERVICE_FAIL_START,
    MESSAGE_WEB_SERVICE_FAIL_STOP,
    MESSAGE_NO_ERROR,
)

##### LOCAL constants ####################
WR_TIMEOUT = 30     # Seconds to wait for server
WS_TIMEOUT = 2      # Seconds between pings. Safe for small devices and small networks.

class UvicornServerThread:
    """
    Manage a Uvicorn ASGI server running in a background thread
    Provides start/stop control, thread safety, and readiness checks
    """
    def __init__(self, app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, level=ORADIO_LOG_LEVEL):
        """
        Initialize the server manager
        app: ASGI application instance
        host (str): Host address to bind to
        port (int): Port number to bind to
        level (str): Logging level for Uvicorn
        """
        self.app = app
        self.host = host
        self.port = port
        self.level = level
        self.server = None
        self.thread = None
        self.lock = Lock()
        self.last_exception = None

    def _run(self):
        """
        Run the Uvicorn server (blocking call)
        The server can be stopped by setting `self.server.should_exit = True`
        """
        try:
            self.server.run()
        except (OSError, ImportError, RuntimeError, ValueError, CancelledError) as ex_err:
            self.last_exception = ex_err
            oradio_log.error("Uvicorn server crashed: %s", ex_err)

    def _wait_until_ready(self):
        """
        Block until the server is accepting connections or timeout occurs
        Returns: True if server became ready, False on timeout
        """
        end = time.time() + WR_TIMEOUT
        while time.time() < end:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def start(self):
        """
        Start the server if not already running
        Returns: True if server started successfully, False otherwise
        """
        with self.lock:
            if self.is_running:
                oradio_log.debug("Uvicorn server already running")
                return True

            oradio_log.debug("Starting Uvicorn server...")
            self.last_exception = None
            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_config=None,
                log_level=self.level,
                ws_ping_interval = WS_TIMEOUT,  # Send ping every WS_TIMEOUT seconds
                ws_ping_timeout = WS_TIMEOUT,   # Close connection if no pong after WS_TIMEOUT seconds
            )
            self.server = uvicorn.Server(config)
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()

            # Wait for the server to become ready
            if not self._wait_until_ready():
                if self.last_exception:
                    oradio_log.error("Uvicorn server failed to start: %s", self.last_exception)
                else:
                    oradio_log.warning("Uvicorn server did not become ready in time")
                return False

            oradio_log.info("Uvicorn server running")
            return True

    def stop(self):
        """
        Stop the server if it is running
        Returns: True if server stopped cleanly, False otherwise
        """
        with self.lock:
            if not self.is_running:
                oradio_log.debug("Uvicorn server already stopped")
                return True

            oradio_log.debug("Stopping Uvicorn server...")
            self.server.should_exit = True
            self.thread.join(timeout=WR_TIMEOUT)

            if self.thread.is_alive():
                oradio_log.warning("Uvicorn server thread did not exit cleanly")
                return False

            oradio_log.info("Uvicorn server stopped")
            return True

    @property
    def is_running(self):
        """
        Check if the server thread is alive and not exiting
        Returns: True if server is running, False otherwise
        """
        return (
            self.thread is not None and
            self.thread.is_alive() and
            self.server is not None and
            not self.server.should_exit
        )

class WebService:
    """
    Manage the web interface over wifi or an internal access point (Captive Portal)
    This class coordinates:
    - wifi access point setup
    - Captive portal redirection (HTTP and DNS)
    - Launching and stopping the web server
    - Relaying status and error messages to a controller
    It starts a dedicated process to listen for server messages, configures
    iptables and DNS redirection, and ensures the web service is active when required
    """
    def __init__(self, queue):
        """
        Initialize a new WebService instance
        queue (multiprocessing.Queue): Outgoing queue for sending messages to the controller
        """
        # Queue for sending messages to the external controller
        self.outgoing_q = queue

        # Queue for receiving messages from the web server or wifi service
        self.incoming_q = Queue()

        # Create the wifi service interface
        self.wifi_service = WifiService(self.incoming_q)

        # Pass the receiving queue to the web server API
        api_app.state.queue = self.incoming_q

        # Prepare the embedded web server: Uvicorn running in a background thread
        self.uvicorn_server = UvicornServerThread(api_app)

        # Spawn a separate process to continuously monitor incoming messages
        # NOTE: last part of __init__, as Process COPIES the variables
        self.server_listener = Process(target=self._check_server_messages)
        self.server_listener.start()

        # Send initial "no error" state to the controller
        self._send_message(MESSAGE_NO_ERROR)

    def _check_server_messages(self):
        """
        Continuously read messages from the incoming queue and forward them to the controller
        Runs as a separate process to avoid blocking the main thread
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = self.incoming_q.get(block=True, timeout=None)
            oradio_log.debug("WebService: message received: '%s'", message)

            # Default all messages are forwarded
            forward = True

            # Check if message contains wifi credentials (:= operator assigns and test if true)
            if ssid := message.get("ssid"):
                # password can be empty for open networks
                pswd = message.get("pswd", "")
                # Connect to network with give credentials
                self.wifi_service.wifi_connect(ssid, pswd)
                # Stop the Captive Portal service
                self.stop()
                # Do not forward this message
                forward = False

            # Check if message wants to stop the captive portal
            if (message.get("source") == MESSAGE_WEB_SERVICE_SOURCE and
                message.get("request") == STATE_WEB_SERVICE_STOP):
                # Stop the Captive Portal service
                self.stop()
                # Do not forward this message
                forward = False

            # Forward message to the outgoing queue
            if forward:
                oradio_log.debug("WebService: Forwarding message: %s", message)
                safe_put(self.outgoing_q, message)

    def _send_message(self, error):
        """
        Send a structured status message to the controller
        error (str): Error message or code to include in the message
        """
        # Build the status message
        message = {
            "source": MESSAGE_WEB_SERVICE_SOURCE,
            "state" : self.get_state(),
            "error" : error
        }

        # Send message to the outgoing queue
        oradio_log.debug("Send web service message: %s", message)
        safe_put(self.outgoing_q, message)

    def get_state(self):
        """
        Return the current operational state of the web service
        Returns: STATE_WEB_SERVICE_ACTIVE if the server is running,
                 STATE_WEB_SERVICE_IDLE otherwise
        """
        if self.uvicorn_server and self.uvicorn_server.is_running:
            return STATE_WEB_SERVICE_ACTIVE
        return STATE_WEB_SERVICE_IDLE

    def start(self):
        """
        Start the Captive Portal service
        This performs:
        - wifi access point activation
        - HTTP port redirection (iptables)
        - DNS redirection to the captive portal
        - uvicorn web server start
        The method blocks until the access point is confirmed active or a timeout occurs
        """
        # Enable wifi access point mode
        self.wifi_service.wifi_connect(ACCESS_POINT_SSID, None)

        # Get current NAT (iptables) configuration
        cmd = "sudo bash -c \"iptables-save -t nat\""
        result, rules = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to get iptables rules, error = %s", cmd, rules)
            self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
            return

        # Configure HTTP port redirection to captive portal port
        oradio_log.debug("Configure port redirection")
        if f"-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}" not in rules:
            cmd = (
                f"sudo iptables -t nat -A PREROUTING "
                f"-p tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
            )
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure port redirection, error = %s", cmd, error)
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

        # Configure DNS redirection to captive portal host
        oradio_log.debug("Redirect DNS")
        if not Path("/etc/NetworkManager/dnsmasq-shared.d/redirect.conf").exists():
            cmd = "sudo bash -c 'echo \"address=/#/"+ACCESS_POINT_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure DNS redirection, error: %s", cmd, error)
                # Send message with current state and error message
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                # Error, no point continuing
                return

        # Start the web server
        if not self.uvicorn_server.start():
            # Send message web server did not start
            self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
            return

        # Wait until wifi is confirmed to be in access point mode, or timeout
        start_time = time.time()
        state = self.wifi_service.get_state()
        while state != STATE_WIFI_ACCESS_POINT:
            # Check if the timeout has been reached
            if time.time() - start_time > WR_TIMEOUT:
                oradio_log.error("Timeout waiting for access point to become active")
                # Send message web server did not start
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return
            # Sleep for a short interval to prevent busy-waiting
            time.sleep(0.5)
            # Check active network again
            state = self.wifi_service.get_state()

        # Notify controller that the captive portal is active
        self._send_message(MESSAGE_NO_ERROR)

    def stop(self):
        """
        Stop the Captive Portal service
        This performs:
        - wifi access point shutdown
        - uvicorn web server stop
        - Removal of HTTP port redirection
        - Removal of DNS redirection
        The method blocks until the access point is confirmed inactive or a timeout occurs
        """
        # Assume no error until proven otherwise
        err_msg = MESSAGE_NO_ERROR

        # Disconnect wifi if currently in access point mode
        state = self.wifi_service.get_state()
        if state == STATE_WIFI_ACCESS_POINT:
            self.wifi_service.wifi_disconnect()

        # Stop the uvicorn web server
        if not self.uvicorn_server.stop():
            err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Get current NAT (iptables) configuration
        cmd = "sudo bash -c \"iptables-save -t nat\""
        result, rules = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to get iptables rules, error = %s", cmd, rules)
            err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Remove HTTP port redirection if present
        oradio_log.debug("Remove port redirection")
        if f"-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}" in rules:
            cmd = (
                f"sudo iptables -t nat -D PREROUTING "
                f"-p tcp --dport 80 -j REDIRECT --to-ports {WEB_SERVER_PORT}"
            )
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove iptables port redirection, error = %s", cmd, error)
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Remove DNS redirection file if it exists
        oradio_log.debug("Remove DNS redirection")
        if Path("/etc/NetworkManager/dnsmasq-shared.d/redirect.conf").exists():
            cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove DNS redirection, error: %s", cmd, error)
                # Set error message
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Wait until wifi is no longer in access point mode, or timeout
        start_time = time.time()
        while state not in (STATE_WIFI_IDLE, STATE_WIFI_CONNECTED):
            # Check if the timeout has been reached
            if time.time() - start_time > WR_TIMEOUT:
                oradio_log.error("Timeout waiting for access point to become inactive")
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP
                break
            # Sleep for a short interval to prevent busy-waiting
            time.sleep(0.1)
            # Check active network again
            state = self.wifi_service.get_state()

        # Notify controller that the captive portal has stopped (or failed to stop cleanly)
        self._send_message(err_msg)

    def close(self):
        """
        Stop the web service
        - Stop Captive Portal service
        - Close the wifi service
        """
        # Ensure Captive Portal is removed
        self.stop()

        # Close wifi service and unsubscribe from events
        self.wifi_service.close()

        # Stop listening to server messages
        if self.server_listener:
            self.server_listener.terminate()

        oradio_log.info("web service closed")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    import requests
    import subprocess

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\n{GREEN}Message received: '{message}'{NC}\n")

    # Pylint PEP8 ignoring limit of max 12 branches is ok for test menu
    def interactive_menu(queue):    # pylint: disable=too-many-branches
        """Show menu with test options"""
        # Initialize
        web_service = WebService(queue)

        # Show menu with test options
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Show ANY web service state\n"
            " 2-start web service (emulate long-press-AAN)\n"
            " 3-stop web service (emulate any-press-UIT)\n"
            " 4-start and right away stop web service (test robustness)\n"
            " 5-start, connect to wifi, stop service (emulate web interface submit network)\n"
            " 6-get wifi state and connection\n"
            "Select: "
        )

        # User command loop
        while True:
            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1
            # Execute selected function
            match function_nr:
                case 0:
                    print("\nStopping the web service...\n")
                    web_service.close()
                    print("\nExiting test program...\n")
                    break
                case 1:
                    # Check if a process is listening on WEB_SERVER_HOST:WEB_SERVER_PORT
                    proc = subprocess.run(f"ss -tuln | grep {WEB_SERVER_HOST}:{WEB_SERVER_PORT}", shell=True, check=False, stdout=subprocess.DEVNULL)
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
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                        url = f"http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}/wifi_connect"
                        try:
                            requests.post(url, json={"ssid": name, "pswd": pswrd}, timeout=WR_TIMEOUT)
                        except requests.exceptions.RequestException:
                            print(f"{RED}Failed to connect. Make sure you have an active web server{NC}\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    print(f"{YELLOW}Careful: state may not be correct if wifi is still processing: check messages and run again when in doubt{NC}")
                    wifi_state = web_service.wifi_service.get_state()
                    if wifi_state == STATE_WIFI_IDLE:
                        print(f"\nWiFi state: '{wifi_state}'\n")
                    else:
                        print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Present menu with tests
    interactive_menu(message_queue)

    # Stop listening to messages
    message_listener.terminate()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
