#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Jan 22, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       
    Module test for touch_buttons functions
    * Testing BUTTONS touched 
    * Class extensions for button simulations

"""
from threading import Event, Thread
from multiprocessing import Queue
import sys
from time import sleep, perf_counter

##### local oradio import modules ####################
from oradio_logging import oradio_log, DEBUG, CRITICAL
from touch_buttons import TouchButtons, BUTTON_DEBOUNCE_TIME
from oradio_utils import ( input_prompt_int, input_prompt_float,
                           validate_oradio_message
                        )
from oradio_const import (
    BUTTON_NAMES,
    TEST_ENABLED, TEST_DISABLED,
    DEBUGGER_ENABLED, DEBUGGER_NOT_CONNECTED,
    YELLOW, RED, NC,
    MESSAGE_BUTTON_SHORT_PRESS
    )
from remote_debugger import setup_remote_debugging

# pylint: disable=protected-access
# motivation: for test purposes need to test the local methods
def _stop_all_long_press_timer(test_buttons: TouchButtons)-> None:
    """
    :Args
        test_buttons = instance of the class Touchbuttons
    """
    for button_name in BUTTON_NAMES:
        timer = test_buttons.long_press_timers.pop(button_name, None)
        if timer:
            timer.cancel()

def _keyboard_input(event:Event):
    """
    wait for keyboard input with return, and sets the specified event if input detected
    :Args
        event = The specified event will be set upon a keyboard input
    """
    _=input("Press Return on keyboard to stop this test")
    event.set()

#### globals statistics for button callbacks ############
def _handle_message(message: dict, test_buttons: TouchButtons) -> bool:
    """
    the message dict will be validated against the OradioMessage class
    if valid the message received in queue will be processed
    :Args
        message dict must be according OradioMessage class
    :Returns
        True = message is correct and processed
        False = message is not correct
    """
    validated_message = validate_oradio_message(message)
    if validated_message:
        if validated_message.data:
            # do the statistics
            timdat=test_buttons.timing_data
            time_stamp = float(validated_message.data[0])
            # statistics
            button_name = validated_message.state.removeprefix(MESSAGE_BUTTON_SHORT_PRESS)
            if button_name not in BUTTON_NAMES:
                print("invalid button:", button_name, validated_message)
            else:
                test_buttons.timing_data.valid_callbacks[button_name] +=1

            timdat.sum_count +=1
            duration = perf_counter() - time_stamp
            timdat.sum_time +=duration
            timdat.avg_time = timdat.sum_time/timdat.sum_count
            timdat.max_time = max(timdat.max_time,duration)
            timdat.min_time = min(timdat.min_time,duration)
            print ( f"current_time={round(duration,4)},"
                    f"min_time={round(timdat.min_time,4)},"
                    f"max_time={round(timdat.max_time,4)},"
                    f"sum_count={timdat.sum_count},"
                    f"avg_time={round(timdat.avg_time,4)}"
                    )
        else:
            # message without data
            print(f"{YELLOW} Valid message in Queue: {validated_message}{NC}")
    else:
        print(f"{RED}Invalid OradioMessage received {NC}")

def evaluate_test_results(test_buttons:TouchButtons, nr_of_events:int) -> None:
    """
    evaluate the timing test results
    :Args
        nr_of_events = the number of events submitted by gpio callback
        test_buttons = instance of TouchButtons
    """
    timdat = test_buttons.timing_data
    print(f"{YELLOW}==============================================================")
    print (f"min_time={round(timdat.min_time,4)},"
           f"max_time={round(timdat.max_time,4)},"
           f" sum_count={timdat.sum_count},"
           f" avg_time={round(timdat.avg_time,4)}"
        )
    print(f"number of submitted callbacks = {nr_of_events}")
    print(f"Valid callbacks = {timdat.valid_callbacks}")
    print(f"Neglected callbacks = {timdat.neglected_callback}")
    print(f"======================================================================={NC}")

def _check_for_new_message_in_queue(msg_queue: Queue, test_buttons:TouchButtons ):
    """
    Continuously wait, read and handle messages from the shared queue.
    :Args
        msg_queue = queue to check for new messages
        test_buttons = instance of TouchButtons
    """
    while True:
        try:
            msg = msg_queue.get()  # blocking
            print("Received message in Queue: %r", msg)
        except KeyError as ex:
            # A required key like 'source' or 'state' is missing
            print(f"Malformed message (missing key): {ex} | {msg}")
        except (TypeError, AttributeError) as ex:
            # msg wasn't a mapping/dict-like or had wrong types
            print(f"Invalid message format: {ex} | {msg}")
        except (RuntimeError, OSError) as ex:
            # Unexpected runtime/OS errors during handling
            print(f"Runtime error in process_messages: {ex}")
        else:
            _handle_message(msg, test_buttons)

def _callback_test(buttons:TouchButtons):
    """
    Callback test submitted a callback for each of the buttons
    :Args
        buttons = instance of TouchButtons
    """
    button_data = {}
    for button_name in BUTTON_NAMES:
        button_data["state"] = MESSAGE_BUTTON_SHORT_PRESS + button_name
        button_data['name']  = button_name
        buttons._button_event_callback(button_data)
        sleep(1)

def _single_button_play_burst_test(test_buttons:TouchButtons, burst_freq:float) -> None:
    """
    Single_button burst test for PLAY_BUTTON, continues until <Return> button pressed
    When finished all long_press_timers are stopped
    :Args
        test_buttons = instance of TouchButtons
        burst_freq = frequency of submitting event callbacks, shall be >0
    * input requested for burst frequency used in callback simulation
    * resets all timing data
    * set GPIO_MODULE_TEST = TEST_ENABLED
    * set BUTTONS_MODULE_TEST = TEST_ENABLED
    * stop the logging temporary, but setting log-level to CRITICAL
    """
    test_buttons._reset_timing_data()
    test_buttons.button_gpio.set_button_edge_event_callback(test_buttons._button_event_callback)
    test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_ENABLED
    test_buttons.BUTTONS_MODULE_TEST = TEST_ENABLED
    stop_event = Event()
    keyboard_thread = Thread(target=_keyboard_input,
                             args=(stop_event,))
    keyboard_thread.start()
    oradio_log.set_level(CRITICAL)
    status,nr_of_events = test_buttons.button_gpio.simulate_button_play_events_burst(
                                                            burst_freq,
                                                            stop_event)
    if status:
        # module test is enabled
        oradio_log.set_level(DEBUG)
        evaluate_test_results(test_buttons, nr_of_events)
        _stop_all_long_press_timer(test_buttons)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_DISABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_DISABLED
    else:
        print("\nThe module test is not enabled")


def _all_button_burst_test(test_buttons:TouchButtons, burst_freq: float) -> None:
    """
    All_button burst test, continues until <Return> button pressed
    When finished all long_press_timers are stopped        
    :Args
        test_buttons = instance of TouchButtons
        burst_freq = frequency of submitting event callbacks, shall be >0
    * input requested for burst frequency used in callback simulation
    * resets all timing data
    * set GPIO_MODULE_TEST = TEST_ENABLED
    * set BUTTONS_MODULE_TEST = TEST_ENABLED
    * stop the logging temporary, but setting log-level to CRITICAL
    """
    test_buttons._reset_timing_data()
    nr_of_events = 0
    test_buttons.button_gpio.set_button_edge_event_callback(test_buttons._button_event_callback)
    test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_ENABLED
    test_buttons.BUTTONS_MODULE_TEST = TEST_ENABLED
    stop_event = Event()
    keyboard_thread = Thread(target=_keyboard_input,
                             args=(stop_event,))
    keyboard_thread.start()
    oradio_log.set_level(CRITICAL)
    status, nr_of_events = test_buttons.button_gpio.simulate_all_buttons_events_burst(
                                                                        burst_freq,
                                                                        stop_event)
    if status:
        # module test is enabled
        oradio_log.set_level(DEBUG)
        evaluate_test_results(test_buttons, nr_of_events)
        _stop_all_long_press_timer(test_buttons)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_DISABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_DISABLED
    else:
        print("The module test is not enabled")

def _btn_press_release_cb_test(test_buttons:TouchButtons) ->None:
    """
    Button press/release test for BUTTON_STOP, with user specified press-ON time.
    Stops when press-ON timing = 0
    * input requested for button-name and press-timing used in callback simulation
    * resets all timing data
    * stop the logging temporary, but setting log-level to CRITICAL
    When finished all long_press_timers are stopped        
    :Args
        buttons = instance of TouchButtons
    """
    test_buttons.button_gpio.GPIO_MODULE_TEST   = TEST_DISABLED
    test_buttons.BUTTONS_MODULE_TEST            = TEST_DISABLED
    test_buttons._reset_timing_data()
    stop_test = False
    button_name_options = ["Quit"] + BUTTON_NAMES
    selection_done = False
    while not selection_done:
        for idx, button_name in enumerate(button_name_options, start=0):
            print(f" {idx} - {button_name}")
        button_choice = input_prompt_int("Select a Button: ", default=-1)
        match button_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case 1 | 2 | 3 | 4 | 5: # 5 buttons
                selected_button_name = BUTTON_NAMES[button_choice-1]
                selection_done = True
                print(f"\nThe selected BUTTON is {selected_button_name}\n")
            case _:
                print("Please input a valid test option.")
    print("Specify the button-pressed timing in seconds (float), 0 = stop test")
    while not stop_test:
        button_pressed_time = input_prompt_float(
            "Button-press timing (BUTTON_STOP) in seconds (float):", default=0)
        if button_pressed_time == 0:
            stop_test = True
        else:
            test_buttons.button_gpio.simulate_button_press_and_release(
                                                        selected_button_name,
                                                        button_pressed_time)
    _stop_all_long_press_timer(test_buttons)

def _burst_test_button(test_buttons:TouchButtons, test_choice:int):
    """
    Run a burst test for a BUTTON_PLAY or all buttons with a custom frequency
    :Args
        test_buttons: instance used for testing
        test_choice: the requested test number = [3...6]
    """

    if test_choice in (3,5): # Needed for input text
        condition = '>'
    else:
        condition = '<'
    input_text = (f"Specify the event frequency, must be {condition}"
                  f"{int(1000/BUTTON_DEBOUNCE_TIME)} :")
    burst_freq = input_prompt_float( input_text, default=2.0)
    if burst_freq == 0:
        print(f"{YELLOW}invalid frequency{NC}")
    else:
        if test_choice in (3,4):
            _single_button_play_burst_test(test_buttons,burst_freq)
        else:
            _all_button_burst_test(test_buttons,burst_freq)

def _start_module_test():
    """
    Show menu with test options
    """
    # pylint: disable=duplicate-code
    shared_queue = Queue()

    TouchButtons.BUTTONS_MODULE_TEST = TEST_ENABLED
    test_buttons = TouchButtons( shared_queue)

    # Create a thread to listen and process new messages in shared queue
    Thread(target=_check_for_new_message_in_queue,
                    args=(shared_queue,test_buttons),
                    daemon=True).start()
    test_options = ["Quit"] + \
                    ["Pressing a button and check message queue "] + \
                    ["Send for each button a button callback and check message queue"] +\
                    ["BUTTON_PLAY gpio-callback (incl-click) latency timing within debouncing window"] +\
                    ["BUTTON_PLAY gpio-callback (incl-click) latency timing outside debouncing window "] +\
                    ["All buttons gpio-callback (incl-click) latency timing within debouncing window "] +\
                    ["All buttons gpio-callback (incl-click) latency timing outside debouncing window "] +\
                    ["Single button press/release gpio-callback (incl-click) simulation"]
    test_active = True
    while test_active:
        print("\nTEST options:")
        for idx, name in enumerate(test_options, start=0):
            print(f" {idx} - {name}")
        test_choice = input_prompt_int("Select test number: ", default=-1)

        match test_choice:
            case 0:
                print("\nExiting test program\n")
                test_buttons.button_gpio.gpio_cleanup()
                test_active = False
            case 1:
                print(f"\n running {test_options[1]}\n")
                # wait for message received in queue
                _ = input("Press any Return key to stop test")
            case 2:
                print(f"\n running {test_options[2]}\n")
                _callback_test(test_buttons)
                _ = input("Press any Return key to stop test")
            case 3 | 4 | 5 | 6:
                print(f"\n running {test_options[test_choice]}\n")
                _burst_test_button(test_buttons,test_choice)
            case 7:
                print(f"\n running {test_options[7]}\n")
                _btn_press_release_cb_test(test_buttons)
                _ = input("Press any Return key to stop test")
            case _:
                print("Please input a valid number.")

if __name__ == '__main__':
    # try to setup a remote debugger connection, if enabled in remote_debugger.py
    # pylint: disable=duplicate-code
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    _start_module_test()
    sys.exit()
