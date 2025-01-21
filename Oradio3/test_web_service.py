'''
Simulate button presses as used by Oradio
'''
from multiprocessing import Process, Queue
from threading import Thread

import oradio_utils
import wifi_utils, web_service
from web_service import api_app
from oradio_const import *

def long_press_aan(process, queue):
    '''
    If the Oradio web service is not running: start the web service
    :return process ==> identifier of started process
    '''
    # Check if web service is running
    if not web_service.web_service_active(process):
        print("long_press_aan: web service is not running. Start it")
        # Start Oradio web service
        process = web_service.web_service_start(queue)
    else:
        print("long_press_aan: web service was already running. Reset the timeout counter")
        # Reset Oradio web server timeout
        process.timeout_reset()

    # Return process
    return process

def extra_long_press_aan(process, queue):
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
    return long_press_aan(process, queue)

def any_press_uit(process):
    '''
    If access point is active: remove it
    If web service is running: stop it
    :param process ==> identfies the process to stop
    '''
    # If web service is running: Stop it
    if process:
        print("any_press_uit: web service found. Stop it")
        web_service.web_service_stop(process)

    # Return None indicates web service stopped
    return None

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    # Initialize
    message_queue = Queue()

    def check_messages(message_queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param message_queue = the queue to check for
        """
        while True:
            message = message_queue.get(block=True, timeout=None)
            oradio_utils.logging("info", f"QUEUE-msg received, message ={message}")

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Initialize
    message_listener = None
    web_service_process = None

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
                break
            case 1:
                # Simulate long-press AAN action
                web_service_process = long_press_aan(web_service_process, message_queue)
                print(f"Verify:\n1. web server should be active, is {web_service.web_service_active(web_service_process)}\n2. wifi connection should be active, is: {wifi_utils.get_wifi_connection()}")
                # Start listening to server messages
            case 2:
                # Simulate extra-long-press AAN action
                web_service_process = extra_long_press_aan(web_service_process, message_queue)
                print(f"Verify:\n1. web server should be active, is {web_service.web_service_active(web_service_process)}\n2. wifi connection should be {ACCESS_POINT_NAME}, is: {wifi_utils.get_wifi_connection()}")
            case 3:
                # Simulate an-press UIT action
                web_service_process = any_press_uit(web_service_process)
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
