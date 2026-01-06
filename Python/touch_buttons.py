#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on April 28, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       4
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio touch buttons module with debounce, per-button callbacks, and selftest
"""

from threading import Timer, Thread, Event
from multiprocessing import Queue
from time import sleep, monotonic, perf_counter
import json
from pydantic import ValidationError
##### oradio modules ####################
from play_system_sound import PlaySystemSound
from oradio_logging import oradio_log
from gpio_service import GPIOService
from oradio_utils import (safe_put, 
                          input_prompt_int, input_prompt_float, 
                          Oradio_message, validate_oradio_message)
from system_sounds import play_sound

##### GLOBAL constants ####################
from oradio_const import (BUTTON_PLAY,BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, BUTTON_NAMES, \
                         BUTTON_PRESSED, BUTTON_RELEASED, \
                         TEST_ENABLED, TEST_DISABLED, \
                         GREEN, YELLOW, RED, NC, \
                         MESSAGE_BUTTON_SOURCE, MESSAGE_NO_ERROR, MESSAGE_BUTTON_SHORT_PRESS, MESSAGE_BUTTON_LONG_PRESS, \
                         MESSAGE_LONG_PRESS_BUTTON_PLAY, \
                         SOUND_CLICK, \
                         MODEL_NAME_FOUND)

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500          # ms, ignore rapid repeats
DEBOUNCE_SECONDS = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS           = 10                      # hardware debounce in GPIO.add_event_detect
LONG_PRESS_DURATION = 6  # seconds
VALID_LONG_PRESS_BUTTONS = [BUTTON_PLAY]
BUTTON_LONG_PRESSED = "button long pressed"

class TouchButtons:
    """
    Handle GPIO-based touch buttons with debounce, short-press callbacks,
    and long-press callbacks. This class has **no knowledge** of the state machine.
    :Conditionals
        BUTTONS_MODULE_TEST:
            TEST_DISABLED = The module test is disabled (default)
            TEST_ENABLED  = The module test is enabled, additional code is provided

    """
    BUTTONS_MODULE_TEST = TEST_DISABLED

    def __init__(self,queue : Queue):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for button IO-service
        :arguments
            queue: the shared message queue
        :exceptions
            ValueError : when GPIOService initialization fails
        """
        try:
            self.button_gpio = GPIOService()
        except (ValueError) as err:
            oradio_log.error(f"GPIO Initialization failed: {err}")
            raise ValueError("Invalid value provided") from err
        self.button_gpio.set_button_edge_event_callback(self._button_event_callback)
        self.message_queue = queue

        self.button_press_times: dict[str, float] = {}   # button -> press start (monotonic)
        self.last_trigger_times: dict[str, float] = {}   # button -> last accepted press time
        self.long_press_timers: dict[str, threading.Timer] = {}  # button -> Timer

    def _send_message(self,button_data: dict) -> None:
        """Send current TouchButton state message to the registered queue.
        :arguments
            button_data = { 'name': str,   # name of button
                            'state': str,  # state of button Pressed/Released
                           }
            if TEST_ENABLED a data key is added
            {
              'data': float # timestamp
            }
            state = [BUTTON_PRESSED | BUTTON_RELEASED | BUTTON_LONG_PRESSED
            name : [BUTTON_PLAY | BUTTON_STOP |
                    BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        """
        message = {}
        message["source"] = MESSAGE_BUTTON_SOURCE
        if button_date["state"] == BUTTON_LONG_PRESSED:
            state = MESSAGE_BUTTON_LONG_PRESS+button_data["name"]
        else:
            state = MESSAGE_BUTTON_SHORT_PRESS+button_data["name"]
        if self.BUTTONS_MODULE_TEST == TEST_ENABLED:
            message["state"]  = state
            message["error"]  = button_data["error"]
            data_list = []
            data_list.append(button_data["data"])
            message["data"] = data_list

        else:
            message["state"]  = state
            message["error"]  = button_data["error"]
        # validate and create the message
        oradio_msg = Oradio_message(**message).model_dump_json()
        oradio_log.debug("Send TouchButton message: %s", oradio_msg)
        if not safe_put(self.message_queue, oradio_msg):
            print("Failure when sending message to shared queue")

    def _button_event_callback(self, button_data: dict) -> None:
        '''
        callback for button events
        :arguments
            button_data = { 'name': str,   # name of button
                            'state': str,  # state of button Pressed/Released
                           }
            if TEST_ENABLED a data key is added
            {
              'data': float # timestamp
            }
            state = [BUTTON_PRESSED | BUTTON_RELEASED
            name : [BUTTON_PLAY | BUTTON_STOP |
                    BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        '''
        button_name = button_data["name"]
        oradio_log.debug(f"Button change event: {button_name} = {button_data['state']}")
        if button_data["state"] == BUTTON_RELEASED:
            # cancel pending long-press timer (if any)
            timer = self.long_press_timers.pop(button_name, None)
            if timer:
                timer.cancel()
            return
        # a button press detected
        now = monotonic()
        last = self.last_trigger_times.get(button_name, 0.0)
        time_diff = now-last
        if (time_diff) < DEBOUNCE_SECONDS:
            # another button press detected within the debounce period
            # is considered to be a new button press.
            # The button press was to short, so will be neglected
            if self.BUTTONS_MODULE_TEST == TEST_ENABLED:
                print_text = "{yellow}New {name} event in {diff} sec".format(
                    yellow=YELLOW, name=button_name, diff=round(time_diff,3))
                print_text +="print but within the debounce window of {debounce} will be neglected {nc}".format(
                        debounce =DEBOUNCE_SECONDS, nc=NC )
                print(print_text)
                global neglected_callback
                neglected_callback[button_name] +=1 
            return  # software debounce

        self.last_trigger_times[button_name] = now
        self.button_press_times[button_name] = now

        # Cancel any existing timer, then arm a fresh one
        prev = self.long_press_timers.pop(button_name, None)
        if prev:
            prev.cancel()

        timer = Timer(LONG_PRESS_DURATION,
                        self._long_press_timeout,
                        args=(button_name,))
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()
        play_sound(SOUND_CLICK)
        self._send_message(button_data)

    def _long_press_timeout(self, button_name: str) -> None:
        """Fire long-press if still held after LONG_PRESS_DURATION.
        If button is in the list of VALID_LONG_PRESS_BUTTONS,
        it is allowed to put message in queue to inform controls
        :arguments
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        """

        if not self.button_gpio.get_button_state(button_name):
            return  # released during wait; ignore

        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)
        if button_name in VALID_LONG_PRESS_BUTTONS:
            button_data["name"]  = button_name
            button_data["state"] = BUTTON_LONG_PRESSED
            self._send_message(button_data)

#########################  Test Module ##################################
if __name__ == "__main__":
#################################################################
#    module test
#    Note:
#    in case remote python debugging is required:
#    * run the Python Debug Server in your IDE
#    * call module test with argument -rd [no | yes]
#        if yes add in -ip your host ip address and -p the portnr
#        >python touch_buttons.py -rd yes -ip 102.168.xxx.xxx -p 5678
#################################################################

    import sys

    from oradio_utils import setup_remote_debugging
    from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL

    if not setup_remote_debugging():
        print(f"{YELLOW}The remote debugging error, check the remote IP connection {NC}")
        sys.exit()

    def _stop_all_long_press_timer(test_buttons: TouchButtons)-> None:
        '''
        :arguments
            test_buttons = instance of the class Touchbuttons
        '''
        for button_name in BUTTON_NAMES:
            timer = test_buttons.long_press_timers.pop(button_name, None)
            if timer:
                timer.cancel()

    def _keyboard_input(event:Event):
        '''
        wait for keyboard input with return, and set event if input detected
        :arguments
            event = The specified event will be set upon a keyboard input
        :post_condition:
            the event is set
        '''
        _=input("Press Return on keyboard to stop this test")
        event.set()

#### statistics for button callbacks ############
    min_time = 10000
    max_time = 0
    sum_time = 0.0
    sum_count = 0
    avg_time = 0.0
    valid_callbacks = {}
    neglected_callback = {}

    def _reset_timing_data() -> None:
        '''
        reset all (global) timing data
        '''
        global min_time, max_time, sum_count, sum_time, avg_time, valid_callbacks, neglected_callback
        min_time = 10000
        max_time = 0
        sum_time = 0.0
        sum_count = 0
        avg_time = 0.0
        for button in BUTTON_NAMES:
            valid_callbacks[button]= 0
            neglected_callback[button] = 0

    def _handle_message(message: dict) -> bool:
        '''
        the message dict will be validated against the Oradio_message class
        if valid the message received in queue will be processed
        :arguments
            message dict must be according Oradio_message class
        :return
            True = message is correct and processed
            False = message is not correct
        '''
        status = True
        validated_message = validate_oradio_message(message)
        if validated_message:
            if validated_message.data:
                # do the statistics
                time_stamp = float(validated_message.data[0])
                global min_time, max_time, sum_count, sum_time, avg_time, valid_callbacks
                # statistics
                button_name = validated_message.state.removeprefix(MESSAGE_BUTTON_SHORT_PRESS)
                if button_name not in BUTTON_NAMES:
                    print("invalid button:", button_name, validated_message)
                else:
                    valid_callbacks[button_name] +=1

                sum_count +=1
                duration = perf_counter() - time_stamp
                sum_time +=duration
                avg_time = sum_time/sum_count
                if duration > max_time:
                    max_time = duration
                if duration < min_time:
                    min_time = duration
                print ("current_time={cur}, min_time={min}, max_time={max}, sum_count={sum}, avg_time={avg}".format(
                       min=round(min_time,4), max=round(max_time,4), sum=sum_count, avg=round(avg_time,4),
                       cur=round(duration,4))
                )
        else:
            print(f"{RED}Invalid Oradio_message received {NC}")

    def evaluate_test_results(nr_of_events:int) -> None:
        '''
        evaluate the timing test results
        :arguments
            nr_of_events = the number of events submitted by gpio callback
        '''
        global min_time, max_time, sum_count, sum_time, avg_time, valid_callbacks, neglected_callback
        print ("min_time={min}, max_time={max}, sum_count={sum}, avg_time={avg}".format(
               min=round(min_time,4), max=round(max_time,4), sum=sum_count, avg=round(avg_time,4))
               )
        print("number of submitted callbacks = ", nr_of_events)
        print("Valid callbacks = {valid}".format(valid = valid_callbacks))
        print("Neglected callbacks = {neglet}".format(neglet = neglected_callback))

    def _check_for_new_message_in_queue(msg_queue):
        """
        Continuously wait, read and handle messages from the shared queue.
        :arguments
            msg_queue = queue to check for new messages
        """
        while True:
            try:
                msg = msg_queue.get()  # blocking
                oradio_log.debug("Received message in Queue: %r", msg)
            except KeyError as ex:
                # A required key like 'source' or 'state' is missing
                oradio_log.error("Malformed message (missing key): %s | msg=%r", ex, msg)
            except (TypeError, AttributeError) as ex:
                # msg wasn't a mapping/dict-like or had wrong types
                oradio_log.error("Invalid message format: %s | msg=%r", ex, msg)
            except (RuntimeError, OSError) as ex:
                # Unexpected runtime/OS errors during handling
                oradio_log.exception("Runtime error in process_messages: %s", ex)
            else:
                _handle_message(msg)

    def _callback_test(buttons:TouchButtons):
        '''
        Callback test submitted a callback for each of the buttons
        :arguments
            buttons = instance of TouchButtons
        '''
        button_data = {}
        for button_name in BUTTON_NAMES:
            button_data["state"] = MESSAGE_BUTTON_SHORT_PRESS + button_name
            button_data['name']  = button_name
            buttons._button_event_callback(button_data)
            sleep(1)

    def _single_button_play_burst_test() -> None:
        '''
        single_button burst test, continues until Return button pressed
        * input requested for burst frequency used in callback simulation
        * resets all timing data
        * set GPIO_MODULE_TEST = TEST_ENABLED
        * set BUTTONS_MODULE_TEST = TEST_ENABLED
        * stop the logging temporary, but setting log-level to CRITICAL
        :POST
            * all long_press_timers are stopped
        '''
        _reset_timing_data()
        test_buttons.button_gpio.set_button_edge_event_callback(test_buttons._button_event_callback)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_ENABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_ENABLED
        burst_freq = input_prompt_float( input_text, default=2.0)
        if burst_freq == 0:
            print("{yellow}invalid frequency{nc}".format(yellow=YELLOW, nc=NC))
        else:
            stop_event = Event()
            keyboard_thread = Thread(target=_keyboard_input,
                                     args=(stop_event,))
            keyboard_thread.start()
            oradio_log.set_level(CRITICAL)
            try:
                nr_of_events = test_buttons.button_gpio.simulate_button_play_events_burst(burst_freq,stop_event)
            except RuntimeError as err:
                print("\nThe module test is not enabled, enable module test in code")
            oradio_log.set_level(DEBUG)
        evaluate_test_results(nr_of_events)
        _stop_all_long_press_timer(test_buttons)

    def _all_button_burst_test() -> None:
        '''
        All_button burst test, continues until Return button pressed
        * input requested for burst frequency used in callback simulation
        * resets all timing data
        * set GPIO_MODULE_TEST = TEST_ENABLED
        * set BUTTONS_MODULE_TEST = TEST_ENABLED
        * stop the logging temporary, but setting log-level to CRITICAL
        :POST
            * all long_press_timers are stopped
        '''
        _reset_timing_data()
        test_buttons.button_gpio.set_button_edge_event_callback(test_buttons._button_event_callback)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_ENABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_ENABLED
        burst_freq = input_prompt_float( input_text, default=2.0)
        if burst_freq == 0:
            print("{yellow}invalid frequency{nc}".format(yellow=YELLOW, nc=NC))
        else:
            stop_event = Event()
            keyboard_thread = Thread(target=_keyboard_input,
                                     args=(stop_event,))
            keyboard_thread.start()
            oradio_log.set_level(CRITICAL)
            try:
                nr_of_events = test_buttons.button_gpio.simulate_all_buttons_events_burst(burst_freq,stop_event)
            except RuntimeError as err:
                print("\nThe module test is not enabled, enable module test in code")
            oradio_log.set_level(DEBUG)
        _stop_all_long_press_timer(test_buttons)
        evaluate_test_results(nr_of_events)

    def button_press_release_callback_test(test_buttons) ->None:
        '''
        Button press/release test for BUTTON_STOP, with user specified press-ON time. 
        Stops when press-ON timing = 0
        * input requested for button-name and press-timing used in callback simulation
        * resets all timing data
        * stop the logging temporary, but setting log-level to CRITICAL
        :POST
            * all long_press_timers are stopped
        '''
        _reset_timing_data()
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
            button_pressed_time = input_prompt_float("Button-press timing (BUTTON_STOP) in seconds (float):", default=0)
            if button_pressed_time == 0:
                stop_test = True
            else:
                test_buttons.button_gpio.simulate_button_press_and_release(selected_button_name, button_pressed_time)
        _stop_all_long_press_timer(test_buttons)
        
    def _interactive_menu():
        """Show menu with test options"""
        # pylint: disable=too-many-branches

        shared_queue = Queue()
        # Create a thread to listen and process new messages in shared queue
        Thread(target=_check_for_new_message_in_queue, args=(shared_queue,), daemon=True).start()

        try:
            test_buttons = TouchButtons( shared_queue)
        except (ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return
#        status = callable(gpio_srv.simulate_button_events_burst)
        test_options = ["Quit"] + \
                        ["Pressing a button and check message queue "] + \
                        ["Send for each button a button callback and check message queue"] +\
                        ["BUTTON_PLAY gpio-callback (incl-click) latency timing within debouncing window"] +\
                        ["BUTTON_PLAY gpio-callback (incl-click) latency timing outside debouncing window "] +\
                        ["All buttons gpio-callback (incl-click) latency timing within debouncing window "] +\
                        ["All buttons gpio-callback (incl-click) latency timing outside debouncing window "] +\
                        ["Single button press/release gpio-callback (incl-click) simulation"]
        while True:
            print("\nTEST options:")
            for idx, name in enumerate(test_options, start=0):
                print(f" {idx} - {name}")
            test_choice = input_prompt_int("Select test number: ", default=-1)
            match test_choice:
                case 0:
                    print("\nExiting test program\n")
                    test_buttons.button_gpio.gpio_cleanup()
                    break
                case 1:
                    print(f"\n running {test_options[1]}\n")
                    # wait for message received in queue
                    _ = input("Press any Return key to stop test")
                case 2:
                    print(f"\n running {test_options[2]}\n")
                    _callback_test(test_buttons)
                    _ = input("Press any Return key to stop test")
                case 3 | 4:
                    if test_choice == 3:
                        print(f"\n running {test_options[3]}\n")
                        input_text = "Specify the event frequency, must > {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME))
                    else:
                        print(f"\n running {test_options[4]}\n")
                        input_text = "Specify the event frequency, must be <= {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME))
                    _single_button_play_burst_test()
                case 5 | 6:
                    if test_choice == 5:
                        print(f"\n running {test_options[5]}\n")
                        input_text = "Specify the event frequency, must > {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME))
                    else:
                        print(f"\n running {test_options[6]}\n")
                        input_text = "Specify the event frequency, must be <= {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME))
                        _all_button_burst_test()
                case 7:
                    print(f"\n running {test_options[7]}\n")
                    button_press_release_callback_test(test_buttons)
                    _ = input("Press any Return key to stop test")

                case _:
                    print("Please input a valid number.")

    # Present menu with tests
    _interactive_menu()


