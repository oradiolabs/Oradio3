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
import contextlib
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
TIMEOUT = 10   # Seconds to wait for web server process to start/stop

class Server(uvicorn.Server):
    """
    Wrapper to run FastAPI service in a separate thread using uvicorn
    https://stackoverflow.com/questions/61577643/python-how-to-use-fastapi-and-uvicorn-run-without-blocking-the-thread
    """
    # Ignore signals
    def install_signal_handlers(self):
        """ Override to avoid signal handler installation in thread context """
        # Intentionally empty
        pass     # pylint: disable=unnecessary-pass

    @contextlib.contextmanager
    def run_in_thread(self):
        """ Run the server in a background thread using a context manager """
        thread = Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                time.sleep(1e-3)
            yield
        finally:
            self.should_exit = True
            thread.join()

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

        # Create and store an event for manually stopping the process
        self.event_stop = Event()

        # Track web service status (Events start as 'not set' == STATE_WEB_SERVICE_IDLE)
        self.event_active = Event()

        # Register wifi service and send wifi status message
        self.wifi = WifiService(self.msg_q)

        # Pass the class instance to the web server
        api_app.state.service = self

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
        Return web service status
        """
        if self.event_active.is_set():
            status = STATE_WEB_SERVICE_ACTIVE
        else:
            status = STATE_WEB_SERVICE_IDLE
        return status

    def start(self):
        """
        Public function
        Start port redirection
        Start the web server
        Setup access point
        """
        # Start web service if not running
        if not self.event_active.is_set():

            # Set port redirection for all network requests to reach the web service
            oradio_log.debug("Configure port redirection")
            cmd = f"sudo bash -c 'iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure port redirection, error = %s", cmd, error)
                # Send message web server did not start
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

            # Execute main loop as separate thread
            # ==> Don't use reference so that the python interpreter can garbage collect when thread is done
            Thread(target=self._run, daemon=True).start()

            # Wait for the web server to start with a timeout
            if not self.event_active.wait(timeout=TIMEOUT):
                # Send message web server did not start
                self._send_message(MESSAGE_WEB_SERVICE_FAIL_START)
                return

            # Start access point, saving current connection if any
            self.wifi.wifi_connect(ACCESS_POINT_SSID, None)

            # Send message web server has started
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
        if self.event_active.is_set():

            #Initialize
            error = MESSAGE_NO_ERROR

            # Remove access point, restoring wifi connection if any
            self.wifi.wifi_disconnect()

            # Signal the web server to stop
            self.event_stop.set()

            # Wait for the web server to stop with a timeout
            start_time = time.time()
            while self.event_active.is_set():
                if time.time() - start_time >= TIMEOUT:
                    error = MESSAGE_WEB_SERVICE_FAIL_STOP
                    break
                time.sleep(0.1)

            # Remove port redirection
            oradio_log.debug("Remove port redirection")
            cmd = f"sudo bash -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to remove iptables port redirection, error = %s", cmd, error)
                error = MESSAGE_WEB_SERVICE_FAIL_STOP

            # Send state and error message
            self._send_message(error)

        else:
            oradio_log.debug("web service is already stopped")

    def _run(self):
        """
        Process web server task
        """
        # Start web server
        oradio_log.debug("Start FastAPI server")
        config = uvicorn.Config(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_level="info")
        server = Server(config=config)

        # Pass started status to web service
        self.event_active.set()

        # Running web server non-blocking
        with server.run_in_thread():

            # Confirm starting the web server
            oradio_log.info("Web service is running")

            # Signal the web server is running
            self.event_active.set()

            # Wait for stop event
            self.event_stop.wait()

            # Reset stop event
            self.event_stop.clear()

            # Confirm stopping the web server
            oradio_log.debug("Web service stopped")

        # Pass stopped status to web service
        self.event_active.clear()

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
                       " 1-Show web service state\n"
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
