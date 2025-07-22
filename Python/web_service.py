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
@version:       2
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
from threading import Thread
from multiprocessing import Event, Process, Queue
import uvicorn

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script
from fastapi_server import api_app
from wifi_service import WifiService

##### GLOBAL constants ####################
from oradio_const import (
    ACCESS_POINT_SSID,
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
TIMEOUT  = 10    # Seconds to wait for web server to start/stop

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
            self.thread.join()
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
        # Register queue
        self.msg_q = queue

        # Register wifi service and send wifi status message
        self.wifi = WifiService(self.msg_q)

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
        self.msg_q.put(message)

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
        Public function
        Start port redirection
        Start the web server
        Setup access point
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
#OMJ: state == idle
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

            # Start access point, saving current connection if any
            self.wifi.wifi_connect(ACCESS_POINT_SSID, None)

            # Start web server
            if not self.server.start():
                # Send message web server did not start
#OMJ: state == idle
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

            # Send message web server started
#OMJ: state == active
            self._send_message(MESSAGE_NO_ERROR)
        else:
            oradio_log.debug("web service is already running")

    def stop(self):
        """
        Public function
        Stop access point
        Set event flag to signal to stop the web server
        Stop port redirection
        """
        if self.server.is_running:

            # Remove access point, restoring wifi connection if any
            self.wifi.wifi_disconnect()

            # Remove port redirection
            oradio_log.debug("Remove port redirection")
            cmd = f"sudo bash -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove iptables port redirection, error = %s", cmd, error)
                # Send state and error message
#OMJ: state == active
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_STOP)

            # Stop the web server
            if not self.server.stop():
                # Send message web server did not stop
#OMJ: state == active
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_STOP)
                return

            # Send message web server stopped
#OMJ: state == idle
            self._send_message(MESSAGE_NO_ERROR)
        else:
            oradio_log.debug("web service is already stopped")

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    import subprocess

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("Listening for messages\n")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"Message received: '{message}'\n")

    # Initialize
    message_queue = Queue()
    web_service = WebService(message_queue)

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    INPUT_SELECTION = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-Show ANY web service state\n"
                       " 2-start web service (long-press-AAN)\n"
                       " 3-stop web service (any-press-UIT)\n"
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
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
