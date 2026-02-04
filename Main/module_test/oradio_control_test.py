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
import sys
from threading import Event, Thread
##### oradio modules ####################
from oradio_logging import oradio_log, DEBUG, CRITICAL
from oradio_control import state_machine, leds, web_service_active, shared_queue
##### GLOBAL constants ####################
from oradio_const import (GREEN, RED, NC,
                          DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED,
                          MESSAGE_SHORT_PRESS_BUTTON_PLAY, MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1, MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET3, MESSAGE_LONG_PRESS_BUTTON_PLAY,
                          MESSAGE_BUTTON_SOURCE,MESSAGE_NO_ERROR,
                          LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3
                          )
from oradio_utils import ( input_prompt_int, input_prompt_float,
                           safe_put,OradioMessage)
from remote_debugger import setup_remote_debugging

##### Local constants ####################
BUTTON_SHORT_PRESS_NAMES = [ MESSAGE_SHORT_PRESS_BUTTON_PLAY,
                            MESSAGE_SHORT_PRESS_BUTTON_STOP,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                            MESSAGE_SHORT_PRESS_BUTTON_PRESET3]
BUTTON_LONG_PRESS_NAMES = [ MESSAGE_LONG_PRESS_BUTTON_PLAY]

def keyboard_input(event:Event):
    '''
    wait for keyboard input with return, and set event if input detected
    :arguments
        event = The specified event will be set upon a keyboard input
    :post_condition:
        the event is set
    '''
    _=input("Press Return on keyboard to stop this test")
    event.set()

def _send_message(message_state: str)-> None:
    """
    Send a OradioMessage formatted message to the message queue
    :Args:
        message_state = the state to be used for the key "state"
    """
    message = {}
    message["source"] = MESSAGE_BUTTON_SOURCE
    message["error"]  = MESSAGE_NO_ERROR
    message["state"]  = message_state
    oradio_msg = OradioMessage(**message)
    if not safe_put(shared_queue, oradio_msg):
        print("Failure when sending message to shared queue")

def _check_led_blinking_status(led_name: str) -> None:
    """
    check if led state is blinking
    :Args
        led_name : the name of the led
    """
    if web_service_active.is_set():
        # select led name should be blinking
        sleep(0.1) # wait short time to enable thread to be active
        if leds.blinking_threads[led_name] is not None:
            # led thread is active, so blinking
            print (f"{GREEN}LED LED_PLAY is BLINKING {NC}")
        else:
            print (f"{RED}LED LED_PLAY is NOT BLINKING{NC}")

def _check_led_status(btn_msg:str) -> None:
    """
    check if led state is according button press state
    :Args
        btn_msg : the button message used during test
    """
    led_name = None
    if btn_msg == MESSAGE_SHORT_PRESS_BUTTON_PLAY:
        led_name = LED_PLAY
    elif btn_msg == MESSAGE_SHORT_PRESS_BUTTON_STOP:
        led_name = LED_STOP
    elif btn_msg ==  MESSAGE_SHORT_PRESS_BUTTON_PRESET1:
        led_name = LED_PRESET1
    elif btn_msg == MESSAGE_SHORT_PRESS_BUTTON_PRESET2:
        led_name = LED_PRESET2
    elif btn_msg ==  MESSAGE_SHORT_PRESS_BUTTON_PRESET3:
        led_name = LED_PRESET3
    sleep(0.1) # give led some time to be processed.
    if leds.get_led_state(led_name):
        print (f"{GREEN}LED {led_name} is ON {NC}")
    else:
        print (f"{RED}LED {led_name} is OFF{NC}")

def _check_stm_state(btn_msg:str)-> None:
    """
    Check the current state-machine state
    :Args
        btn_msg : the button message used during test
    """
    stm_state = None
    if btn_msg == MESSAGE_SHORT_PRESS_BUTTON_PLAY:
        stm_state = "StatePlay"
    elif btn_msg == MESSAGE_SHORT_PRESS_BUTTON_STOP:
        stm_state = "StateStop"
    elif btn_msg ==  MESSAGE_SHORT_PRESS_BUTTON_PRESET1:
        stm_state = "StatePreset1"
    elif btn_msg == MESSAGE_SHORT_PRESS_BUTTON_PRESET2:
        stm_state = "StatePreset2"
    elif btn_msg ==  MESSAGE_SHORT_PRESS_BUTTON_PRESET3:
        stm_state = "StatePreset3"
    if state_machine.state == stm_state:
        print (f"{GREEN}State-machine is at state {stm_state} {NC}")
    else:
        print (f"{RED}Incorrect State-machine state, state={stm_state} {NC}")

def _long_press_button_messages() -> None:
    """
    button selection menu for long button press
    """
    # prepare a option list`
    buttons_option = ["Quit"]\
                     + BUTTON_LONG_PRESS_NAMES\
                     + ["Button stress test"]\
                     + ["ButtonMsgUnknown"]
    selection_done = False
    oradio_log.set_level(CRITICAL)
    while not selection_done:
        #Show test menu with the selection options
        for idx, button_name in enumerate(buttons_option, start=0):
            print(f" {idx} - {button_name}")
        menu_choice = input_prompt_int("Select a LONG BUTTON PRESS message: ", default=-1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case 1 : # 1 long press button + 1 unknown
                selected_button_nr = menu_choice-1
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                button_msg = BUTTON_LONG_PRESS_NAMES[selected_button_nr]
                _send_message(button_msg )
                sleep(4)
                _check_led_blinking_status(LED_PLAY)
                #_check_stm_state(button_msg)
            case 2: # Stress test
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                _long_button_msg_stress_test()
            case 3: # ButtonMsgUnknown
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                oradio_log.set_level(DEBUG)
                _send_message("BUTTON_MSG_UNKNOWN")
            case _:
                print("Please input a valid test option.")
    oradio_log.set_level(DEBUG)

def _short_press_button_messages() -> None:
    """
    button selection menu for short press buttons
    """
    # prepare a option list`
    buttons_option = ["Quit"]\
                     + BUTTON_SHORT_PRESS_NAMES\
                     + ["Button stress test"]\
                     + ["ButtonMsgUnknown"]
    selection_done = False
    oradio_log.set_level(CRITICAL)
    while not selection_done:
        #Show test menu with the selection options
        for idx, button_name in enumerate(buttons_option, start=0):
            print(f" {idx} - {button_name}")
        menu_choice = input_prompt_int("Select a SHORT PRESS BUTTON message: ", default=-1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case 1 | 2 | 3 | 4 | 5 : # 5 short press buttons
                selected_button_nr = menu_choice-1
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                button_msg = BUTTON_SHORT_PRESS_NAMES[selected_button_nr]
                _send_message(button_msg )
                _check_led_status(button_msg)
                _check_stm_state(button_msg)
            case 6: # Stress test
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                _short_button_msg_stress_test()
            case 7: # ButtonMsgUnknown
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                oradio_log.set_level(DEBUG)
                _send_message("BUTTON_MSG_UNKNOWN")
            case _:
                print("Please input a valid test option.")
    oradio_log.set_level(DEBUG)

def _short_button_msg_stress_test()-> None:
    """
    Stress test for button message
    """
    msg_test_sequence_1 = [ MESSAGE_SHORT_PRESS_BUTTON_PLAY,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET3,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP ]
    msg_test_sequence_2 = [ MESSAGE_SHORT_PRESS_BUTTON_PLAY,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET2,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET3,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET3,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET3,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP ]
    msg_test_sequences = [msg_test_sequence_1, msg_test_sequence_2]
    test_sequence = input_prompt_int("Select a test sequence (1 or 2) : ")
    if not test_sequence in (1,2):
        print ("Incorrect number,please, select a valid test number (now 1 is used)")
        test_sequence = 1
    msg_rate = input_prompt_float("Give repetition rate/sec for sending messages as float nr: ")
    if msg_rate in (None,0):
        return
    msg_delay = float(1/msg_rate)

    stop_event = Event()
    keyboard_thread = Thread(target=keyboard_input,
                             args=(stop_event,))
    keyboard_thread.start()
    oradio_log.set_level(CRITICAL)
    while not stop_event.is_set():
        for msg in msg_test_sequences[test_sequence-1]:
            _send_message(msg )
            _check_led_status(msg)
            _check_stm_state(msg)
            sleep(msg_delay)
    oradio_log.set_level(DEBUG)

def _long_button_msg_stress_test()-> None:
    """
    Stress test for button message
    """
    msg_test_sequence_1 = [ MESSAGE_SHORT_PRESS_BUTTON_PLAY,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP,
                          MESSAGE_LONG_PRESS_BUTTON_PLAY,
                          MESSAGE_SHORT_PRESS_BUTTON_PRESET1,
                          MESSAGE_LONG_PRESS_BUTTON_PLAY,
                          MESSAGE_LONG_PRESS_BUTTON_PLAY,
                          MESSAGE_SHORT_PRESS_BUTTON_STOP ]
    msg_test_sequences = [msg_test_sequence_1]
    msg_rate = input_prompt_float("Give repetition rate/sec for sending messages as float nr: ")
    if msg_rate in (None,0):
        return
    msg_delay = float(1/msg_rate)
    stop_event = Event()
    keyboard_thread = Thread(target=keyboard_input,
                             args=(stop_event,))
    keyboard_thread.start()
    oradio_log.set_level(CRITICAL)
    while not stop_event.is_set():
        for msg in msg_test_sequences[0]:
            _send_message(msg )
            sleep(msg_delay)
    oradio_log.set_level(DEBUG)


def _start_module_test():
    """Show menu with test options"""
    # pylint: disable=import-outside-toplevel
    # pylint: disable=unused-import
    import oradio_control
    # pylint: enable=import-outside-toplevel
    # pylint: enable=unused-import
    sleep(7)
    test_options = ["Quit"] + \
                    ["Send Short Press Button message to queue"] + \
                    ["Send Long Press Button messages to queue"] +\
                    ["Send Button Press message to queue with sound mocks"] +\
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
                _short_press_button_messages()
            case 2:
                print(f"\n running {test_options[2]}\n")
                _long_press_button_messages()
            case 3:
                print(f"\n running {test_options[3]}\n")
                print("\n Not implemented yet")
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
