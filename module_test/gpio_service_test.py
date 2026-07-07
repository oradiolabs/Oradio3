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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Module test for GPIO low level functions
    For I/O pins related to buttons and leds
    * Testing LEDs ON/OFF
    * Testing BUTTONS touched
    * Class extensions for button simulations

    Usage: python gpio_service_test.py

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from RPi import GPIO
from time import sleep

##### Oradio modules ######################################
from utilities import input_prompt
from module_test_harness import KeyPressStopWaiter, module_test_session
from gpio_service import GPIOService, LED_ON, LED_OFF, LEDS, BUTTONS, BOUNCE_MS
from messaging import Incidents

##### GLOBAL constants ####################################
from constants import (
    GREEN, YELLOW, RED, NC,
    LED_NAMES, BUTTON_NAMES,
)

##### LOCAL constants #####################################
button_state = {True: f"{YELLOW}1", False: f"{NC}0"}

def button_event_callback(button_data: dict) -> None:
    """
    Callback for button events testing.

    Args:
        button_data (dict): Event information with keys:
            'name'  (str): Name of the button that changed state.
            'state' (str): New state of the button: BUTTON_PRESSED or
                BUTTON_RELEASED.
    """
    print(f"Button change event: {button_data['name']} = {button_data['state']}")

def _button_polling(test_gpio: GPIOService) -> None:
    """
    Poll all button pins at 200 ms intervals and print their states.

    Temporarily removes GPIO edge-detection on all button pins for the
    duration of the poll so that the callback and this loop do not race.
    Edge detection is restored before returning.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
    """
    # Suspend event-driven callbacks while polling.
    for pin in BUTTONS.values():
        GPIO.remove_event_detect(pin)

    waiter = KeyPressStopWaiter()
    waiter.safe_start()

    while not waiter.stopping:
        full_state_text = ""
        active_buttons = []
        for button_name in BUTTON_NAMES:
            state = bool(test_gpio.get_button_state(button_name))
            full_state_text = full_state_text + "," + button_state[state]
            if state:
                active_buttons.append(button_name)
        print(f"{full_state_text} {YELLOW} : {active_buttons}")
        sleep(0.2)

    waiter.safe_stop()

    # Restore event-driven callbacks.
    for pin in BUTTONS.values():
        # pylint: disable=protected-access
        # Direct access to _edge_callback is required here for test purposes.
        GPIO.add_event_detect(
            pin, GPIO.BOTH, callback=test_gpio._edge_callback, bouncetime=BOUNCE_MS
        )
        # pylint: enable=protected-access

def _buttons_testing(test_gpio: GPIOService) -> None:
    """
    Interactive menu for button module tests.

    Offers polling-based and callback-based button tests.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
    """
    button_test_options = ["Quit"] \
                        + ["polling the button state"] \
                        + ["button event-callback handling"]

    test_active = True
    while test_active:
        for idx, name in enumerate(button_test_options, start=0):
            print(f"{NC} {idx} - {name}")

        button_choice = input_prompt("Select test option: ", int, -1)
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
                test_gpio.enable_button_events()
                print("Touch a button and check results. To stop test press RETURN!")
                _ = input("Press RETURN key to stop test and continue to main-menu\n")
                return
            case _:
                print("Please input a valid number.")

def led_selection() -> tuple[int, str]:
    """
    Interactive LED selection menu.

    Returns:
        tuple[int, str]: A (menu_choice, selected_led_name) pair where
            menu_choice is the raw menu index and selected_led_name is the
            corresponding entry from LED_NAMES, or 'LED_UNKNOWN' if the
            unknown option was chosen.
    """
    led_name_option = ["Quit"] + LED_NAMES + ["LedUnknown"]
    selection_done = False
    selected_led_name = "LED_UNKNOWN"
    selected_led_nr = -1

    while not selection_done:
        for idx, led_name in enumerate(led_name_option, start=0):
            print(f" {idx} - {led_name}")
        menu_choice = input_prompt("Select a LED: ", int, -1)

        if menu_choice == 0:
            print("\nReturning to previous selection...\n")
            selection_done = True
        elif 1 <= menu_choice <= len(LED_NAMES):
            selection_done = True
            selected_led_nr = menu_choice - 1
            led_pin = LEDS[LED_NAMES[selected_led_nr]]
            print(f"\nThe selected LED is {led_name_option[menu_choice]} using pin {led_pin}\n")
        elif menu_choice == len(LED_NAMES) + 1:  # "LedUnknown"
            selected_led_nr = -1
            selection_done = True
        else:
            print("Please input a valid test option.")

    if selected_led_nr != -1:
        selected_led_name = LED_NAMES[selected_led_nr]
    return menu_choice, selected_led_name

def _single_led_test(test_gpio: GPIOService) -> None:
    """
    Test set/get functions for an interactively selected LED.

    Turns all LEDs off before presenting the selection menu, then offers
    on/off control with pass/fail feedback for the chosen LED.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
    """
    _all_leds_off(test_gpio)
    menu_choice, selected_led_name = led_selection()
    if menu_choice == 0:
        return

    led_pin_options = ["Quit"] \
                    + ["Turn LED-pin ON"] \
                    + ["Turn LED-pin OFF"]

    test_active = True
    while test_active:
        for idx, name in enumerate(led_pin_options, start=0):
            print(f"{NC} {idx} - {name}")
        led_choice = input_prompt("Select test option: ", int, -1)
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
    Turn off all LEDs without verifying the result.

    Used as a setup step before targeted LED tests.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
    """
    for led_name in LED_NAMES:
        test_gpio.set_led_off(led_name)

def _set_all_leds_test(test_gpio: GPIOService, led_state: bool) -> None:
    """
    Set all LEDs to the given state and report pass/fail.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
        led_state (bool): LED_ON to turn all LEDs on; LED_OFF to turn them off.
    """
    if led_state == LED_ON:
        for led_name in LED_NAMES:
            test_gpio.set_led_on(led_name)
        if all(test_gpio.get_led_state(n) for n in LED_NAMES):
            print(f"{GREEN} Test Result: All the LEDs are ON")
        else:
            print(f"{RED} Test Result: Not all the LEDS are ON !!")
    else:
        for led_name in LED_NAMES:
            test_gpio.set_led_off(led_name)
        if not any(test_gpio.get_led_state(n) for n in LED_NAMES):
            print(f"{GREEN} Test Result: All the LEDs are OFF")
        else:
            print(f"{RED} Test Result: Not all the LEDS are OFF !!")

def _leds_testing(test_gpio: GPIOService) -> None:
    """
    Interactive menu for LED module tests.

    Offers all-on, all-off, and single-LED test options.

    Args:
        test_gpio (GPIOService): Live GPIO service instance under test.
    """
    led_pin_options = ["Quit"] \
                    + ["All leds-pins ON"] \
                    + ["All leds-pins OFF"] \
                    + ["Single LED test"]

    test_active = True
    while test_active:
        print(f"\n{NC}Select a test option:")
        for idx, name in enumerate(led_pin_options, start=0):
            print(f"{NC} {idx} - {name}")

        test_choice = input_prompt("Select test number: ", int, -1)
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
    Top-level test menu.

    Instantiates GPIOService and presents LED and button sub-menus until
    the user chooses to quit, at which point GPIO pins are cleaned up.
    """
    test_gpio = GPIOService()

    print("\nSelect a test or quit:")
    test_active = True
    test_options = ["Quit"] + ["LEDs"] + ["Buttons"]
    while test_active:
        for idx, name in enumerate(test_options, start=0):
            print(f"{NC} {idx} - {name}")
        test_choice = input_prompt("Select one of the test options: ", int, -1)
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
    with module_test_session(Incidents):
        _start_module_test()
