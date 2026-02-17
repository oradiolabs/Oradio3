#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Januari 22, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Module test for GPIO low level functions
    For I/O pins related to buttons and leds
    * Testing LEDs ON/OFF
    * Testing BUTTONS touched 
    * Class extensions for button simulations

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from threading import Thread, Event
from time import sleep
import sys
from typing import Tuple
from RPi import GPIO

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, YELLOW, RED, NC,
    LED_NAMES, BUTTON_NAMES,
    DEBUGGER_ENABLED, DEBUGGER_NOT_CONNECTED,
)
##### local oradio import modules ####################
from oradio_utils import input_prompt_int
from gpio_service import GPIOService, LED_ON, LED_OFF, LEDS, BUTTONS, BOUNCE_MS
from remote_debugger import setup_remote_debugging

##### Local constants ####################
button_state = {True: f"{YELLOW}1", False: f"{NC}0"}

def button_event_callback(button_data: dict) -> None:
    """
    Callback for button events testing
    Args:
        button_data = { 'name': str,   # name of button
                        'state': str,  # state of button Pressed/Released
                        'error' : str  # error 
                        }
    Attributes:
        GPIO_MODULE_TEST: (TEST_ENABLED  = The module test is enabled)
        if TEST_ENABLED a data key is added
        {
            'data': float
        }
    """
    print(f"Button change event: {button_data['name']} = {button_data['state']}")

def _keyboard_input(event: Event):
    """
    Wait for keyboard input with return, and set event if input detected
    Args:
        event = The specified event will be set upon a keyboard input
    post_condition:
        the event is set
    """
    _= input("Press Return on keyboard to stop this test")
    event.set()

def _button_polling(test_gpio: GPIOService) -> None:
    """
    Polling of the buttons pins and report the state of the pins
    """
    # stop the event driven callback
    for button_name, pin in BUTTONS.items():
        GPIO.remove_event_detect(pin)
    stop_event = Event()
    keyboard_thread = Thread(target=_keyboard_input,
                             args=(stop_event,))
    keyboard_thread.start()
    while not stop_event.is_set():
        full_state_text = ""
        active_buttons = []
        for button_name in BUTTON_NAMES:
            state = test_gpio.get_button_state(button_name)
            full_state_text = full_state_text + "," + button_state[state]
            if state:
                active_buttons.append(button_name)
        print(f"{full_state_text} {YELLOW} : {active_buttons}")
        sleep(0.2)
    del stop_event
    # activate the callback events again
    for button_name, pin in BUTTONS.items():
        # pylint: disable=protected-access
        # required for buttons testing purposes
        GPIO.add_event_detect(
            pin, GPIO.BOTH, callback=test_gpio._edge_callback, bouncetime=BOUNCE_MS
        )
        # pylint: enable=protected-access

def _buttons_testing(test_gpio: GPIOService) -> None:
    """
    module tests for the BUTTONS
    Args:
        test_gpio : instance of the GPIO class under test
    Returns:
        True : OK
        False: Error condition
    """
    button_test_options = ["Quit"]\
                    + ["polling the button state"]\
                    + ["button event-callback handling"]

    test_active = True
    while test_active:
        # --- Show test menu with the selection options---
        for idx, name in enumerate(button_test_options, start=0):
            print(f"{NC} {idx} - {name}")

        button_choice = input_prompt_int("Select test option: ", default=-1)
        match button_choice:
            case 0:
                print("\nReturning to main menu selection...\n")
                return
            case 1:
                print(f"\n running {button_test_options[1]}\n")
                _button_polling(test_gpio)
            case 2:
                print(f"\n running {button_test_options[2]}\n")
                test_gpio.set_button_edge_event_callback(button_event_callback)
                print("Touch a button and check results. To stop test press RETURN!")
                while True:
                    _ = input("Press RETURN key to stop test and continue to main-menu\n")
                    break
            case _:
                print("Please input a valid number.")

    test_gpio.set_button_edge_event_callback(button_event_callback)
    print("Touch a button and check results. To stop test press RETURN!")
    while True:
        _ = input("Press RETURN key to stop test and continue to main-menu\n")
        break
    return

def led_selection() -> Tuple[int, str]:
    """
    Led selection menu
    Returns:
        selected_led_name [str] : the name of led as in LED_NAMES
        menu_choice [int]: the number of the selection
    """
    # prepare a option list`
    led_name_option = ["Quit"] + LED_NAMES + ["LedUnknown"]
    selection_done = False
    selected_led_name = "LED_UNKNOWN"
    while not selection_done:
        #Show test menu with the selection options
        for idx, led_name in enumerate(led_name_option, start=0):
            print(f" {idx} - {led_name}")
        menu_choice = input_prompt_int("Select a LED: ", default=-1)
        match menu_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case 1 | 2 | 3 | 4 | 5 | 6: # 5 leds + 1 unknown
                selection_done = True
                selected_led_nr = menu_choice-1
                if menu_choice == 6:
                    led_pin = -1
                else:
                    led_pin = LEDS[LED_NAMES[selected_led_nr]]
                print(f"\nThe selected LED is {led_name_option[menu_choice]} using pin {led_pin}\n")
            case _:
                print("Please input a valid test option.")
    if selected_led_nr != 6:
        selected_led_name = LED_NAMES[selected_led_nr]
    return menu_choice, selected_led_name

def _single_led_test(test_gpio: GPIOService) -> None:
    """
    Test the selected LED functions
    Args:
        test_gpio : instance of gpio service to be use
    """
    _all_leds_off(test_gpio)
    menu_choice, selected_led_name = led_selection()
    if menu_choice == 0:
        #quit selected
        return

    # prepare an option list
    led_pin_options = ["Quit"]\
                    + ["Turn LED-pin ON"]\
                    + ["Turn LED-pin OFF"]
    test_active = True
    while test_active:
        #Show test menu with the selection options
        for idx, name in enumerate(led_pin_options, start=0):
            print(f"{NC} {idx} - {name}")
        led_choice = input_prompt_int("Select test option: ", default=-1)
        match led_choice:
            case 0:
                print("\nReturning to main menu selection...\n")
                return
            case 1:
                print(f"\n running {led_pin_options[1]}\n")
                test_gpio.set_led_on(selected_led_name)
                pin_state = test_gpio.get_led_state(selected_led_name)
                if pin_state is LED_ON:
                    print(f"{GREEN}Test Result: The selected LED {selected_led_name} is ON")
                else:
                    print(f"{RED}Test Result: The selected LED {selected_led_name} is NOT ON")
            case 2:
                print(f"\n running {led_pin_options[2]}\n")
                test_gpio.set_led_off(selected_led_name)
                pin_state = test_gpio.get_led_state(selected_led_name)
                if pin_state is LED_OFF:
                    print(f"{GREEN}Test Result: The selected LED {selected_led_name} is OFF")
                else:
                    print(f"{RED}Test Result: The selected LED {selected_led_name} is NOT OFF")
            case _:
                print("Please input a valid number.")

def _all_leds_off(test_gpio: GPIOService) -> None:
    """
    Switch off all LEDs
    Args:
        test_gpio:  should be an instance of GPIOService
    """
    for led_name in LED_NAMES:
        test_gpio.set_led_off(led_name)

def _set_all_leds_test(test_gpio: GPIOService, led_state: bool) -> None:
    """
    Set all leds ON or OFF using GPIO services
    Args:
        test_gpio instance
        led_state : LED_ON | LED_OFF
    """
    if led_state == LED_ON:
        all_pins_state = LED_ON # the LED pins ON state
        for led_name in LED_NAMES:
            test_gpio.set_led_on(led_name)
            all_pins_state = all_pins_state and test_gpio.get_led_state(led_name)
        if all_pins_state is LED_ON:
            print(f"{GREEN} Test Result: All the LEDs are ON")
        else:
            print(f"{RED} Test Result: Not all the LEDS are ON !!")
    else:
        all_pins_state = LED_OFF
        for led_name in LED_NAMES:
            test_gpio.set_led_off(led_name)
            all_pins_state = all_pins_state or test_gpio.get_led_state(led_name)
        if all_pins_state is LED_OFF:
            print(f"{GREEN} Test Result: All the LEDs are OFF")
        else:
            print(f"{RED} Test Result: Not all the LEDS are OFF !!")

def _leds_testing(test_gpio: GPIOService) -> None:
    """
    module tests for the LEDS
    Args:
        test_gpio should be an instance of GPIOService
    """
    # create a led-pin selection list
    led_pin_options =   ["Quit"] +\
                        ["All leds-pins ON"] +\
                        ["All leds-pins OFF"] +\
                        ["Single LED test"]
    test_active = True
    while test_active:
        print(f"\n{NC}Select a test option:")
        #Show test menu with the selection options
        for idx, name in enumerate(led_pin_options, start=0):
            print(f"{NC} {idx} - {name}")

        test_choice = input_prompt_int("Select test number: ", default=-1)
        match test_choice:
            case 0:
                print("\nExiting led testing\n")
                test_active = False
            case 1:
                print(f"\n running {led_pin_options[1]}\n")
                _set_all_leds_test(test_gpio, LED_ON)
            case 2:
                print(f"\n running {led_pin_options[2]}\n")
                _set_all_leds_test(test_gpio, LED_OFF)
            case 3:
                print(f"\n running {led_pin_options[3]}\n")
                _single_led_test(test_gpio)
            case _:
                print("Please input a valid test option.")

def _start_module_test() -> None:
    """
    Show menu with test options
    """
    test_gpio = GPIOService()

    print("\nSelect a test or quit:")
    test_active = True
    test_options = ["QUIT"] + ["LEDS"] + ["BUTTONS"]
    while test_active:
        for idx, name in enumerate(test_options, start=0):
            print(f"{NC} {idx} - {name}")
        test_choice = input_prompt_int("Select one of the test options: ", default=-1)
        match test_choice:
            case 0:
                print("\nExiting test program\n")
                test_gpio.gpio_cleanup()
                test_active = False
            case 1:
                print(f"\n running {test_options[1]}\n")
                _leds_testing(test_gpio)
            case 2:
                print(f"\n running {test_options[2]}\n")
                _buttons_testing(test_gpio)
            case _:
                print(f"{YELLOW}Please input a valid number.{NC}")

if __name__ == '__main__':
    # try to setup a remote debugger connection, if enabled in remote_debugger.py
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    _start_module_test()
    sys.exit()
