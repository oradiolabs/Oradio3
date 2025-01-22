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
import os, sys, uvicorn, contextlib
from time import sleep, time
from threading import Thread
from multiprocessing import Process, Queue, Event

##### oradio modules ####################
import oradio_utils, wifi_utils
from fastapi_server import api_app
##### GLOBAL constants ####################
from oradio_const import *
##### LOCAL constants ####################
WEB_SERVICE_TIMEOUT = 600 # 10 minutes

class Server(uvicorn.Server):
    """
    Wrapper to run FastAPI service in a separate thread
    https://stackoverflow.com/questions/61577643/python-how-to-use-fastapi-and-uvicorn-run-without-blocking-the-thread
    """
    # Ignore signals
    def install_signal_handlers(self):
        pass

    @contextlib.contextmanager
    def run_in_thread(self):
        thread = Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                sleep(1e-3)
            yield
        finally:
            self.should_exit = True
            thread.join()

class web_service(Process):
    """
    Custom process class for the web interface, via the wifi network or own access point as Captive Portal
    Manage web server and captive portal
    """

    def __init__(self, timeout, parent_q):
        """"
        Class constructor: Setup the class
        :param timeout ==> Seconds after which the access point will be stopped
        """
        # Call the parent constructor
        Process.__init__(self)

        # Mark start time for timeout counter
        self.started = time()

        # Register timeout after which the access point is stopped
        self.timeout = int(timeout)

        # Create and store an event for restarting the timeout counter
        self.event_reset = Event()

        # Create and store an event for manually stopping the process
        self.event_stop = Event()

        # Track web service status (Events start as 'not set' == STATE_WEB_SERVICE_IDLE)
        self.event_active = Event()

        # Initialize queue for receiving web server messages
        server_q = Queue()

        # Start process to monitor the message queue and send messages to parent process
        self.listener = Process(target=self.server_messages_handler, args=(server_q, parent_q,))
        self.listener.start()

        # Pass the queue to the web server
        api_app.state.message_queue = server_q

    def reset_timeout(self):
        """
        Set event flag to signal timeout counter reset
        """
        self.event_reset.set()

    def stop(self):
        """
        Set event flag to signal to stop the web server
        """
        self.event_stop.set()

    def get_status(self):
        """
        Return web service status
        """
        if self.event_active.is_set():
            status = STATE_WEB_SERVICE_ACTIVE
        else:
            status = STATE_WEB_SERVICE_IDLE
        return status

    def server_messages_handler(self, server_q, parent_q):
        """
        Parse messages in the server queue
        Send message to parent queue
        :param server_q = the queue from the web server to check for
        :param parent_q = the queue to forward other messages
        """
        oradio_utils.logging("info", "Waiting for web server messages")

        # Running in thread until killed
        while True:

            # Wait for web server message
            server_msg = server_q.get(block=True, timeout=None)

            # Show message received
            oradio_utils.logging("info", f"Message received from web server: '{server_msg}'")

            # Show message type
            msg_type = server_msg["type"]
            oradio_utils.logging("info", f"web server message type = '{msg_type}'")

            # Parse message types
            if msg_type == MESSAGE_WEB_SERVER_TYPE:

                # Show message command
                msg_cmd = server_msg["command"]
                oradio_utils.logging("info", f"web server message command = '{msg_cmd}'")

                # Handle 'reset timeout' command
                if msg_cmd == MESSAGE_WEB_SERVER_RESET_TIMEOUT:
                    self.reset_timeout()

                # Handle 'connect wifi' command
                elif msg_cmd == MESSAGE_WEB_SERVER_CONNECT_WIFI:

                    # Save active network connection
                    active = wifi_utils.get_wifi_connection()

                    # If access point is active: remove it
                    wifi_utils.access_point_stop()

                    # Prepare message
                    parent_msg = {}
                    parent_msg["type"] = MESSAGE_WEB_SERVICE_TYPE

                    # Get wifi credentials
                    ssid = server_msg["ssid"]
                    pswd = server_msg["pswd"]

                    # Try to connect to wifi network
                    if wifi_utils.wifi_autoconnect(ssid, pswd):
                        oradio_utils.logging("success", f"Connected to '{ssid}'")

                        # Add 'success' message to be sent to parent
                        parent_msg["data"] = MESSAGE_WEB_SERVICE_CONNECT_OK
                    else:
                        oradio_utils.logging("error", f"Failed to connected to '{ssid}'")

                        # If access point was active: restart it
                        if active == ACCESS_POINT_NAME:
                            wifi_utils.access_point_start()

                        # Add 'fail' message to be sent to parent
                        parent_msg["data"] = MESSAGE_WEB_SERVICE_CONNECT_FAIL

                    # Send status message to parent
                    parent_q.put(parent_msg)

                # Unexpected 'command' message
                else:
                    oradio_utils.logging("error", f"Unsupported message command = '{msg_cmd}'")


            # Unexpected 'type' message
            else:
                oradio_utils.logging("error", f"Unsupported message type = '{msg_type}'")

    def run(self):
        """
        Process web server task
        """
        # Set port redirection for all network requests to reach the web service
        cmd = f"sudo bash -c 'iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
        result, error = oradio_utils.run_shell_script(cmd)
        if not result:
            oradio_utils.logging("error", f"Error during <{cmd}> to configure iptables port redirection, error ={error}")
        else:
            oradio_utils.logging("success", f"Redirection to port {WEB_SERVER_PORT} configured")

        # Start web server
        config = uvicorn.Config(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_level="info")
        server = Server(config=config)

        # Pass started status to web service
        self.event_active.set()

        # Running web server non-blocking
        with server.run_in_thread():

            # Confirm starting the web server
            oradio_utils.logging("info", f"Web server started. Timeout = {self.timeout}")

            # Execute in a loop
            while True:

                # Sleeping slows down handling of incoming web service requests. But no sleep means CPU load is 100%. 1s a compromise.
                sleep(1)

                # Check for timeout
                if time() - self.started > self.timeout:
                    oradio_utils.logging("info", "Web server stopped by timeout")
                    break

                # Check for reset event
                if self.event_reset.is_set():
                    oradio_utils.logging("info", "Reset web server timeout counter")
                    self.started = time()
                    self.event_reset.clear()

                # Check for stop event
                if self.event_stop.is_set():
                    oradio_utils.logging("info", "Web server stopped by command")
                    break

# print remaining time before timeout is only for debugging
                print(f"Web server will timeout after {int(self.timeout - (time() - self.started))} seconds",flush=True)

        # Remove port redirection
        cmd = f"sudo bash -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVER_PORT}'"
        result, error = oradio_utils.run_shell_script(cmd)
        if not result:
            oradio_utils.logging("error", f"Error during <{cmd}> to remove iptables port redirection, error ={error}")
        else:
            oradio_utils.logging("success", "Port redirection removed")

        # If access point active: remove it
        wifi_utils.access_point_stop()

        # Stop listening to server messages
        self.listener.kill()

        # Pass stopped status to web service
        self.event_active.clear()

        # Confirm closing the web service
        oradio_utils.logging("info", "Web server stopped")

def web_service_start(process, parent_q, *args, **kwargs):
    """
    Start web service, and if needed setup access point
    :param process ==> identfies the web service process
    :param parent_q ==> The queue the parent process is listening on
    :param timeout (optional) ==> Set timeout after which the web service stops
    :param force_ap (optional) ==> Setup access point even if already connected
    :return process ==> The web service process
    """
    # Check if timeout parameter is provided, use system constant if not
    timeout = kwargs.get('timeout', WEB_SERVICE_TIMEOUT)

    # Force if no process, otherwise check if force access point is defined, False if not
    force_ap = True if not process else kwargs.get('force_ap', False)

    oradio_utils.logging("info", f"force access point: {force_ap}")

    # Set IP redirect and start web service if it is not active
    if not process or process.get_status() == STATE_WEB_SERVICE_IDLE:

        # Assign process for scheduling to specific CPUs
        os.sched_setaffinity(0, CPU_MASK_WEB_SERVICE)  # 0 = calling process
        oradio_utils.logging("info", f"Web service CPU affinity mask is set to {os.sched_getaffinity(0)}")

        # Create and start the web service
        process = web_service(timeout, parent_q)
        process.start()

    # Web server is running
    else:
        oradio_utils.logging("info", f"Active server found: reset timeout counter")

        # Web service is active, keep alive by resetting timeout counter
        process.reset_timeout()

    # If requested ensure access point is active
    if force_ap:

        # Ensure access point is up and running
        wifi_utils.access_point_start()

        # Send message to parent
        msg = {}
        msg["type"] = MESSAGE_WEB_SERVICE_TYPE
        msg["data"] = MESSAGE_WEB_SERVICE_ACCESS_POINT
        parent_q.put(msg)

    else:
        oradio_utils.logging("info", f"Web server is available on wifi connection '{wifi_utils.get_wifi_connection()}'")

    # Return web service process
    return process

def web_service_stop(process):
    """
    Stop web service and cleanup access point and port redirection
    :param process ==> identfies the web service process
    """
    if process and process.get_status() == STATE_WEB_SERVICE_ACTIVE:

        # Stop the web service
        process.stop()

        # Wait for the process to finish
        process.join()

    # Web service is gone
    return None

# Entry point for stand-alone operation
if __name__ == '__main__':

    def check_messages(message_queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param message_queue = the queue to check for
        """
        oradio_utils.logging("info", f"Listening for web service messages")

        # Running in thread until killed
        while True:

            # Wait for web service message
            message = message_queue.get(block=True, timeout=None)

            # Show message received
            oradio_utils.logging("info", f"Message received from web service: '{message}'")

            # Show message type
            msg_type = message["type"]
            oradio_utils.logging("info", f"web service type = '{msg_type}'")

            # Parse web service message
            if msg_type == MESSAGE_WEB_SERVICE_TYPE:

                # Show message data
                msg_data = message["data"]
                oradio_utils.logging("info", f"web service data = '{msg_data}'")

                # Get current wifi connection
                active = wifi_utils.get_wifi_connection()

                # Parse web service data
                if msg_data == MESSAGE_WEB_SERVICE_CONNECT_OK:
                    oradio_utils.logging("info", f"Web service available on wifi network: '{active}'")
                elif msg_data == MESSAGE_WEB_SERVICE_CONNECT_FAIL:
                    oradio_utils.logging("info", f"Web service failed to connect to new wifi network. The web service is available on wifi network: '{active}'")
                elif msg_data == MESSAGE_WEB_SERVICE_ACCESS_POINT:
                    oradio_utils.logging("info", f"Web service available to access point: '{active}'")

                # Unexpected 'data' message
                else:
                    oradio_utils.logging("error", f"Unsupported message data = '{msg_data}'")

            # Unexpected 'type' message
            else:
                oradio_utils.logging("error", f"Unsupported message type = '{msg_type}'")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-start web service (long-press-AAN)\n"
                       " 2-start web service (extra-long-press-AAN)\n"
                       " 3-restart web service timeout\n"
                       " 4-stop web service (any-press-UIT)\n"
                       "select: "
                       )

    # Initialize web service process
    web_service_process = None

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                web_service_stop(web_service_process)
                break
            case 1:
                # Ask for timeout if process not yet running
                if not web_service_process:
                    timeout = input(f"Enter seconds after which the web service will timeout. Leave empty to use system default {WEB_SERVICE_TIMEOUT}: ")
                # Start web service, using timeout if set
                if timeout:
                    web_service_process = web_service_start(web_service_process, message_queue, timeout=timeout)
                else:
                    web_service_process = web_service_start(web_service_process, message_queue)
            case 2:
                # Ask for timeout if process not yet running
                if not web_service_process:
                    timeout = input(f"Enter seconds after which the web service will timeout. Leave empty to use system default {WEB_SERVICE_TIMEOUT}: ")
                # Start web service, using timeout if set
                if timeout:
                    web_service_process = web_service_start(web_service_process, message_queue, timeout=timeout, force_ap=True)
                else:
                    web_service_process = web_service_start(web_service_process, message_queue, force_ap=True)
            case 3:
                # Reset Oradio web service timeout if web service is running
                if web_service_process:
                    web_service_process.reset_timeout()
                else:
                    oradio_utils.logging("warning", "Web service is not running: cannot reset the timeout")
            case 3:
                web_service_process = web_service_stop(web_service_process)
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
