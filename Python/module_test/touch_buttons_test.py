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
@version:       4
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       
    Module test for toucb_buttons functions
    * Testing BUTTONS touched 
    * Class extensions for button simulations

"""
from threading import Event, Thread
from multiprocessing import Queue
import sys
from time import sleep, monotonic, perf_counter
# Add project root to path (keep this before local imports)
sys.path.append('/home/pi/Oradio3/Python')

##### local oradio import modules ####################
from oradio_logging import oradio_log, DEBUG, CRITICAL
from gpio_service import GPIOService
from touch_buttons import TouchButtons, BUTTON_DEBOUNCE_TIME
from oradio_utils import ( safe_put,
                           input_prompt_int, input_prompt_float,
                           OradioMessage, validate_oradio_message
                        )
from remote_debugger import setup_remote_debugging
from oradio_const import ( BUTTON_NAMES,
                           TEST_ENABLED, TEST_DISABLED,
                           DEBUGGER_ENABLED, DEBUGGER_NOT_CONNECTED,
                           GREEN, YELLOW, RED, NC,
                           MESSAGE_BUTTON_SHORT_PRESS
                         )
# following class is used for testing purposes only
class TimingData:
    '''
    Class for timing data statistics during testing
    '''
    def __init__(self):
        self.min_time = 10000
        self.max_time = 0
        self.sum_time = 0.0
        self.sum_count = 0
        self.avg_time = 0.0
        self.valid_callbacks = {}
        for button in BUTTON_NAMES:
            self.valid_callbacks[button]=0
        self.neglected_callback = {}
        for button in BUTTON_NAMES:
            self.neglected_callback[button]=0

    def reset(self):
        '''
        reseting the timing data
        '''
        self.min_time = 10000
        self.max_time = 0
        self.sum_time = 0.0
        self.sum_count = 0
        self.avg_time = 0.0
        self.valid_callbacks = {}
        self.neglected_callback = {}
        for button in BUTTON_NAMES:
            self.valid_callbacks[button]=0
        self.neglected_callback = {}
        for button in BUTTON_NAMES:
            self.neglected_callback[button]=0

class TestGPIOService(GPIOService):
    """
    Class with additional methods for testing purposes only
    Based on GPIOService baseclass
    :Args
        The new class inherits from GPIOService, and extends it with extra test methods
    """
    def __init__(self):
        super().__init__()

    def simulate_button_play_events_burst(self, burst_freq: int, stop_burst: Event) -> int:
        """ 
        simulate a button press by submitting a callback for BUTTON_PLAY
        :Args
            burst_freq = number of events per second
            stop_burst = an event to stop the burst
        :Returns
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        if self.GPIO_MODULE_TEST == TEST_DISABLED:
            raise RuntimeError("Test is disabled. Enable GPIO_MODULE_TEST to use this method")
        while not stop_burst.is_set():
            self._edge_callback(BUTTONS[BUTTON_PLAY])
            nr_of_events +=1
            sleep(1/burst_freq)
        return nr_of_events

    def simulate_all_buttons_events_burst(self, burst_freq: int, stop_burst: Event) -> int:
        """ 
        simulate all button press by submitting a callback for all buttons in a sequence
        :Args
            burst_freq = nr of events per second
            stop_burst = an event to stop the burst
        :Returns
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        if self.GPIO_MODULE_TEST == TEST_DISABLED:
            raise RuntimeError("Test is disabled. Enable GPIO_MODULE_TEST to use this method")
        while not stop_burst.is_set():
            for button in BUTTON_NAMES:
                self._edge_callback(BUTTONS[button])
                nr_of_events +=1
            sleep(1/burst_freq)
        return nr_of_events

    def simulate_button_press_and_release(self,button_name: str, press_timing : float)-> None:
        """ 
        simulate a BUTTON_STOP button press according specified press timing,
        by submitting a callback for specified button
        :Args
            button_name = name of button [ BUTTON_PLAY | BUTTON_STOP] |
                                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3 ]
            press_timing = press time in float seconds for BUTTON_STOP 
        """
        # set the button pin to an output with GPIO,LOW as a button press
        GPIO.setup(BUTTONS[button_name], GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(BUTTONS[button_name], GPIO.LOW)
        self._edge_callback(BUTTONS[button_name])
        # show a progressing time indicator during press period
        start_time = perf_counter()
        elapsed_time = 0.0
        while elapsed_time < press_timing:
            sleep(0.2)
            print(f"{YELLOW}*", end=" ", flush=True)
            elapsed_time = perf_counter()-start_time
        print(f"{YELLOW}button press timing was {NC} ",press_timing, end=" ", flush=True)
        # set the button pin to GPIO,HIGH as a button release
        GPIO.output(BUTTONS[button_name], GPIO.HIGH)
        self._edge_callback(BUTTONS[button_name])
        # reset the button pin back to an input
        GPIO.setup(BUTTONS[button_name], GPIO.IN, pull_up_down=GPIO.PUD_UP)



if __name__ == "__main__":
    # pylint: disable=protected-access
    ###################################################################################
    # motivation: the method <_button_event_callback> has a local scope, but this method
    # is used within this test module, so is for testing purposes
    ###################################################################################

    # try to setup a remote debugger connection, if enabled
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
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

#### globals statistics for button callbacks ############
    def _handle_message(message: dict, test_buttons: TouchButtons) -> bool:
        '''
        the message dict will be validated against the OradioMessage class
        if valid the message received in queue will be processed
        :arguments
            message dict must be according OradioMessage class
        :return
            True = message is correct and processed
            False = message is not correct
        '''
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
                print ("current_time={cur}, min_time={min}, max_time={max}, sum_count={sum}, avg_time={avg}".format(
                       min=round(timdat.min_time,4), max=round(timdat.max_time,4),
                       sum=timdat.sum_count,avg=round(timdat.avg_time,4),
                       cur=round(duration,4))
                )
            else:
                # message without data
                print(f"{YELLOW} Valid message in Queue: {validated_message}{NC}")
        else:
            print(f"{RED}Invalid OradioMessage received {NC}")

    def evaluate_test_results(test_buttons:TouchButtons, nr_of_events:int) -> None:
        '''
        evaluate the timing test results
        :arguments
            nr_of_events = the number of events submitted by gpio callback
            test_buttons = instance of TouchButtons
        '''
        timdat = test_buttons.timing_data
        print(f"{YELLOW}==============================================================")
        print ("min_time={min}, max_time={max}, sum_count={sum}, avg_time={avg}".format(
               min=round(timdat.min_time,4), max=round(timdat.max_time,4),
               sum=timdat.sum_count, avg=round(timdat.avg_time,4))
               )
        print("number of submitted callbacks = ", nr_of_events)
        print("Valid callbacks = {valid}".format(valid = timdat.valid_callbacks))
        print("Neglected callbacks = {neglet}".format(neglet = timdat.neglected_callback))
        print(f"======================================================================={NC}")

    def _check_for_new_message_in_queue(msg_queue: Queue, test_buttons:TouchButtons ):
        """
        Continuously wait, read and handle messages from the shared queue.
        :arguments
            msg_queue = queue to check for new messages
            test_buttons = instance of TouchButtons
        """
        while True:
            try:
                msg = msg_queue.get()  # blocking
                print("Received message in Queue: %r", msg)
            except KeyError as ex:
                # A required key like 'source' or 'state' is missing
                print("Malformed message (missing key): %s | msg=%r", ex, msg)
            except (TypeError, AttributeError) as ex:
                # msg wasn't a mapping/dict-like or had wrong types
                print("Invalid message format: %s | msg=%r", ex, msg)
            except (RuntimeError, OSError) as ex:
                # Unexpected runtime/OS errors during handling
                print("Runtime error in process_messages: %s", ex)
            else:
                _handle_message(msg, test_buttons)

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

    def _single_button_play_burst_test(test_buttons:TouchButtons, burst_freq:float) -> None:
        '''
        single_button burst test for PLAY_BUTTON, continues until Return button pressed
        :arguments
            test_buttons = instance of TouchButtons
            burst_freq = frequency of submitting event callbacks, shall be >0
        * input requested for burst frequency used in callback simulation
        * resets all timing data
        * set GPIO_MODULE_TEST = TEST_ENABLED
        * set BUTTONS_MODULE_TEST = TEST_ENABLED
        * stop the logging temporary, but setting log-level to CRITICAL
        :POST
            * all long_press_timers are stopped
        '''
        test_buttons._reset_timing_data()
        test_buttons.button_gpio.set_button_edge_event_callback(test_buttons._button_event_callback)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_ENABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_ENABLED
        stop_event = Event()
        keyboard_thread = Thread(target=_keyboard_input,
                                 args=(stop_event,))
        keyboard_thread.start()
        oradio_log.set_level(CRITICAL)
        try:
            nr_of_events = test_buttons.button_gpio.simulate_button_play_events_burst(
                                                                burst_freq,
                                                                stop_event)
        except RuntimeError as err:
            print("\nThe module test is not enabled, error =",err)
        oradio_log.set_level(DEBUG)
        evaluate_test_results(test_buttons, nr_of_events)
        _stop_all_long_press_timer(test_buttons)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_DISABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_DISABLED


    def _all_button_burst_test(test_buttons:TouchButtons, burst_freq: float) -> None:
        '''
        All_button burst test, continues until Return button pressed
        :arguments
            test_buttons = instance of TouchButtons
            burst_freq = frequency of submitting event callbacks, shall be >0
        * input requested for burst frequency used in callback simulation
        * resets all timing data
        * set GPIO_MODULE_TEST = TEST_ENABLED
        * set BUTTONS_MODULE_TEST = TEST_ENABLED
        * stop the logging temporary, but setting log-level to CRITICAL
        :POST
            * all long_press_timers are stopped
        '''
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
        try:
            nr_of_events = test_buttons.button_gpio.simulate_all_buttons_events_burst(
                                                                            burst_freq,
                                                                            stop_event)
        except RuntimeError as err:
            print("\nThe module test is not enabled: ",err)
        oradio_log.set_level(DEBUG)
        evaluate_test_results(test_buttons, nr_of_events)
        _stop_all_long_press_timer(test_buttons)
        test_buttons.button_gpio.GPIO_MODULE_TEST = TEST_DISABLED
        test_buttons.BUTTONS_MODULE_TEST = TEST_DISABLED

    def button_press_release_callback_test(test_buttons:TouchButtons) ->None:
        '''
        Button press/release test for BUTTON_STOP, with user specified press-ON time.
        Stops when press-ON timing = 0
        * input requested for button-name and press-timing used in callback simulation
        * resets all timing data
        * stop the logging temporary, but setting log-level to CRITICAL
        :arguments
            buttons = instance of TouchButtons
        :POST
            * all long_press_timers are stopped
        '''
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
            button_pressed_time = input_prompt_float("Button-press timing (BUTTON_STOP) in seconds (float):", default=0)
            if button_pressed_time == 0:
                stop_test = True
            else:
                test_buttons.button_gpio.simulate_button_press_and_release(
                                                            selected_button_name,
                                                            button_pressed_time)
        _stop_all_long_press_timer(test_buttons)

    def _start_module_test():
        """Show menu with test options"""
        # pylint: disable=too-many-branches
        # motivation:
        # Probably too many, but the code is still readable and not complex
        shared_queue = Queue()

        TouchButtons.BUTTONS_MODULE_TEST = TEST_ENABLED
        test_buttons = TouchButtons( shared_queue)

        # Create a thread to listen and process new messages in shared queue
        Thread(target=_check_for_new_message_in_queue,
                        args=(shared_queue,test_buttons),
                        daemon=True).start()

        # pylint: disable=line-too-long
        ###################################################################################
        # motivation:
        # understand, but is only some text to be printed, no code
        ##################################################################################
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
                        condition = '>'
                    else:
                        print(f"\n running {test_options[4]}\n")
                        condition = '<'
                    input_text = "Specify the event frequency, must {cond} {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME),
                                cond=condition)
                    burst_freq = input_prompt_float( input_text, default=2.0)
                    if burst_freq == 0:
                        print("{yellow}invalid frequency{nc}".format(yellow=YELLOW, nc=NC))
                    else:
                        _single_button_play_burst_test(test_buttons,burst_freq)
                case 5 | 6:
                    if test_choice == 5:
                        print(f"\n running {test_options[3]}\n")
                        condition = '>'
                    else:
                        print(f"\n running {test_options[4]}\n")
                        condition = '<'
                    input_text = "Specify the event frequency, must {cond} {debounce} :".format(
                                debounce= int(1000/BUTTON_DEBOUNCE_TIME),
                                cond=condition)
                    burst_freq = input_prompt_float( input_text, default=2.0)
                    if burst_freq == 0:
                        print("{yellow}invalid frequency{nc}".format(yellow=YELLOW, nc=NC))
                    else:
                        _all_button_burst_test(test_buttons, burst_freq)
                case 7:
                    print(f"\n running {test_options[7]}\n")
                    button_press_release_callback_test(test_buttons)
                    _ = input("Press any Return key to stop test")

                case _:
                    print("Please input a valid number.")

    _start_module_test()

if __name__ == '__main__':
    # try to setup a remote debugger connection, if enabled in remote_debugger.py
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    _start_module_test()
    sys.exit()
