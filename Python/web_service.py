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
import contextlib
from time import sleep, time
from threading import Thread
from multiprocessing import Process, Queue, Event
import uvicorn

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script
from fastapi_server import api_app
from wifi_service import WIFIService

##### GLOBAL constants ####################
from oradio_const import (
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_WEB_SERVICE_TYPE,
    STATE_WEB_SERVICE_IDLE,
    STATE_WEB_SERVICE_ACTIVE,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################
DEBUG_ALIVE_INTERVAL = 60   # Only show debug message every 60 seconds

class Server(uvicorn.Server):
    """
    Wrapper to run FastAPI service in a separate thread using uvicorn
    https://stackoverflow.com/questions/61577643/python-how-to-use-fastapi-and-uvicorn-run-without-blocking-the-thread
    """
    # Ignore signals
    def install_signal_handlers(self):
        """
        Override to avoid signal handler installation in thread context
        """
        pass

    @contextlib.contextmanager
    def run_in_thread(self):
        """
        Run the server in a background thread using a context manager
        """
        thread = Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                sleep(1e-3)
            yield
        finally:
            self.should_exit = True
            thread.join()

class web_service():
    """
    Custom process class for the web interface, via the wifi network or own access point as Captive Portal
    Manage web server and captive portal
    """

    def __init__(self, queue):
        """"
        Class constructor: Setup the class
        """
        # Initialize
        self.msg_q = queue

        # Clear error
        self.error = None

        # Create and store an event for manually stopping the process
        self.event_stop = Event()

        # Track web service status (Events start as 'not set' == STATE_WEB_SERVICE_IDLE)
        self.event_active = Event()

        # Pass the queue to the web server
        api_app.state.message_queue = self.msg_q

        # Register wifi service and send wifi status message
        self.wifi = WIFIService(self.msg_q)
        self.wifi.send_message(MESSAGE_NO_ERROR)

        # Send initial state and error message
        self.send_message(MESSAGE_NO_ERROR)

    def send_message(self, error):
        """
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

    def start(self):
        """
        Start the web server
        Setup access point
        """
        # Web service is not running
        if not self.event_active.is_set():

            oradio_log.debug("Configure port redirection")
            # Set port redirection for all network requests to reach the web service
            cmd = f"sudo bash -c 'iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
            result, error = run_shell_script(cmd)
            if not result:
                oradio_log.error("Error during <%s> to configure port redirection, error = %s", cmd, error)

            # Start web server
            oradio_log.debug("Start FastAPI server")
            config = uvicorn.Config(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_level="info")
            server = Server(config=config)

            # Execute main loop as separate thread
            # ==> Don't use reference so that the python interpreter can garbage collect when thread is done
            Thread(target=self._run, args=(server,)).start()

        else:
            oradio_log.debug("web service is already running")

    def stop(self):
        """
        Set event flag to signal to stop the web server
        """
        if self.event_active.is_set():
            self.event_stop.set()
        else:
            oradio_log.debug("web service is already stopped")

    def get_state(self):
        """
        Return web service status
        """
        if self.event_active.is_set():
            status = STATE_WEB_SERVICE_ACTIVE
        else:
            status = STATE_WEB_SERVICE_IDLE
        return status

    def _run(self, server):
        """
        Process web server task
        """
        # Pass started status to web service
        self.event_active.set()

        # Send state and error message
        self.send_message(MESSAGE_NO_ERROR)

        # Start access point, saving current connection if any
        self.wifi.access_point_start()

        # Running web server non-blocking
        with server.run_in_thread():

            # Confirm starting the web server
            oradio_log.info("Web service is running")

            # Execute in a loop
            while True:

                # Sleeping slows down handling of incoming web service requests. But no sleep means CPU load is 100%. 1s a compromise.
                sleep(1)

                # Check for stop event
                if self.event_stop.is_set():
                    oradio_log.debug("Web service stopped")
                    self.event_stop.clear()
                    break

        # Remove access point, keeping wifi connection if connected
        self.wifi.access_point_stop()

        # Remove port redirection
        oradio_log.debug("Remove port redirection")
        cmd = f"sudo bash -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
        result, error = run_shell_script(cmd)
        if not result:
            oradio_log.error("Error during <%s> to remove iptables port redirection, error = %s", cmd, error)

        # Pass stopped status to web service
        self.event_active.clear()

        # Send state and error message
        self.send_message(MESSAGE_NO_ERROR)

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
            print(f"\nMessage received: '{message}'\n")

    # Initialize
    message_queue = Queue()
    oradio_web_service = web_service(message_queue)

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    InputSelection = ("Select a function, input the number.\n"
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
            FunctionNr = int(input(InputSelection))
        except ValueError:
            FunctionNr = -1

        # Execute selected function
        match FunctionNr:
            case 0:
                print("\nStopping the web service...\n")
                oradio_web_service.stop()
                print("\nExiting test program...\n")
                break
            case 1:
                # Check if a process is listening on WEB_SERVER_HOST:WEB_SERVER_PORT
                result = subprocess.run(f"ss -tuln | grep {WEB_SERVER_HOST}:{WEB_SERVER_PORT}", shell=True, stdout=subprocess.DEVNULL)
                if not result.returncode:
                    print("\nActive web service found\n")
                else:
                    print("\nNo active web service found\n")
            case 2:
                print("\nStarting the web service...\n")
                oradio_web_service.start()
            case 3:
                print("\nStopping the web service...\n")
                oradio_web_service.stop()
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
