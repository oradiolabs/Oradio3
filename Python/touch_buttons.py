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
from time import sleep, monotonic
##### oradio modules ####################
from play_system_sound import PlaySystemSound
from oradio_logging import oradio_log
from gpio_service import GPIOService
from oradio_utils import safe_put

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500          # ms, ignore rapid repeats
DEBOUNCE_SECONDS = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS           = 10                      # hardware debounce in GPIO.add_event_detect
LONG_PRESS_DURATION = 2  # seconds

##### GLOBAL constants ####################
from oradio_const import (BUTTON_PLAY,BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, BUTTON_NAMES, \
                         BUTTON_PRESSED, \
                         TEST_ENABLED, TEST_DISABLED, \
                         GREEN, YELLOW, RED, NC, \
                         MESSAGE_BUTTON_SOURCE, MESSAGE_NO_ERROR, MESSAGE_SHORT_PRESS, \
                         MESSAGE_LONG_PRESS_BUTTON)


class TouchButtons:
    """
    Handle GPIO-based touch buttons with debounce, short-press callbacks,
    and long-press callbacks. This class has **no knowledge** of the state machine.
    """
    BUTTONS_PERFORMANCE_TEST = TEST_DISABLED

    def __init__(self,queue : Queue):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for LED IO-service
        :arguments
            queue: the shared message queue
        :exceptions
            ValueError : when GPIOService initialization fails
        """
        try:
            self.button_driver = GPIOService()
        except (ValueError) as err:
            oradio_log.error(f"GPIO Initialization failed: {err}")
            raise ValueError("Invalid value provided") from err
        self.button_driver.set_button_edge_event_callback(self._button_event_callback)

        self.sound_player = PlaySystemSound()
        ## Note: PlaySystemSound has no exceptions, so no need to try

        self.message_queue = queue

        self.button_press_times: dict[str, float] = {}   # button -> press start (monotonic)
        self.last_trigger_times: dict[str, float] = {}   # button -> last accepted press time
        self.long_press_timers: dict[str, threading.Timer] = {}  # button -> Timer

    def _send_message(self,button_data: dict) -> None:
        """Send current TouchButton state message to the registered queue."""
        if self.BUTTONS_PERFORMANCE_TEST == TEST_ENABLED:
            message = {
                "source": MESSAGE_BUTTON_SOURCE,
                "state" : button_data["state"],
                "error" : MESSAGE_NO_ERROR,
                "data"  : button_data["timestamp"]
            }
        else:
            message = {
                "source": MESSAGE_BUTTON_SOURCE,
                "state": button_data["state"],
                "error": MESSAGE_NO_ERROR,
            }
        oradio_log.debug("Send TouchButton message: %s", message)
        if not safe_put(self.message_queue, message):
            print("Failure when sending message to shared queue")

    def _button_event_callback(self, button_data: dict) -> None:
        button_data
        '''
        callback for button events
        :arguments
            button_state : [BUTTON_PRESSED | BUTTON_RELEASED
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]

        '''
        button_name = button_data["name"]
        print(f"Button change event: {button_name} = {button_data['state']}")
        now = monotonic()
        last = self.last_trigger_times.get(button_name, 0.0)
        if (now - last) < DEBOUNCE_SECONDS:
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

        # Immediate short-press feedback
        if self.sound_player:
            self.sound_player.play("Click")
            # Note: method does not report error or raises exceptions
        
        ### Send Message to message queue
#        message_state = MESSAGE_SHORT_PRESS+button_name
        self._send_message(button_data)

    def _long_press_timeout(self, button_name: str) -> None:
        """Fire long-press if still held after LONG_PRESS_DURATION.
        If button is in the list of VALID_LONG_PRESS_BUTTONS,
        it is allowed to put message in queue to inform controls
        :arguments
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        """
        
        if not self.button_driver.get_button_state(button_name):
            return  # released during wait; ignore

        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)
        if button_name in VALID_LONG_PRESS_BUTTONS:
            safe_put(self.message_queue, message)

# ------------------ Standalone Test (no state machine) ------------------
if __name__ == "__main__":
    import sys

    from oradio_utils import setup_remote_debugging
    ### Change HOST_ADDRESS to your host computer local address for remote debugging
    HOST_ADDRESS = "192.168.178.52"
    DEBUG_PORT = 5678
    if not setup_remote_debugging(HOST_ADDRESS,DEBUG_PORT):
        print("The remote debugging error, check the remote IP connection")
        sys.exit()

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


    def _prompt_int(prompt: str, default=-1 ) -> int:
        '''
        Prompt for an user input and return int value of number typed
        :argument prompt : prompt text for user
        :argument default: default value to return in case of an error
        :return the integer value type in by user | default value in case of an error
        '''
        try:
            return int(input(prompt))
        except ValueError:
            return default

    min_time = 10000
    max_time = 0
    sum_time = 0.0
    sum_count = 0
    avg_time = 0.0
    def _handle_message(message):
        command_source  = message.get("source")
        state           = message.get("state")
        error           = message.get("error", None)
        if 'data' in message:
            time_stamp = message.get("data")
            global min_time, max_time, sum_count, sum_time, avg_time
            # statistics
            sum_count +=1
            sum_time +=time_stamp
            avg_time = sum_time/sum_count
            if time_stamp > max_time:
                max_time = time_stamp
            if time_stamp < min_time:
                min_time = time_stamp
            print (min_time, max_time, sum_count, avg_time)
        else:
            print(f"{YELLOW} Received message in queue = ",message)
            print(f"{NC}")

    def _check_for_new_message_in_queue(msg_queue):
        """WaitContinuously read and handle messages from the shared queue."""
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
    def _callback_test(buttons_driver:TouchButtons):
        '''
        '''
        for button_name in BUTTON_NAMES:
            buttons_driver._button_event_callback(BUTTON_PRESSED, button_name)
            sleep(1)

    def _callback_for_burst_test(button_data : dict) -> None:
        '''
        '''
        
        print("Received callback data=",button_data)

    
    shared_queue = Queue()

    # Create a thread to listen and process new messages in shared queue
    Thread(target=_check_for_new_message_in_queue, args=(shared_queue,), daemon=True).start()

    def _interactive_menu():
        """Show menu with test options"""
        # pylint: disable=too-many-branches
        try:
            test_buttons = TouchButtons( shared_queue)
        except (ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return

        test_options = ["Quit"] + \
                        ["Pressing a button and check message queue "] + \
                        ["Check button callback and message queue"] +\
                        ["Performance test for button events callback"] +\
                        ["Test 4"] +\
                        ["Test 5"]

        while True:
            print("\nTEST options:")
            for idx, name in enumerate(test_options, start=0):
                print(f" {idx} - {name}")
            test_choice = _prompt_int("Select test number: ", default=-1)
            match test_choice:
                case 0:
                    print("\nExiting test program\n")
                    test_buttons.button_driver.gpio_cleanup()
                    break
                case 1:
                    print(f"\n running {test_options[1]}\n")
                    # wait for message received in queue
                    _ = input("Press any Return key to stop test")
                case 2:
                    print(f"\n running {test_options[2]}\n")
                    _callback_test(test_buttons)
                    _ = input("Press any Return key to stop test")
                case 3:
                    print(f"\n running {test_options[3]}\n")
                    test_buttons.button_driver.set_button_edge_event_callback(test_buttons._button_event_callback)
                    test_buttons.button_driver.GPIO_PERFORMANCE_TEST = TEST_ENABLED
                    test_buttons.BUTTONS_PERFORMANCE_TEST = TEST_ENABLED
                    burst_freq = _prompt_int("Specify the event burst frequency: ", default=-1)
                    stop_event = Event()
                    keyboard_thread = Thread(target=keyboard_input,
                                             args=(stop_event,))
                    keyboard_thread.start()
                    test_buttons.button_driver.simulate_button_events_burst(burst_freq,stop_event)
                    
                case 4:
                    print(f"\n running {test_options[4]}\n")
                case 5:
                    print(f"\n running {test_options[5]}\n")
                case _:
                    print("Please input a valid number.")

    # Present menu with tests
    _interactive_menu()


