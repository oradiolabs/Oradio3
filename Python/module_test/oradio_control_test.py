#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 29, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio Control module test

"""
from time import sleep
from multiprocessing import Queue
##### oradio modules ####################

##### GLOBAL constants ####################
from oradio_const import (LED_NAMES, GREEN, YELLOW, RED, NC,
                          DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED,
                          MESSAGE_SHORT_PRESS_BUTTON_PLAY, MESSAGE_BUTTON_SOURCE,MESSAGE_NO_ERROR )
from oradio_utils import ( input_prompt_int, input_prompt_float,
                           safe_put,OradioMessage)
from remote_debugger import setup_remote_debugging

##### Local constants ####################
BUTTON_SHORT_PRESS_NAMES = [ MESSAGE_SHORT_PRESS_BUTTON_PLAY,
                            MESSAGE_SHORT_PRESS_BUTTON_STOP,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET3]

def _send_message(msg_queue: Queue, message_state: str)-> None:

    message = {}
    message["source"] = MESSAGE_BUTTON_SOURCE
    message["error"]  = MESSAGE_NO_ERROR
    message["state"]  = message_state
    oradio_msg = OradioMessage(**message)
    if not safe_put(msg_queue, oradio_msg):
        print("Failure when sending message to shared queue")

def button_selection() -> Tuple[int, str]:
    """
    button selection menu
    :Returns
    """
    # prepare a option list`
    buttons_option = ["Quit"] + BUTTON_NAMES + ["ButtonUnknown"]
    selection_done = False
    selected_button_name = "BUTTON_UNKNOWN"
    while not selection_done:
        #Show test menu with the selection options
        for idx, button_name in enumerate(buttons_option, start=0):
            print(f" {idx} - {button_name}")
        menu_choice = input_prompt_int("Select a BUTTON: ", default=-1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case 1 | 2 | 3 | 4 | 5 | 6: # 5 buttons + 1 unknown
                selection_done = True
                selected_button_nr = menu_choice-1
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
            case _:
                print("Please input a valid test option.")
    if selected_button_nr != 6:
        selected_button_msg = BUTTON_SHORT_PRESS_NAMES[xxxx]
    return menu_choice, selected_button_msg
    
def _start_module_test():
    """Show menu with test options"""
    import oradio_control
    from oradio_control import shared_queue
    sleep(7)
    test_options = ["Quit"] + \
                    ["Send Button Press message to message queue"] + \
                    ["test 2"] +\
                    ["test 3"] +\
                    ["test 4"] +\
                    ["test 5"]
    while True:
        # --- Test menu selection ---
        print("\nTEST options:")
        for idx, name in enumerate(test_options, start=0):
            print(f" {idx} - {name}")
        test_choice = input_prompt_int("Select test number: ", default=-1)
        match test_choice:
            case 0:
                print("\nExiting test program\n")
                break
            case 1:
                print(f"\n running {test_options[1]}\n")
                _send_message(shared_queue, MESSAGE_SHORT_PRESS_BUTTON_PLAY)
            case 2:
                print(f"\n running {test_options[2]}\n")
            case 3:
                print(f"\n running {test_options[3]}\n")
            case 4:
                print(f"\n running {test_options[4]}\n")
            case 5:
                print(f"\n running {test_options[5]}\n")
            case _:
                print("Please input a valid number.")

if __name__ == '__main__':
    # try to setup a remote debugger connection, if enabled in remote_debugger.py
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    _start_module_test()
    sys.exit()

