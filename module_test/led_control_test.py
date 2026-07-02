#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Module test for the Oradio LED control module
    Following test capabilities provided:
        * Turn individual or all LEDs ON/OFF
        * One-shot timed LED activation with timing accuracy check
        * Blinking LED with cycle-time accuracy measurement
"""
import sys
import time
import math
from threading import Thread, Event

##### Oradio modules ######################################
from led_control import LEDControl
from utilities import input_prompt
from messaging import Errors, DebugMessageHandler
from remote_debugger import setup_remote_debugging

##### GLOBAL constants ####################################
from constants import (
    LED_NAMES, GREEN, YELLOW, RED, NC,
    DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED
)

##### LOCAL constants #####################################
LED_OFF       = "▄"  # symbol for led off
LED_ON        = "▀"  # symbol for led on
BAR_LENGTH    = 60   # Number of characters for the progress bar
LINE_LENGTH   = 90   # Number of characters for the blink-timeline display
INTERVAL_TIME = 0.02 # Display/poll update interval in seconds

def keyboard_input(event: Event) -> None:
    """
    Wait for the user to press Return and then set the given event.

    Intended to run in a background thread so that a polling loop can
    check the event and exit cleanly without blocking on input().

    Args:
        event (Event): Event that will be set when Return is pressed.
    """
    _ = input("Press Return on keyboard to stop this test")
    event.set()

def _progress_bar(led_control: LEDControl, led_name: str, duration: float) -> float:
    """
    Display a progress bar while an LED is one-shot ON, then OFF.

    Extended ascii characters see at https://coding.tools/ascii-table

    Args:
        led_control (LEDControl): Instance under test.
        led_name (str): One of LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2,
            LED_PRESET3.
        duration (float): Total duration in seconds to display the bar for.

    Returns:
        float: The measured ON duration, rounded to 1 decimal.
    """
    start_time          = time.monotonic()
    end_time             = start_time + duration
    progress_bar_state  = "Led ON"
    led_on_timing       = 0.0
    progress_bar        = ""
    bar_led_off_start   = 0
    while time.monotonic() < end_time:
        elapsed = time.monotonic() - start_time
        progress = elapsed/duration
        filled_length = int(round(BAR_LENGTH * progress))
        if progress_bar_state == "Led ON":
            if led_control.leds_driver.get_led_state(led_name):
                progress_bar = f"{YELLOW}{LED_ON}" * filled_length +\
                                 "-" * (BAR_LENGTH - filled_length)
                bar_led_off_start = filled_length
                led_on_timing = round(elapsed, 1)
            else:
                time.sleep(0.1) # to allow log messages to print before showing progress bar
                progress_bar_state = "Led OFF"
        elif progress_bar_state == "Led OFF":
            # continue with led OFF progress bar
            progress_bar = f"{YELLOW}{LED_ON}" * bar_led_off_start +\
                            f"{NC}{LED_OFF}" * (filled_length-bar_led_off_start) +\
                             "-" * (BAR_LENGTH - filled_length)
        sys.stdout.write(f"\r[{progress_bar}]{YELLOW}LED-ON={led_on_timing} seconds")
        sys.stdout.flush()
        time.sleep(0.05)  # Update interval (shorter for smoother updates)
    print("\n")
    return led_on_timing

def _show_and_measure_blinking(led_control: LEDControl,
                               led_name: str,
                               cycle_time: float,
                               stop_event: Event ) -> float:
    # pylint: disable=too-many-locals
    ################################################################
    # motivation: for calculation purposes more vars are required
    #################################################################
    """
    Display the blinking state of the selected LED and measure its timing.

    Extended ascii characters see at https://coding.tools/ascii-table

    Args:
        led_control (LEDControl): Instance under test.
        led_name (str): One of LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2,
            LED_PRESET3.
        cycle_time (float): The expected full blink cycle time in seconds.
        stop_event (Event): Event used to stop the test.

    Returns:
        float: The last measured ON or OFF half-cycle duration.
    """
    def round_down(num, decimals):
        """
        Round down a float to the nearest value with the given decimals.

        Args:
            num (float): Number to round down.
            decimals (int): Number of decimals to keep.

        Returns:
            float: num rounded down (towards zero/negative infinity) to the
                specified number of decimals.
        """
        multiplier = 10 ** decimals
        return math.floor(num * multiplier) / multiplier

    line          = [" "] * LINE_LENGTH  # Initialize with spaces
    led_state     = led_control.leds_driver.get_led_state(led_name)
    start_time    = time.monotonic()
    half_time     = round_down((cycle_time/2), 2)
    puls_length   = int(half_time/INTERVAL_TIME)
    mid_puls_position = int(puls_length/2)
    # Initialised in case stop_event is already set before the loop runs.
    state_time = 0.0
    while not stop_event.is_set():
        # Get current LED state
        new_led_state = led_control.leds_driver.get_led_state(led_name)
        now = time.monotonic()
        if new_led_state != led_state:
            state_time = round_down((now - start_time), 2)
            led_state  = new_led_state
            start_time = now
            # set the state_time in the line list at mid position of last state
            state_time_list = list(str(state_time))
            new_line = line[:-mid_puls_position] + state_time_list
            new_line = new_line + line[-(mid_puls_position-4):] # 4 chars of state_time
            if len(new_line) == LINE_LENGTH:
                line = new_line
                # else reject line
            # check if timing is within 5% accuracy
            accuracy = 5
            diff = abs(state_time - half_time)
            allowed_deviation = (accuracy/100) * half_time
            if diff > allowed_deviation:
                print(f"\n{RED}Test Result: The ON cycle timing of {state_time} \
                        for {led_name} is not {half_time} !!{NC}\n")
        if new_led_state:
            symbol = LED_ON
        else:
            symbol = LED_OFF
        # Shift the line left and append the new symbol
        line = line[1:] + [symbol]
        if len(line)<LINE_LENGTH:
            print(f"{RED} TEST ERROR ==> stop")
        sys.stdout.write("\r" + "".join(line))
        sys.stdout.flush()
        time.sleep(INTERVAL_TIME)  # Update interval
    led_control.turn_off_led(led_name)
    return state_time

def _single_led_test(led_control: LEDControl, test_led_nr: int) -> None:
    """
    Test the selected LED's set/get/oneshot/blink functions.

    Args:
        led_control (LEDControl): Instance under test.
        test_led_nr (int): Index into LED_NAMES (0=LED_PLAY, 1=LED_STOP,
            2=LED_PRESET1, 3=LED_PRESET2, 4=LED_PRESET3), or
            len(LED_NAMES) to test the unrecognised "LED_UNKNOWN" name.
    """
    # pylint: disable=too-many-branches
    if test_led_nr == len(LED_NAMES):
        # to test for unknown LED_NAMES
        selected_led = "LED_UNKNOWN"
    else:
        selected_led = LED_NAMES[test_led_nr]
    led_test_options = ["Quit"]\
                    + [f"Turn {selected_led} ON"]\
                    + [f"Turn {selected_led} OFF"]\
                    + [f"Testing ONESHOT ON for {selected_led}"]\
                    + [f"Testing LED blinking {selected_led}"]
    while True:
        # --- Show test menu with the selection options---
        for idx, name in enumerate(led_test_options, start=0):
            print(f"{NC} {idx} - {name}")

        led_test_choice = input_prompt("Select test number: ", int, -1)
        match led_test_choice:
            case 0:
                print("\nReturning to main menu selection...\n")
                return
            case 1:
                print(f"\nTurn ON {selected_led}\n")
                led_control.turn_on_led(selected_led)
            case 2:
                print(f"\nTurn OFF {selected_led}\n")
                led_control.turn_off_led(selected_led)
            case 3:
                one_shot = input_prompt("Input a one-shot ON period as float number : ", float, -1)
                print(f"\n{one_shot} sec ONESHOT ON for {selected_led}\n")
                led_control.turn_off_all_leds()
                led_control.oneshot_on_led(selected_led, one_shot)
                led_on_timing = _progress_bar(led_control, selected_led, one_shot+1 )
                if math.isclose(led_on_timing, round(one_shot, 1), abs_tol=0.05):
                    print(f"{GREEN}Test:The ONESHOT timing for {selected_led} is OK")
                else:
                    print(f"{RED}Test:The ONESHOT timing for {selected_led} is NOT OK")
            case 4:
                cycle_time = input_prompt("Input a cycletime as float number : ", float, -1)
                print(f"\nBlinking LED {selected_led} with cycle-time of {cycle_time} sec\n")
                stop_event = Event()
                keyboard_thread = Thread(target=keyboard_input,
                                         args=(stop_event,))
                keyboard_thread.start()
                while not stop_event.is_set():
                    led_control.turn_off_all_leds()
                    led_control.control_blinking_led(selected_led, cycle_time)
                    if selected_led in LED_NAMES:
                        _show_and_measure_blinking(led_control,
                                                   selected_led,
                                                   cycle_time,
                                                   stop_event)
                        led_control.turn_off_led(selected_led) # stop blinking
                    else:
                        # Unrecognised LED name: nothing to blink, so end the
                        # test immediately. keyboard_thread is intentionally
                        # left running (still blocked on input()) and will
                        # exit once the user presses Return.
                        stop_event.set()
            case _:
                print("Please input a valid number.")
    # pylint: enable=too-many-branches

def _start_module_test():
    """Show menu with test options"""
    # pylint: disable=too-many-branches
    # pylint: disable=duplicate-code
    led_control = LEDControl()
    test_options = ["Quit"] + \
                    ["Turn all LEDs OFF"] + \
                    ["Turn all LEDs ON"] +\
                    ["Blink all LEDS"] +\
                    ["OneShot all LEDS"] +\
                    ["Single LED test"]
    while True:
        # --- LED selection ---
        print("\nTEST options:")
        for idx, name in enumerate(test_options, start=0):
            print(f" {idx} - {name}")
        test_choice = input_prompt("Select test number: ", int, -1)
        match test_choice:
            case 0:
                led_control.turn_off_all_leds()
                print("\nExiting test program\n")
                break
            case 1:
                print(f"\n running {test_options[1]}\n")
                led_control.turn_off_all_leds()
            case 2:
                print(f"\n running {test_options[2]}\n")
                led_control.turn_on_all_leds()
            case 3:
                print(f"\n running {test_options[3]}\n")
                led_control.turn_off_all_leds()
                cycle_time = input_prompt("Input a cycletime as float number : ", float, -1)
                for led in LED_NAMES:
                    print(f"\nBlinking LED {led} with cycle-time of {cycle_time} sec\n")
                    led_control.control_blinking_led(led, cycle_time)
                _ = input("Press any key to stop blinking")
                led_control.turn_off_all_leds()
            case 4:
                print(f"\n running {test_options[4]}\n")
                one_shot = input_prompt("Input a one-shot ON period as float number : ", float, -1)
                led_control.turn_off_all_leds()
                for led in LED_NAMES:
                    led_control.oneshot_on_led(led, one_shot)
                    print(f"\n{one_shot} sec ONESHOT ON for {led}\n")
                _ = input("Press any key to stop blinking")
                led_control.turn_off_all_leds()
            case 5:
                print(f"\n running {test_options[5]}\n")
                led_options = ["Quit"] + LED_NAMES + ["LedUnknown"]
                for idx, led_name in enumerate(led_options, start=0):
                    print(f" {idx} - {led_name}")
                led_choice = input_prompt("Select a LED: ", int, -1)
                match led_choice:
                    case 0:
                        print("\nReturning to previous menu...\n")
                    case n if 1 <= n <= len(LED_NAMES) + 1:
                        _single_led_test(led_control, (led_choice-1))
                    case _:
                        print("Please input a valid LED option.")
            case _:
                print("Please input a valid number.")
    # pylint: enable=too-many-branches

if __name__ == '__main__':
    # try to setup a remote debugger connection, if enabled in remote_debugger.py
    # pylint: disable=duplicate-code

    print("\nStarting test program...\n")

    # Subscribe to command and error topics so published messages are printed to console
    err_handler = DebugMessageHandler(Errors.subscribe())

    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    _start_module_test()

    # Stop receiving messages
    Errors.unsubscribe(err_handler.get_queue())
    # Signal the thread to exit and confirm it has exited
    err_handler.stop()

    print("\nExiting test program...\n")

    sys.exit()
