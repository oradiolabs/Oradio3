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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio Control module test
"""
from time import sleep

##### GLOBAL constants ####################################
from constants import (
    GREEN, RED, YELLOW, NC,
    LED_PLAY, LED_STOP,
    LED_PRESET1, LED_PRESET2, LED_PRESET3,
)

##### Oradio modules ######################################
from log_service import oradio_log, DEBUG, CRITICAL
from oradio_control import state_machine, leds, web_service_active, mpd_control
from utilities import input_prompt
from module_test_harness import KeyPressStopWaiter, module_test_session
from messaging import (
    Commands,
    Incidents,
    CommandMessage,
    BUTTON_SOURCE,
    BUTTON_SHORT_PRESS_PLAY,
    BUTTON_SHORT_PRESS_STOP,
    BUTTON_SHORT_PRESS_PRESET1,
    BUTTON_SHORT_PRESS_PRESET2,
    BUTTON_SHORT_PRESS_PRESET3,
    BUTTON_LONG_PRESS_PLAY,
)

##### LOCAL constants #####################################
BUTTON_SHORT_PRESS_NAMES = [
    BUTTON_SHORT_PRESS_PLAY,
    BUTTON_SHORT_PRESS_STOP,
    BUTTON_SHORT_PRESS_PRESET1,
    BUTTON_SHORT_PRESS_PRESET2,
    BUTTON_SHORT_PRESS_PRESET3
]
BUTTON_LONG_PRESS_NAMES = [BUTTON_LONG_PRESS_PLAY]

BUTTON_UNKNOWN = "ButtonUnknown"

def _check_for_webradio() -> None:
    """
    Check if the current active preset is a web-radio and report it.
    """
    if mpd_control.is_webradio():
        print(f"{YELLOW}Current Preset is a Web-Radio{NC}")

def _check_led_blinking_status(led_name: str) -> None:
    """
    check if led state is blinking
    Args:
        led_name : the name of the led
    """
    # select led name should be blinking
    if leds.blink_workers.get(led_name) is not None:
        # led thread is active, so blinking
        print (f"{GREEN}LED {led_name} is BLINKING {NC}\n")
    else:
        print (f"{RED}LED {led_name} is NOT BLINKING{NC}\n")

def _check_led_status(btn_msg: str) -> None:
    """
    check if led state is according button press state
    Args:
        btn_msg : the button message used during test
    """
    led_name = None
    if btn_msg == BUTTON_SHORT_PRESS_PLAY:
        led_name = LED_PLAY
    elif btn_msg == BUTTON_SHORT_PRESS_STOP:
        led_name = LED_STOP
    elif btn_msg ==  BUTTON_SHORT_PRESS_PRESET1:
        led_name = LED_PRESET1
    elif btn_msg == BUTTON_SHORT_PRESS_PRESET2:
        led_name = LED_PRESET2
    elif btn_msg ==  BUTTON_SHORT_PRESS_PRESET3:
        led_name = LED_PRESET3

    if led_name is None:
        oradio_log.error("Unrecognised button message: %s", btn_msg)
        return

    sleep(0.1) # give led some time to be processed.
    if leds.get_led_state(led_name):
        print (f"{GREEN}LED {led_name} is ON {NC}")
    else:
        print (f"{RED}LED {led_name} is OFF{NC}")

def _check_stm_state(btn_msg: str) -> None:
    """
    Check the current state-machine state
    Args:
        btn_msg : the button message used during test
    """
    stm_state = None
    if btn_msg == BUTTON_SHORT_PRESS_PLAY:
        stm_state = "StatePlay"
    elif btn_msg == BUTTON_SHORT_PRESS_STOP:
        stm_state = "StateStop"
    elif btn_msg ==  BUTTON_SHORT_PRESS_PRESET1:
        stm_state = "StatePreset1"
    elif btn_msg == BUTTON_SHORT_PRESS_PRESET2:
        stm_state = "StatePreset2"
    elif btn_msg ==  BUTTON_SHORT_PRESS_PRESET3:
        stm_state = "StatePreset3"

    if stm_state is None:
        oradio_log.error("Unrecognised button message: %s", btn_msg)
        return

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
                     + [BUTTON_SHORT_PRESS_STOP]\
                     + ["Button stress test"]\
                     + ["ButtonMsgUnknown"]
    selection_done = False
    oradio_log.set_level(CRITICAL)
    while not selection_done:
        #Show test menu with the selection options
        for idx, button_name in enumerate(buttons_option, start=0):
            print(f" {idx} - {button_name}")
        menu_choice = input_prompt("Select a LONG BUTTON PRESS message: ", int, -1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case n if 1 <= n <= len(BUTTON_LONG_PRESS_NAMES): # long press buttons
                selected_button_nr = menu_choice-1
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                button_msg = BUTTON_LONG_PRESS_NAMES[selected_button_nr]
                Commands.publish(CommandMessage(BUTTON_SOURCE, button_msg))
                print("Wait for wifi-AP to start: ....")
                if web_service_active.wait(timeout=10):
                    print(f"{GREEN} Web server is active {NC}")
                    sleep(3)
                    _check_led_blinking_status(LED_PLAY)
                else:
                    print(f"{RED} Web server is not active {NC}")
            case n if n == len(BUTTON_LONG_PRESS_NAMES) + 1: # Stop button
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                Commands.publish(CommandMessage(BUTTON_SOURCE, BUTTON_SHORT_PRESS_STOP))
            case n if n == len(BUTTON_LONG_PRESS_NAMES) + 2: # Stress test
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                _long_button_msg_stress_test()
            case n if n == len(BUTTON_LONG_PRESS_NAMES) + 3: # ButtonMsgUnknown
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                oradio_log.set_level(DEBUG)
                Commands.publish(CommandMessage(BUTTON_SOURCE, BUTTON_UNKNOWN))
                oradio_log.set_level(CRITICAL)
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
        menu_choice = input_prompt("Select a SHORT PRESS BUTTON message: ", int, -1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case n if 1 <= n <= len(BUTTON_SHORT_PRESS_NAMES): # short press buttons
                selected_button_nr = menu_choice-1
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                button_msg = BUTTON_SHORT_PRESS_NAMES[selected_button_nr]
                Commands.publish(CommandMessage(BUTTON_SOURCE, button_msg))
                _check_led_status(button_msg)
                _check_stm_state(button_msg)
                _check_for_webradio()
            case n if n == len(BUTTON_SHORT_PRESS_NAMES) + 1: # Stress test
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                _short_button_msg_stress_test()
            case n if n == len(BUTTON_SHORT_PRESS_NAMES) + 2: # ButtonMsgUnknown
                print(f"\nThe selected BUTTON press is {buttons_option[menu_choice]}\n")
                oradio_log.set_level(DEBUG)
                Commands.publish(CommandMessage(BUTTON_SOURCE, BUTTON_UNKNOWN))
                oradio_log.set_level(CRITICAL)
            case _:
                print("Please input a valid test option.")
    oradio_log.set_level(DEBUG)

def _short_button_msg_stress_test() -> None:
    """
    Stress test that repeatedly publishes a chosen sequence of short-press
    button messages at a user-specified rate, checking LED and state-machine
    state after each message, until Return is pressed.
    """
    msg_test_sequence_1 = [ BUTTON_SHORT_PRESS_PLAY,
                          BUTTON_SHORT_PRESS_STOP,
                          BUTTON_SHORT_PRESS_PRESET1,
                          BUTTON_SHORT_PRESS_PRESET2,
                          BUTTON_SHORT_PRESS_PRESET3,
                          BUTTON_SHORT_PRESS_STOP ]
    msg_test_sequence_2 = [ BUTTON_SHORT_PRESS_PLAY,
                          BUTTON_SHORT_PRESS_STOP,
                          BUTTON_SHORT_PRESS_PRESET1,
                          BUTTON_SHORT_PRESS_PRESET1,
                          BUTTON_SHORT_PRESS_PRESET1,
                          BUTTON_SHORT_PRESS_STOP,
                          BUTTON_SHORT_PRESS_PRESET2,
                          BUTTON_SHORT_PRESS_PRESET2,
                          BUTTON_SHORT_PRESS_PRESET2,
                          BUTTON_SHORT_PRESS_STOP,
                          BUTTON_SHORT_PRESS_PRESET3,
                          BUTTON_SHORT_PRESS_PRESET3,
                          BUTTON_SHORT_PRESS_PRESET3,
                          BUTTON_SHORT_PRESS_STOP ]
    msg_test_sequences = [msg_test_sequence_1, msg_test_sequence_2]
    test_sequence = input_prompt("Select a test sequence (1 or 2) : ", int, -1)
    if test_sequence not in (1, 2):
        print ("Incorrect number, please select a valid test number (now 1 is used)")
        test_sequence = 1
    msg_rate = input_prompt("Give repetition rate/sec for sending messages as float nr: ", float, -1.0)
    if msg_rate is None or msg_rate <= 0:
        print(f"{YELLOW}invalid repetition rate{NC}")
        return
    msg_delay = float(1/msg_rate)

    waiter = KeyPressStopWaiter()
    waiter.safe_start()
    oradio_log.set_level(CRITICAL)
    while not waiter.stopping:
        for msg in msg_test_sequences[test_sequence-1]:
            Commands.publish(CommandMessage(BUTTON_SOURCE, msg))
            _check_led_status(msg)
            _check_stm_state(msg)
            sleep(msg_delay)
    oradio_log.set_level(DEBUG)
    waiter.safe_stop()

def _long_button_msg_stress_test() -> None:
    """
    Stress test that repeatedly publishes a fixed sequence of long- and
    short-press button messages at a user-specified rate, until Return is
    pressed.
    """
    msg_test_sequence_1 = [ BUTTON_SHORT_PRESS_PLAY,
                          BUTTON_SHORT_PRESS_STOP,
                          BUTTON_LONG_PRESS_PLAY,
                          BUTTON_SHORT_PRESS_PRESET1,
                          BUTTON_LONG_PRESS_PLAY,
                          BUTTON_LONG_PRESS_PLAY,
                          BUTTON_SHORT_PRESS_STOP ]
    msg_test_sequences = [msg_test_sequence_1]
    msg_rate = input_prompt("Give repetition rate/sec for sending messages as float nr: ", float, -1.0)
    if msg_rate is None or msg_rate <= 0:
        print(f"{YELLOW}invalid repetition rate{NC}")
        return
    msg_delay = float(1/msg_rate)
    waiter = KeyPressStopWaiter()
    waiter.safe_start()
    oradio_log.set_level(CRITICAL)
    while not waiter.stopping:
        for msg in msg_test_sequences[0]:
            Commands.publish(CommandMessage(BUTTON_SOURCE, msg))
            sleep(msg_delay)
    oradio_log.set_level(DEBUG)
    waiter.safe_stop()

def _run_oradio_control() -> None:
    """
    Block until Return is pressed.

    oradio_control's actual processing runs in background threads started
    as a side effect of importing the oradio_control module; this function
    does not start or drive any loop itself, it simply keeps the test
    program alive (and out of the test menu) while that background
    processing runs, until the user is done observing it.
    """
    print("Oradio control main loop running")
    _ = input("Press Return on keyboard to stop")

def _start_module_test():
    """Show menu with test options"""
    # pylint: disable=duplicate-code
    # Give oradio_control's background threads (imported at module level)
    # time to fully start up before driving tests against them.
    sleep(7)
    test_options = ["Quit"] + \
                    ["Run oradio_control"] +\
                    ["Send Short Press Button message to queue"] +\
                    ["Send Long Press Button messages to queue"] +\
                    ["Send Button Press message to queue with sound mocks"] +\
                    ["test 5 (tbd)"]
    while True:
        # --- Test menu selection ---
        print("\nTEST options:")
        for idx, name in enumerate(test_options, start=0):
            print(f" {idx} - {name}")
        test_choice = input_prompt("Select test number: ", int, -1)
        match test_choice:
            case 0:
                print("\nExiting test program\n")
                break
            case 1:
                print(f"\n running {test_options[1]}\n")
                _run_oradio_control()
            case 2:
                print(f"\n running {test_options[2]}\n")
                _short_press_button_messages()
            case 3:
                print(f"\n running {test_options[3]}\n")
                _long_press_button_messages()
            case 4:
                print(f"\n running {test_options[4]}\n")
                print("\n Not implemented yet")
            case 5:
                print(f"\n running {test_options[5]}\n")
                print("\n Not implemented yet")
            case _:
                print("Please input a valid number.")


if __name__ == '__main__':
    with module_test_session(Incidents):
        _start_module_test()
