"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for wifi connectivity services
    :Note
    :Install
    :Documentation
"""

"""
Placeholder for oradio_control: Instantiate USB handler, process messages from USB handler
"""
import oradio_utils
import wifi_utils
from usb_utils import oradio_usb
from oradio_const import *

def connect2wifi(ssid, pswd):
    """
    Use credentials to connect to the wifi network
    """
    # Test if currently connected to ssid
    if not ssid == wifi_utils.get_wifi_connection():
        # Currently not connected to ssid, try to connected
        return wifi_utils.wifi_autoconnect(ssid, pswd)
    else:
        oradio_utils.logging("info", f"Already connected to '{ssid}'")
        # Connected to ssid
        return True

# Entry point for stand-alone operation
if __name__ == '__main__':

    # import when running stand-alone
    from multiprocessing import Process, Queue

    # Initialize
    usb_handler = None
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
            if message["command_type"] == COMMAND_USB_TYPE:
                command_type = message["command_type"]
                oradio_utils.logging("info", f"USB command type = '{command_type}'")
                if message["command"] == COMMAND_USB_STATE_CHANGED:
                    command = message["command"]
                    oradio_utils.logging("info", f"USB command = '{command}'")
                    usb_state = message["usb_state"]
                    oradio_utils.logging("info", f"USB state = '{usb_state}'")
                    if message["usb_label"] == USB_ORADIO:
                        if message["usb_wifi"]:
                            ssid = message["usb_wifi"]["SSID"]
                            pswd = message["usb_wifi"]["PASSWORD"]
                            oradio_utils.logging("info", f"Wifi info found: ssid = '{ssid}', password = '{pswd}'")
                            connect2wifi(pswd, pswd)
                    else:
                        oradio_utils.logging("warning", "USB is not a valid Oradio USB drive: it should be ignored")
                if message["command"] == COMMAND_USB_ERROR_TIMEOUT:
                    oradio_utils.logging("error", "USB service encountered an error. Try to fix by removing the USB drive and inserting it again")

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-instantiate USB handler\n"
                       " 2-manually test wifi connect\n"
                       " 3-stop USB handler\n"
                       "select: "
                       )

    # User command loop
    print("########## Testing USB ##########")
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                # Be a good citizen: stop if active
                if usb_handler:
                    usb_handler.usb_monitor_stop()
                break
            case 1:
                if not usb_handler:
                    usb_handler = oradio_usb(message_queue)
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case 2:
                ssid = input("Enter SSID of the network to connect with: ")
                pswd = input("Enter password for the network to connect with: ")
                if ssid and pswd:
                    connect2wifi(ssid, pswd)
                else:
                    oradio_utils.logging("warning", "No SSID and/or password given")
            case 3:
                if usb_handler:
                    usb_handler.usb_monitor_stop()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
