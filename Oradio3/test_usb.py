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
from usb_service import oradio_usb
from oradio_const import *

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
            # Wait for message
            message = message_queue.get(block=True, timeout=None)

            # Show message received
            oradio_utils.logging("info", f"QUEUE-msg received, message ={message}")

            # Show message type
            msg_type = message["type"]
            oradio_utils.logging("info", f"USB type = '{msg_type}'")

            # Parse USB message
            if msg_type == MESSAGE_USB_TYPE:

                # Show USB state
                usb_state = message["state"]
                oradio_utils.logging("info", f"USB state = '{usb_state}'")

                # Error state has additional info
                if usb_state == STATE_USB_ERROR:

                    # Show error message
                    usb_error = message["error"]
                    oradio_utils.logging("warning", f"USB error: '{usb_error}'")

                    # Label error has label found
                    if usb_error == MESSAGE_USB_ERROR_LABEL:

                        # Show label
                        usb_label = message["label"]
                        oradio_utils.logging("warning", f"USB drive has invalid label: '{usb_label}'")

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-instantiate USB handler\n"
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
                if usb_handler:
                    usb_handler.usb_monitor_stop()
                else:
                    oradio_utils.logging("warning", "USB monitor not running")
            case _:
                print("\nPlease input a valid number\n")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
