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
from threading import Thread, Event
from multiprocessing import Process, Queue
import uvicorn

##### oradio modules ####################
from oradio_logging import oradio_log, ORADIO_LOG_LEVEL
from oradio_utils import run_shell_script
from fastapi_server import api_app
from wifi_service import WifiService, get_wifi_connection

##### GLOBAL constants ####################
from oradio_const import (
    RED, GREEN, YELLOW, NC,
    ACCESS_POINT_HOST,
    ACCESS_POINT_SSID,
    STATE_WIFI_IDLE,
    STATE_WIFI_INTERNET,
    STATE_WIFI_CONNECTED,
    STATE_WIFI_ACCESS_POINT,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_WEB_SERVICE_TYPE,
    STATE_WEB_SERVICE_IDLE,
    STATE_WEB_SERVICE_ACTIVE,
    MESSAGE_WEB_SERVICE_STOP,
    MESSAGE_WEB_SERVICE_FAIL_START,
    MESSAGE_WEB_SERVICE_FAIL_STOP,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################
TIMEOUT = 30    # Seconds to wait

class WebServiceMessageHandler:
    """Class to manage message handler running in a thread"""

    def __init__(self, service, server_q, control_q):
        """Class constructor: Store parameters and initialise message handler thread"""
        self.stop_event = Event()
        self.started_event = Event()
        self.message_listener = Thread(
            target=self._check_messages,
            args=(
                service,
                server_q,
                control_q,
            )
        )

    def start(self):
        """Start the message handler"""
        self.message_listener.start()
        self.started_event.wait()

    def stop(self):
        """Stop the message handler"""
        self.stop_event.set()  # Signal the thread to stop
        self.message_listener.join(timeout=TIMEOUT)  # Wait for it to exit or timeout
        if self.message_listener.is_alive():
            oradio_log.warning("WebServiceMessageHandler thread did not exit cleanly")

    def _check_messages(self, service, listen_q, send_q):
        """
        Check if a new message is put into the queue
        If so, read the message from queue, display it, prune/filter it, and if requested forward it
        """
        # Notify the thread is running
        self.started_event.set()
        oradio_log.debug("WebService: Listening for messages")
        while not self.stop_event.is_set():
            try:
                # Wait for message. Use timeout to allow checking stop_event
                message = listen_q.get(block=True, timeout=1)
                oradio_log.debug("WebService: Received: %s", message)

                # Assume message needs to be forwarded
                forward = True

            # Prune/filter messages
                # Ignore wifi access point messages
                if message.get("state") in (STATE_WIFI_ACCESS_POINT,):
                    oradio_log.debug("WebService: Ignoring message: %s", message)
                    forward = False

                # Stop web server
                if message.get("state") in (MESSAGE_WEB_SERVICE_STOP,):
                    oradio_log.debug("WebService: Processing message: %s", message)
                    service.stop()
                    forward = False

                # Pass the message on?
                if forward and send_q:
                    # Put message in queue
                    oradio_log.debug("WebService: Forwarding message: %s", message)
                    send_q.put(message)
            except Exception: # pylint: disable=broad-exception-caught
                # Timeout occurred, loop again to check stop_event
                pass

class UvicornServerThread:
    """Class to manage Uvicorn server running in a thread"""

    def __init__(self, app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, level=ORADIO_LOG_LEVEL):
        """Class constructor: Store parameters and prepare uvicorn thread"""
        self.app = app
        self.host = host
        self.port = port
        self.level = level
        self.server = None
        self.thread = None

    def _run(self):
        """
        Run the uvicorn server: blocking call
        Use self.server.should_exit = True to stop the uvicorn server
        """
        try:
            self.server.run()
        except Exception as ex_err: # pylint: disable=broad-exception-caught
            oradio_log.error("Uvicorn server crashed: %s", ex_err)

    def _wait_until_ready(self):
        """Wait until the server is accepting connections"""
        end = time.time() + TIMEOUT
        while time.time() < end:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.1):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def start(self):
        """Start the server, if not running"""
        if self.thread is None or not self.thread.is_alive():
            oradio_log.debug("Starting Uvicorn server...")
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_config=None, log_level=self.level)
            self.server = uvicorn.Server(config)
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()
            # Wait for server to be ready
            if not self._wait_until_ready():
                oradio_log.warning("Uvicorn server did not become ready in time")
                return False
        else:
            oradio_log.debug("Uvicorn server already running")
        # All done, no errors
        oradio_log.info("Uvicorn server running")
        return True

    def stop(self):
        """Stop the server, if running"""
        if self.thread and self.thread.is_alive():
            oradio_log.debug("Stopping Uvicorn server...")
            self.server.should_exit = True
            self.thread.join(timeout=TIMEOUT)
            if self.thread.is_alive():
                oradio_log.warning("Uvicorn server thread did not exit cleanly")
                return False
        else:
            oradio_log.debug("Uvicorn server already stopped")
        # All done, no errors
        oradio_log.info("Uvicorn server stopped")
        return True

    @property
    def is_running(self):
        """Check if server is runnning"""
        return self.thread is not None and self.thread.is_alive() and not self.server.should_exit

class WebService():
    """
    Custom process class for the web interface, via the wifi network or own access point as Captive Portal
    Manage web server and captive portal
    """

    def __init__(self, queue):
        """"
        Class constructor: Setup the class
        """
        # Register queue for sending message to controller
        self.control_q = queue

        # Initialize queue for receiving messeages
        self.server_q = Queue()

        # Create message handler
        self.message_handler = WebServiceMessageHandler(self, self.server_q, self.control_q)
        self.message_handler.start() # Returns after thread has entered its target function

        # Register wifi service
        self.wifi = WifiService(self.server_q)

        # Pass the class instance to the web server
        api_app.state.service = self

        # Initialize web server
        self.server = UvicornServerThread(api_app)

        # Send initial state and error message
        self._send_message(MESSAGE_NO_ERROR)

    def _send_message(self, error):
        """
        Private function
        Send web service message
        :param error: Error message or code to include in the message
        """
        # Create message
        message = {
            "type": MESSAGE_WEB_SERVICE_TYPE,
            "state": self.get_state(),
            "error": error
        }

        # Put message in queue
        oradio_log.debug("Send web service message: %s", message)
        if self.control_q:
            self.control_q.put(message)
        else:
            oradio_log.error("No queue proviced to send web service message")

    def get_state(self):
        """
        Public function
        Return web service state
        """
        if self.server and self.server.is_running:
            return STATE_WEB_SERVICE_ACTIVE
        return STATE_WEB_SERVICE_IDLE

    def start(self):
        """
        Public function to setup Captive Portal
        Start wifi access point
        Start port redirection
        Start DNS redirection
        Start the web server
        Wait for access point
        """
        # Start access point
        self.wifi.wifi_connect(ACCESS_POINT_SSID, None)

        # Get iptables rules
        cmd = "sudo bash -c \"iptables-save -t nat\""
        result, rules = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to get iptables rules, error = %s", cmd, rules)
            self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
            return

        # Configure port redirection
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

        # Configure DNS redirection
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

        # Start web server
        if not self.server.start():
            # Send message web server did not start
            self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
            return

        # Wait for wifi to be configured as access point or timeout
        start_time = time.time()
        state = self.wifi.get_state()
        while state != STATE_WIFI_ACCESS_POINT:
            # Check if the timeout has been reached
            if time.time() - start_time > TIMEOUT:
                oradio_log.error("Timeout waiting for access point to become active")
                # Send message web server did not start
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return
            # Sleep for a short interval to prevent busy-waiting
            time.sleep(0.1)
            # Check active network again
            state = self.wifi.get_state()

        # Send message captive portal started
        self._send_message(MESSAGE_NO_ERROR)

    def stop(self):
        """
        Public function
        Stop access point, if any
        Stop the web server
        Stop port redirection
        Stop DNS redirection
        """
        # Initialize error message, assume no error
        err_msg = MESSAGE_NO_ERROR

        # Get wifi state
        state = self.wifi.get_state()

        # Disconnect if access point
        if state == STATE_WIFI_ACCESS_POINT:
            self.wifi.wifi_disconnect()

        # Stop the web server
        if not self.server.stop():
            err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Get iptables rules
        cmd = "sudo bash -c \"iptables-save -t nat\""
        result, rules = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to get iptables rules, error = %s", cmd, rules)
            err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Remove port redirection
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

        # Remove address redirection
        oradio_log.debug("Remove DNS redirection")
        if Path("/etc/NetworkManager/dnsmasq-shared.d/redirect.conf").exists():
            cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove DNS redirection, error: %s", cmd, error)
                # Set error message
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

        # Wait for wifi to be anything but access point
        start_time = time.time()
        while state not in (STATE_WIFI_IDLE, STATE_WIFI_INTERNET, STATE_WIFI_CONNECTED):
            # Check if the timeout has been reached
            if time.time() - start_time > TIMEOUT:
                oradio_log.error("Timeout waiting for access point to become inactive")
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP
                break
            # Sleep for a short interval to prevent busy-waiting
            time.sleep(0.1)
            # Check active network again
            state = self.wifi.get_state()

        # Send message captive portal stopped
        self._send_message(err_msg)

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # import when running stand-alone
    import requests
    import subprocess

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("\nMain: Listening for messages")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"{GREEN}Main: Message received: '{message}'{NC}\n")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Start web service AFTER starting the queue handler, as web service sends messages
    web_service = WebService(message_queue)

    # Show menu with test options
    INPUT_SELECTION = ("\nSelect a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Show ANY web service state\n"
                       " 2-start web service (emulate long-press-AAN)\n"
                       " 3-stop web service (emulate any-press-UIT)\n"
                       " 4-start and right away stop web service (test robustness)\n"
                       " 5-start, connect to wifi, stop service (emulate web interface submit network)\n"
                       " 6-get wifi state and connection\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(INPUT_SELECTION)) # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1 # pylint: disable=invalid-name

        # Execute selected function
        match function_nr:
            case 0:
                if web_service.get_state() == STATE_WEB_SERVICE_ACTIVE:
                    print("\nStopping the web service...\n")
                    web_service.stop()
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
                    url = f"http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}/wifi_connect" # pylint: disable=invalid-name
                    try:
                        requests.post(url, json={"ssid": name, "pswd": pswrd}, timeout=TIMEOUT)
                    except: # pylint: disable=bare-except
                        print(f"{RED}Failed to requestr server to start a connection. Make sure you have an active web server{NC}\n")
                else:
                    print(f"\n{YELLOW}No network given{NC}\n")
            case 6:
                print(f"\n{YELLOW}Careful: state may not be correct if wifi is still processing: check messages and run again when in doubt{NC}\n")
                wifi_state = web_service.wifi.get_state() # pylint: disable=invalid-name
                if wifi_state == STATE_WIFI_IDLE:
                    print(f"\nWiFi state: '{wifi_state}'\n")
                else:
                    print(f"\nWiFi state: '{wifi_state}'. Connected with: '{get_wifi_connection()}'\n")
            case _:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Stop web service message handler
    web_service.message_handler.stop()

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
