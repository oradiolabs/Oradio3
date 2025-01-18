'''

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
'''
import os, sys, uvicorn, contextlib
from multiprocessing import Process, Event
from time import sleep, time
from threading import Thread

# Running in subdirectory, so tell Python where to find other Oradio modules
sys.path.append("webapp")

##### oradio modules ####################
import oradio_utils
import wifi_utils
from oradio_const import *
from fastapi_server import api_app

# Wrapper to run FastAPI service in a separate thread
# https://stackoverflow.com/questions/61577643/python-how-to-use-fastapi-and-uvicorn-run-without-blocking-the-thread
class Server(uvicorn.Server):

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

# Custom process class for the web interface, via the wifi network or own access point as Captive Portal
class web_service(Process):
    '''
    Manage web server and captive portal
    '''

    def __init__(self, timeout):
        '''
        Class constructor: Setup the class
        :param timeout ==> Number seconds after which the access point will be stopped
        '''

        # Call the parent constructor
        Process.__init__(self)

        # Register timeout after which the access point is stopped
        self.timeout = int(timeout)

        # Mark start time for timeout counter
        self.started = time()

        # Create and store an event for restarting the timeout counter
        self.event_reset = Event()

        # Create and store an event for manually stopping the process
        self.event_stop = Event()

    def timeout_reset(self):
        '''
        Reset timeout counter
        '''
        self.event_reset.set()

    def stop(self):
        '''
        Set event flag to signal to stop the access point
        '''
        self.event_stop.set()

    def run(self):
        '''
        Process task: Called by web_service.start()
        '''

        # Start web server
        config = uvicorn.Config(api_app, host=WEB_SERVICE_HOST, port=WEB_SERVICE_PORT, log_level="trace")
        server = Server(config=config)

        # Running web server non-blocking
        with server.run_in_thread():

            # Confirm starting the web server
            oradio_utils.logging("info", f"Web service started. Timeout = {self.timeout}")

            # Execute in a loop
            while True:

                # Sleeping slows down handling of incoming web service requests. But no sleep means CPU load is 100%. 1s a compromise.
                sleep(1)

                # Check for timeout
                if time() - self.started > self.timeout:
                    oradio_utils.logging("info", "Web service stopped by timeout")
                    break

                # Check for reset event
                if self.event_reset.is_set():
                    oradio_utils.logging("info", "Reset web service timeout counter")
                    self.started = time()
                    self.event_reset.clear()

                # Check for stop event
                if self.event_stop.is_set():
                    oradio_utils.logging("info", "Web service stopped by command")
                    break

# print remaining time before timeout is only for debugging
                print(f"Web service will timeout after {int(self.timeout - (time() - self.started))} seconds",flush=True)

        # Confirm closing the web service
        oradio_utils.logging("info", "Web service stopped")

def web_service_start(*args, **kwargs):
    '''
    Create and start the web service
    :param timeout (optional) ==> identfies the timeout after which the web service stops
    :return process ==> identifier of started process
    '''

    # Set port redirection for all network requests to reach the web service
    script = f"sudo bash -c 'iptables -t nat -I PREROUTING -p tcp --dport 80 -j REDIRECT --to-port {WEB_SERVICE_PORT}'"
    if not oradio_utils.run_shell_script(script):
        oradio_utils.logging("error", f"Failed to configure iptables port redirection")
    else:
        oradio_utils.logging("success", f"Redirection to port {WEB_SERVICE_PORT} configured")


    # Check if timeout parameter is provided, use system constant if not
    timeout = kwargs.get('timeout', WEB_SERVICE_TIMEOUT)

    # Assign process for scheduling to specific CPUs
    os.sched_setaffinity(0, WEB_SERVICE_CPU_MASK)  # 0 = calling process
    oradio_utils.logging("info", f"Web service CPU affinity mask is set to {os.sched_getaffinity(0)}")

    # Create and start the web service
    process = web_service(timeout)
    process.start()

    # Return process
    return process

def web_service_active(process):
    '''
    True if process is not None and instance of web service and process is alive
    :param process ==> identfies the process to stop
    '''
    return process and isinstance(process, web_service) and process.is_alive():

def web_service_stop(process):
    '''
    Stop process and wait for cleanup to be finished
    :param process ==> identfies the process to stop
    '''

    # Stop the web service
    process.stop()

    # Wait for the process to finish
    process.join()

    # Remove port redirection
    script = f"sudo bash -c 'iptables -t nat -F'"
    if not oradio_utils.run_shell_script(script):
        oradio_utils.logging("error", "Failed to remove port redirection")
    else:
        oradio_utils.logging("success", "Port redirection removed")

    # Web service is gone
    return None

# Entry point for stand-alone operation
if __name__ == '__main__':

    from multiprocessing import Queue

    # Check if monitoring is available, i.e. running in Oradio context
    import importlib.util
    system_monitoring = importlib.util.find_spec("system_monitoring")

    '''
    TODO: Move to oradio_utils to determine what to do: logging only, monitoring, ...
    # If monitoring is available then use it
    if system_monitoring:
        import logging.config
        from system_monitoring import system_monitor
        from oradio_data_collector import oradio_data_collector
        from settings import get_config

        # Initialize logging and monitoring
        logging.config.fileConfig(ORADIO_LOGGING_CONFIG)
        status, oradio_config = get_config()
        data_collector = oradio_data_collector()
        sys_monitor = system_monitor(oradio_config, data_collector)

        # No system checks
        sys_monitor.timer_off()
    '''

    def check_for_new_command_from_web_server(command_queue):
        '''
        Check if a new command is put into the queue
        If so, read the command from queue and display it
        :param command_queue = the queue to check for
        '''
        while True:
            command = command_queue.get(block=True, timeout=None)
            oradio_utils.logging("info", f"QUEUE-msg received, command ={command}")
    
    # Create message queue for web service to pass messages
    command_queue = Queue()

    # Pass the queue to the web server
    api_app.state.command_queue = command_queue

    # Start separate thread to monitor the queue
    Thread(target=check_for_new_command_from_web_server, args=(command_queue,), daemon=True).start() # start the thread

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-start web service\n"
                       " 2-restart web service timeout\n"
                       " 3-stop web service\n"
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
                # Check if web service is running
                if web_service_active(web_service_process):
                    web_service_stop(web_service_process)
                break
            case 1:
                # Check if web service is not yet running
                if not web_service_active(web_service_process):
                    timeout = input(f"Enter seconds after which the web service will timeout. Leave empty to use system default {WEB_SERVICE_TIMEOUT}: ")
                    if timeout:
                        web_service_process = web_service_start(timeout=timeout)
                    else:
                        web_service_process = web_service_start()
                else:
                    oradio_utils.logging("warning", "Web service is already running")
            case 2:
                # Check if web service is running
                if web_service_active(web_service_process):
                    # Reset Oradio web service timeout
                    web_service_process.timeout_reset()
                else:
                    oradio_utils.logging("warning", "Web service is not running: cannot reset the timeout")
            case 3:
                # Check if web service is running
                if web_service_active(web_service_process):
                    web_service_process = web_service_stop(web_service_process)
                else:
                    oradio_utils.logging("warning", "Web service is not running: cannot stop")
            case _:
                print("\nPlease input a valid number\n")

    '''
    TODO: put check in oradio_utils
    # If monitoring: Stop monitoring
    if system_monitoring:
        sys_monitor.stop()
    '''
