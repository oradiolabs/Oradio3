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
from threading import Thread, Event
from multiprocessing import Queue
import uvicorn

##### oradio modules ####################
from oradio_logging import oradio_log
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
    MESSAGE_WEB_SERVICE_FAIL_START,
    MESSAGE_WEB_SERVICE_FAIL_STOP,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################
TIMEOUT = 30    # Seconds to wait

class WebServiceMessageHandler:
    """Class to manage message handler running in a thread"""

    def __init__(self, rx_queue, tx_queue):
        """Class constructor: Store parameters and initialise message handler thread"""
        self.started_event = Event()
        self.stop_event = Event()
        self.message_listener = Thread(
            target=self._check_messages,
            args=(
                self.started_event,
                self.stop_event,
                rx_queue,
                tx_queue,
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

    def _check_messages(self, started_event, stop_event, rx_q, tx_q):
        """
        Check if a new message is put into the queue
        If so, read the message from queue, display it, and forward it
        :param queue = the queue to check for
        """
        # Notify the thread is running
        started_event.set()
        oradio_log.debug("WebService: Listening for messages")
        while not stop_event.is_set():
            try:
                # Wait for message. Use timeout to allow checking stop_event
                message = rx_q.get(block=True, timeout=1)
                oradio_log.debug("WebService: Received: %s", message)
#OMJ: This is the place to parse the incoming message before sending a message to control
                if tx_q:
                    # Put message in queue
                    oradio_log.debug("WebService: Forwarding: %s", message)
                    tx_q.put(message)
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                # Timeout occurred, loop again to check stop_event
                pass

class UvicornServerThread:
    """Class to manage Uvicorn server running in a thread"""

    def __init__(self, app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, level="info"):
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
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level=self.level)
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
        self.tx_queue = queue

        # Initialize queue for receiving messeages
        self.rx_queue = Queue()

        # Create message handler
        self.message_handler = WebServiceMessageHandler(self.rx_queue, self.tx_queue)
        self.message_handler.start() # Returns after thread has entered its target function

        # Register wifi service
        self.wifi = WifiService(self.rx_queue)

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
        if self.tx_queue:
            self.tx_queue.put(message)
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
        Start port redirection
        Start DNS redirection
        Start the web server
        Setup access point
        Wait for access point
        """
        # Start web service if not running
        if not self.server.is_running:

            # Set port redirection for all network requests to reach the web service
            oradio_log.debug("Configure port redirection")
            cmd = f"sudo bash -c 'iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure port redirection, error = %s", cmd, error)
                # Send message web server did not start
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

            # Configure DNS redirection
            oradio_log.debug("Redirect DNS")
            cmd = "sudo bash -c 'echo \"address=/#/"+ACCESS_POINT_HOST+"\" > /etc/NetworkManager/dnsmasq-shared.d/redirect.conf'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure DNS redirection, error: %s", cmd, error)
                # Send message with current state and error message
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                # Error, no point continuing
                return

            # Start access point
            self.wifi.wifi_connect(ACCESS_POINT_SSID, None)

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
                time.sleep(1)
                # Check active network again
                state = self.wifi.get_state()

            # Send message captive portal started
            self._send_message(MESSAGE_NO_ERROR)
        else:
            oradio_log.debug("web service is already running")

    def stop(self):
        """
        Public function
        Stop access point
        Set event flag to signal to stop the web server
        Stop port redirection
        Stop DNS redirection
        """
        if self.server.is_running:
            # Initialize error message, assume no error
            err_msg = MESSAGE_NO_ERROR

            # Get wifi state
            state = self.wifi.get_state()

            # Disconnect if access point
            if state == STATE_WIFI_ACCESS_POINT:
                self.wifi.wifi_disconnect()

            # Wait for wifi to be anything but access point
            start_time = time.time()
            while state not in (STATE_WIFI_IDLE, STATE_WIFI_INTERNET, STATE_WIFI_CONNECTED):
                # Check if the timeout has been reached
                if time.time() - start_time > TIMEOUT:
                    oradio_log.error("Timeout waiting for access point to become inactive")
                    # Set error message
                    err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP
                    break
                # Sleep for a short interval to prevent busy-waiting
                time.sleep(1)
                # Check active network again
                state = self.wifi.get_state()

            # Stop the web server
            if not self.server.stop():
                # Set error message
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

            # Remove port redirection
            oradio_log.debug("Remove port redirection")
            cmd = f"sudo bash -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove iptables port redirection, error = %s", cmd, error)
                # Set error message
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

            # Remove address redirection
            oradio_log.debug("Remove DNS redirection")
            cmd = "sudo rm -rf /etc/NetworkManager/dnsmasq-shared.d/redirect.conf"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove DNS redirection, error: %s", cmd, error)
                # Set error message
                err_msg = MESSAGE_WEB_SERVICE_FAIL_STOP

            # Send message captive portal stopped
            self._send_message(err_msg)
        else:
            oradio_log.debug("web service is already stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    import subprocess
    from multiprocessing import Process, Queue

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
            print(f"\nMain: Message received: '{message}'\n")

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
                       " 5-start, connect to wifi, and stop service (emulate web interface submit network)\n"
                       " 6-get wifi state and connection\n"
                       "select: "
                       )

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(INPUT_SELECTION))  # pylint: disable=invalid-name
        except ValueError:
            function_nr = -1  # pylint: disable=invalid-name

        # Execute selected function
        match function_nr:
            case 0:
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
                password = input("Enter password for the network to add (empty for open network): ")
                if name:
                    print("\nStarting the web service...\n")
                    web_service.start()
                    print(f"\nConnecting with '{name}'. Check messages for result\n")
                    web_service.wifi.wifi_connect(name, password)
                    print("\nStopping the web service...\n")
                    web_service.stop()
                else:
                    print(f"\n{YELLOW}No network given{NC}\n")
            case 6:
                print(f"\n{YELLOW}Careful: state may not be correct if wifi is still processing: check messages and run again when in doubt{NC}\n")
                state = web_service.wifi.get_state()
                if state == STATE_WIFI_IDLE:
                    print(f"\nWiFi state: '{state}'\n")
                else:
                    print(f"\nWiFi state: '{state}'. Connected with: '{get_wifi_connection()}'\n")
            case _:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Stop web service message handler
    web_service.message_handler.stop()

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
