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
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio LED control module

"""
import time
from threading import Thread, Event
import sys
import math

##### oradio modules ####################
from led_control import LEDControl
from oradio_utils import input_prompt_int, input_prompt_float


##### GLOBAL constants ####################
from oradio_const import (
    LED_NAMES, GREEN, YELLOW, RED, NC,
    DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED
)

##### oradio modules ####################
from remote_debugger import setup_remote_debugging

##### Local constants ####################
LED_OFF     = "▄" # symbol for led off
LED_ON      = "▀" # symbol for led on
BAR_LENGTH  = 60 # Number of characters for the progress bar

def keyboard_input(event: Event):
    """
    wait for keyboard input with return, and set event if input detected
    Args:
        event = The specified event will be set upon a keyboard input
    post_condition:
        the event is set
    """
    _=input("Press Return on keyboard to stop this test")
    event.set()

def _progress_bar(led_control: LEDControl, led_name: str, duration: int) -> float:
    """
    progress bar
    extended ascii characters see at https://coding.tools/ascii-table
    Args:
        led_name (str) = [ LED_PLAY | LED_STOP] |
                        LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        seconds (int) : duration of progress bar
    Returns:
        led_on_timing (float, 1 decimal)
    """
    start_time          = time.monotonic()
    end_time            = start_time + duration
    progress_bar_state  = "Led ON"
    led_on_timing       = 0
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

LINE_LENGTH     = 90
INTERVAL_TIME   = 0.05
def _show_and_measure_blinking(led_control: LEDControl,
                               led_name: str,
                               cycle_time: float,
                               stop_event: Event ) -> float:
    # pylint: disable=too-many-locals
    ################################################################
    # motivation: for calculation purposes more vars are required
    #################################################################
    """
    display the blinking state of selected led
    extended ascii characters see at https://coding.tools/ascii-table
    Args:
        led_name (str) = [ LED_PLAY | LED_STOP] |
                        LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        led_control : test instance of LEDControl
        cycle_time : the cycle time as float
        stop_event : Event to stop the test
    Returns:
        state_time = the measured ON or OFF period of blink.
    """
    def round_down(num, decimals):
        """
        round down float to nearest value, respecting the float decimals
        Args:
            num = float number
            decimals = number of decimals to use
        ReturnS
            the nearest down value for the float with the specified decimals
        """
        multiplier = 10 ** decimals
        return math.floor(num * multiplier) / multiplier

    line          = [" "] * LINE_LENGTH  # Initialize with spaces
    led_state     = led_control.leds_driver.get_led_state(led_name)
    start_time    = time.monotonic()
    half_time     = round_down((cycle_time/2), 2)
    puls_length   = int(half_time/INTERVAL_TIME)
    mid_puls_position = int(puls_length/2)
    while not stop_event.is_set():
        # Get current LED state
        new_led_state = led_control.leds_driver.get_led_state(led_name)
        now = time.monotonic()
        state_time = 0.0
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
                print(f"{RED}Test Result: The ON cycle timing of {state_time} \
                        for {led_name} is not {half_time} !!")
                break
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
        time.sleep(INTERVAL_TIME)  # Update interval        return led_on_timing
    led_control.turn_off_led(led_name)
    return state_time

def _single_led_test(led_control: LEDControl, test_led_nr: str) -> None:
    """
    Test the selected LED functions
    Args:
        test_led_nr (int) : 0=LED_PLAY, 1=LED_STOP, 
                            2=LED_PRESET1, 3=LED_PRESET2, 4=LED_PRESET3,
                            5=LED_UNKNOWN
        led-driver = instance of LEDControl to use
    """
    # pylint: disable=too-many-branches
    if test_led_nr == 5:
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

        led_test_choice = input_prompt_int("Select test number: ", default=-1)
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
                one_shot = input_prompt_float("Input a one-shot ON period as float number : ")
                print(f"\n{one_shot} sec ONESHOT ON for {selected_led}\n")
                led_control.turn_off_all_leds()
                led_control.oneshot_on_led(selected_led, one_shot)
                led_on_timing = _progress_bar(led_control, selected_led, one_shot+1 )
                if led_on_timing == round(one_shot, 1):
                    print(f"{GREEN}Test:The ONESHOT timing for {selected_led} is OK")
                else:
                    print(f"{RED}Test:The ONESHOT timing for {selected_led} is NOT OK")
            case 4:
                cycle_time = input_prompt_float("Input a cycletime as float number : ")
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
        test_choice = input_prompt_int("Select test number: ", default=-1)
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
                cycle_time = input_prompt_float("Input a cycletime as float number : ")
                for led in LED_NAMES:
                    print(f"\nBlinking LED {led} with cycle-time of {cycle_time} sec\n")
                    led_control.control_blinking_led(led, cycle_time)
                _ = input("Press any key to stop blinking")
                led_control.turn_off_all_leds()
            case 4:
                print(f"\n running {test_options[4]}\n")
                one_shot = input_prompt_float("Input a one-shot ON period as float number : ")
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
                led_choice = input_prompt_int("Select a LED: ", default=-1)
                match led_choice:
                    case 0:
                        print("\nExiting test program\n")
                    case 1 | 2 | 3 | 4 | 5 | 6 :
                        _single_led_test(led_control, (led_choice-1))
            case _:
                print("Please input a valid number.")
    # pylint: enable=too-many-branches

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
