'''
Simulate button presses as used by Oradio
'''
from multiprocessing import Process, Queue
from threading import Thread

import oradio_utils
import wifi_utils, web_service
from web_service import api_app
from oradio_const import *

def long_press_aan(process):
    '''
    If the Oradio web service is not running: start the web service
    If the Oradio is not connected to wifi: activate access point
    :return process ==> identifier of started process
    '''
    # Check if web service is running
    if not web_service.web_service_active(process):
        print("long_press_aan: web service is not running. Start it")
        # Start Oradio web service
        process = web_service.web_service_start()
    else:
        print("long_press_aan: web service was already running. Reset the timeout counter")
        # Reset Oradio web server timeout
        process.timeout_reset()

    # Setup access point if not connected to wifi network
    if not wifi_utils.get_wifi_connection():
        print("long_press_aan: No active wifi connection found. Setup access point")
        wifi_utils.access_point_start()
    else:
        print("long_press_aan: active wifi connection found. Don't touch it")

    # Return process
    return process

def extra_long_press_aan(process):
    '''
    If connected: remove existing wifi connection and start captive portal
    :param process ==> identfies the process to stop
    '''
    # Get active wifi connection
    active = wifi_utils.get_wifi_connection()

    # Remove active wifi connection
    # If active wifi connection found which is not an access point: remove it
    if active and active != ACCESS_POINT_NAME:
        print(f"extra_long_press_aan: active wifi connection {active} found. Remove it")
        wifi_utils.wifi_remove(active)

    # Start captive portal: access point + web service
    return long_press_aan(process)

def any_press_uit(process):
    '''
    If access point is active: remove it
    If web service is running: stop it
    :param process ==> identfies the process to stop
    '''
    # Get active wifi connection
    active = wifi_utils.get_wifi_connection()

    # If access point active: remove it
    if active == ACCESS_POINT_NAME:
        print("any_press_uit: access point found. Remove it")
        wifi_utils.wifi_remove(active)

    # If web service is running: Stop it
    if process:
        print("any_press_uit: web service found. Stop it")
        web_service.web_service_stop(process)

    # Return None indicates web service stopped
    return None

# Entry point for stand-alone operation
if __name__ == '__main__':

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

    # Initialize
    message_listener = None
    web_service_process = None

    def check_for_new_command_from_web_server(command_queue, web_service_process):
        '''
        Check if a new command is put into the queue
        If so, read the command from queue and display it
        :param command_queue = the queue to check for
        '''
        oradio_utils.logging("info", "Thread listening to server messages is running")
        while True:
            # Wait for message
            command = command_queue.get(block=True, timeout=None)
            oradio_utils.logging("info", f"QUEUE-msg received, command ={command}")
            # Process message
            if command["command_type"] == COMMAND_WIFI_TYPE:
                # Connect to wifi network
                if command["command"] == COMMAND_WIFI_CONNECT:
                    oradio_utils.logging("info", "Connect to network")
                    wifi_utils.wifi_autoconnect(command["ssid"], command["pswd"])
            """
            Best would be if the timeout counter can be reset from webapp/fastapi_server.py, but don't know how :-(
            Second-best is to have the fastapi_server.py in a catch-all send a request to reset the timeout counter as currently implmented.
            """
            # Reset server timeout: Any api action results in timeout reset
            web_service_process.timeout_reset()

    # Initialize web server process

    # Create message queue for web server to pass messages
    command_queue = Queue()

    # Pass the queue to the web server
    api_app.state.command_queue = command_queue

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-long-press AAN\n"
                       " 2-extra-long-press AAN\n"
                       " 3-press UIT\n"
                       "select: "
                       )

    # User command loop
    print("########## Testing Networking ##########")
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                # Simulate an-press UIT action
                any_press_uit(web_service_process)
                # Stop listening to server messages
                if message_listener:
                    message_listener.kill()
                break
            case 1:
                # Simulate long-press AAN action
                web_service_process = long_press_aan(web_service_process)
                print(f"Verify:\n1. web server should be active, is {web_service.web_service_active(web_service_process)}\n2. wifi connection should be active, is: {wifi_utils.get_wifi_connection()}")
                # Start listening to server messages
                if not message_listener:
                    message_listener = Process(target=check_for_new_command_from_web_server, args=(command_queue, web_service_process))
                    message_listener.start()
            case 2:
                # Simulate extra-long-press AAN action
                web_service_process = extra_long_press_aan(web_service_process)
                print(f"Verify:\n1. web server should be active, is {web_service.web_service_active(web_service_process)}\n2. wifi connection should be {ACCESS_POINT_NAME}, is: {wifi_utils.get_wifi_connection()}")
                # Start listening to server messages
                if not message_listener:
                    message_listener = Process(target=check_for_new_command_from_web_server, args=(command_queue, web_service_process))
                    message_listener.start()
            case 3:
                # Simulate an-press UIT action
                web_service_process = any_press_uit(web_service_process)
                # Stop listening to server messages
                if message_listener:
                    message_listener.kill()
            case _:
                print("\nPlease input a valid number\n")

    '''
    TODO: put check in oradio_utils
    # If monitoring: Stop monitoring
    if system_monitoring:
        sys_monitor.stop()
    '''
