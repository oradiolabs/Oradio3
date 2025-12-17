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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio LED control module

"""
import time
import threading

##### oradio modules ####################
from oradio_logging import oradio_log
from gpio_service import GPIOService

##### GLOBAL constants ####################
from oradio_const import (LED_NAMES, GREEN, YELLOW, RED, NC)

class LEDControl:
    """Control LED states"""

    def __init__(self):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for LED IO-service
        """
        try:
            self.leds_driver = GPIOService()
        except (ValueError) as err:
            oradio_log.error(f"GPIO Initialization failed: {err}")
            raise ValueErrorError("Invalid value provided")
        else:
            self.blink_stop_events = {}       # map led_name → threading.Event()
            self.blinking_threads = {}        # map led_name → Thread
            oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name:str) -> bool:
        """
        Turns off a specified LED and waits for its blink‐thread to exit.
        :arguments
            led_name (str), precondition: must be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
            log (bool) = enable/disable logging of led status
        :return
            True: specified LED is turned off
            False: Invalid LED name 
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Invalid LED name: %s", led_name)
            return False

        # signal any blink thread to stop
        running_stop_event = self.blink_stop_events.pop(led_name, None)
        if running_stop_event:
            running_stop_event.set()

        # block until thread really finishes
        active_thread = self.blinking_threads.pop(led_name, None)
        if active_thread:
            active_thread.join()

        self.leds_driver.set_led_off(led_name)
        oradio_log.debug("%s turned off", led_name)
        return True

    def turn_on_led(self, led_name:str):
        """
        Turns ON a specified LED and stops blink‐thread if active.
        :arguments
            led_name (str), precondition: must be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
        :return
            True: specified LED is turned off
            False: Invalid LED name 
        """
        if led_name not in LED_NAMES:
            oradio_log.error("Invalid LED name: %s", led_name)
            return False

        # stop blinking silently (no 'turned off' log), then light it
        self.turn_off_led(led_name)
        # Turn led ON
        self.leds_driver.set_led_on(led_name)
        oradio_log.debug("%s turned on", led_name)
        return True

    def turn_off_all_leds(self) ->None:
        """Stops all blink‐threads and turns every LED off."""
        for led_name in LED_NAMES:
            self.turn_off_led(led_name)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_all_leds(self):
        """Stops all blink‐threads and turns every LED on."""
        for led_name in LED_NAMES:
            self.turn_on_led(led_name)
        oradio_log.debug("All LEDs turned ON and blinking stopped")

    def oneshot_on_led(self, led_name, period: float=3) ->bool:
        """
        Turns on a specific LED and then turns it off after a delay.

        :arguments:
            led_name (str), precondition: must be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
            period (float): Time in seconds before turning off the LED.Default = 3
        :return
            True: Oneshot running for specified LED 
            False: Invalid LED name 
        """
        def oneshot_led_off(led_name, period):
            self.turn_off_led(led_name)
            oradio_log.debug("%s turned off after %s seconds", led_name, period)

        if period <= 0:
            # no valid period, no timer started
            oradio_log.warning("Invalid period time of %f for oneshot of led: %s",period, led_name)
            return False
        else:
            period = round(period,1) # more accuracy not visible, so not required
        if led_name not in LED_NAMES:
            oradio_log.error("Invalid LED name: %s", led_name)
            return False

        # Stop any blinking for this LED and turn it on
        self.turn_on_led(led_name)
        oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, period)
        oneshot_timer = threading.Timer(period,oneshot_led_off, args=(led_name,period))
        oneshot_timer.start()
        return True


    def control_blinking_led(self, led_name: str, cycle_time:float = None) -> bool:
        """
        Blink using an Event for blink timing and instant stop, 
        :arguments
            led_name (str), precondition: must be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3]
            cycle_time (float) = 
            _________|^^^^^^^^^^^|____________|^^^^^^^^^^^^|____________|^^
                     |<====== cycle_time ====>| 
                     |<== half =>|
        :return
            True = Blinking started and running
            False = Failure, no blinking
        """

        def _blink():
            half = cycle_time / 2
            while not stop_evt.is_set():
                self.leds_driver.set_led_on(led_name)
                if stop_evt.wait(half):
                    break
                self.leds_driver.set_led_off(led_name)
                if stop_evt.wait(half):
                    break
            self.leds_driver.set_led_off(led_name)

        if led_name not in LED_NAMES:
            oradio_log.error("Invalid LED name: %s", led_name)
            return False

        # stop and remove any existing blink for selected led
        running_stop_evt = self.blink_stop_events.pop(led_name, None)
        if running_stop_evt:
            running_stop_evt.set()
        active_blinking_thread = self.blinking_threads.pop(led_name, None)
        if active_blinking_thread:
            active_blinking_thread.join()

        # if no cycle_time, just turn off selected LED
        if not cycle_time:
            self.turn_off_led(led_name)
            oradio_log.debug("%s blinking stopped and turned off", led_name)
            return False

        # start new blink thread
        stop_evt = threading.Event()
        self.blink_stop_events[led_name] = stop_evt
        thread = threading.Thread(target=_blink, daemon=True)
        thread.start()
        self.blinking_threads[led_name] = thread
        oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)
        return True

# Entry point for stand-alone operation
if __name__ == "__main__":
    import sys
    import math

    print("\nStarting LED Control Module Test...\n")

    from oradio_utils import setup_remote_debugging
    ### Change HOST_ADDRESS to your host computer local address for remote debugging
    HOST_ADDRESS = "192.168.178.52"
    DEBUG_PORT = 5678
    if not setup_remote_debugging(HOST_ADDRESS,DEBUG_PORT):
        print("The remote debugging error, check the remote IP connection")
        exit()

    def _prompt_int(prompt: str, default: int | None = None) -> int | None:
        try:
            return int(input(prompt))
        except ValueError:
            return default

    def _prompt_float(prompt: str, default: float | None = None) -> float | None:
        try:
            return float(input(prompt))
        except ValueError:
            return default

    def _progress_bar_measure_on_time(led_control:LEDControl, led_name:str, duration:int)-> float:
        '''
        progress bar
        extended ascii characters see at https://coding.tools/ascii-table
        :arguments
            led_name (str) = [ LED_PLAY | LED_STOP] |
                            LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
            seconds (int) : duration of progress bar
        :return led_on_timing (float, 1 decimal)
        '''
        start_time = time.time()
        end_time   = start_time + duration
        bar_length = 60  # Number of characters for the progress bar
        LED_OFF = "▄"
        LED_ON = "▀"
        progress_bar_state = "Led ON"
        while time.time() < end_time:
            elapsed = time.time() - start_time
            progress = elapsed/duration
            filled_length = int(round(bar_length * progress))
            if progress_bar_state == "Led ON":
                if led_control.leds_driver.get_led_state(led_name):
                    bar = f"{YELLOW}{LED_ON}" * filled_length + "-" * (bar_length - filled_length)
                    bar_led_off_start = filled_length
                    led_on_timing = round(elapsed,1)
                else:
                    time.sleep(0.1) # to allow log messages to print before showing progress bar
                    progress_bar_state = "Led OFF"
            elif progress_bar_state == "Led OFF":
                # continue with led OFF progress bar
                bar = f"{YELLOW}{LED_ON}" * bar_led_off_start +\
                f"{NC}{LED_OFF}" * (filled_length-bar_led_off_start) +\
                 "-" * (bar_length - filled_length)
            progress_time = int(round(progress))
            sys.stdout.write(f"\r[{bar}]{YELLOW}LED-ON={led_on_timing} seconds")
            sys.stdout.flush()
            time.sleep(0.05)  # Update interval (shorter for smoother updates)
        print("\n")
        return led_on_timing

    def _show_and_measure_blinking(led_control:LEDControl,
                                   led_name:str,
                                   cycle_time: float )-> float:
        '''
        display the blinking state of selected led
        extended ascii characters see at https://coding.tools/ascii-table
        :arguments
            led_name (str) = [ LED_PLAY | LED_STOP] |
                            LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        :return 
            state_time = the measured ON or OFF period of blink.
        '''

        def round_down(num, decimals):
            '''
            round down float to nearest value, respecting the float decimals
            :argument
                num = float number
                decimals = number of decimals to use
            :return
                the nearest down value for the float with the specified decimals
            '''
            multiplier = 10 ** decimals
            return math.floor(num * multiplier) / multiplier

        line_length   = 90
        line          = [" "] * line_length  # Initialize with spaces
        led_symbols   = {True: "▄", False: "▀"}  # Symbols for on/off
        led_state     = led_control.leds_driver.get_led_state(led_name)
        start_time    = time.time()
        half_time     = round_down((cycle_time/2),2)
        interval_time = 0.05
        puls_length   = int(half_time/interval_time)
        mid_puls_position = int(puls_length/2)
        try:
            while True:
                # Get current LED state
                new_led_state = led_control.leds_driver.get_led_state(led_name)
                now = time.time()
                if new_led_state != led_state:
                    state_time = round_down((now - start_time),2)
                    led_state  = new_led_state
                    start_time = now
                    # set the state_time in the line list at mid position of last state
                    state_time_list = list(str(state_time))
                    new_line = line[:-mid_puls_position] + state_time_list
                    new_line = new_line + line[-(mid_puls_position-4):] # 4 chars of state_time
                    line = new_line
                    # check if timing is within 5% accuracy
                    ACCURACY = 5
                    diff = state_time - half_time
                    allowed_deviation = (ACCURACY/100) * half_time
                    if diff > allowed_deviation:
                        print(f"{RED}Test Result: The ON cycle timing of {state_time}\
                         for {led_name} is not {half_time} !!")
                        break
                symbol = led_symbols[new_led_state]
                # Shift the line left and append the new symbol
                line = line[1:] + [symbol]
                if len(line)<line_length:
                    print(f"{RED} TEST ERROR ==> stop")
                    break
                sys.stdout.write("\r" + "".join(line))
                sys.stdout.flush()
                time.sleep(interval_time)  # Update interval        return led_on_timing
        except KeyboardInterrupt:
            led_control.turn_off_led(led_name)
            pass
        return state_time

    def _single_led_test(led_control:LEDControl,selected_led:str) ->None:
        '''
        Test the selected LED functions
        :arguments 
            selected_led (str) = [ LED_PLAY | LED_STOP] |
                                  LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
            led-driver = instance of LEDControl to use
        '''
        led_test_options = ["Quit"]\
                        + [f"Turn {selected_led} ON"]\
                        + [f"Turn {selected_led} OFF"]\
                        + [f"Testing ONESHOT ON for {selected_led}"]\
                        + [f"Testing LED blinking {selected_led}"]
        while True:
            # --- Show test menu with the selection options---
            for idx, name in enumerate(led_test_options, start=0):
                print(f"{NC} {idx} - {name}")

            led_test_choice = _prompt_int("Select test number: ", default=-1)
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
                    one_shot = _prompt_float("Input a one-shot ON period as float number : ")
                    print(f"\n{one_shot} sec ONESHOT ON for {selected_led}\n")
                    if led_control.oneshot_on_led(selected_led,one_shot):
                        led_on_timing = _progress_bar_measure_on_time(led_control,
                                                                      selected_led,
                                                                      one_shot+1 )
                        if led_on_timing == round(one_shot,1):
                            print(f"{GREEN}Test Result: The ONESHOT timing for {selected_led} is OK")
                        else:
                            print(f"{RED}Test Result: The ONESHOT timing for {selected_led} is NOT ON !!")
                case 4:
                    cycle_time = _prompt_float("Input a cycletime as float number : ")
                    print(f"\nBlinking LED {selected_led} with cycle-time of {cycle_time} sec\n")
                    print("Press CTRL-C to stop this test\n")
                    if led_control.control_blinking_led(selected_led, cycle_time):
                        _show_and_measure_blinking(led_control,selected_led, cycle_time)
                        led_control.turn_off_led(selected_led) # stop blinking
                    else:
                        print(f"{RED}Test Result: The blinking failed for {selected_led}")
                case _:
                    print("Please input a valid number.")


    def _interactive_menu():
        """Show menu with test options"""
        try:
            leds = LEDControl()
        except (ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return

        test_options = ["Quit"] + \
                        LED_NAMES + \
                        ["Turn all LEDs OFF"] + \
                        ["Turn all LEDs ON"]

        while True:
            # --- LED selection ---
            print("\nSelect a TEST:")
            for idx, name in enumerate(test_options, start=0):
                print(f" {idx} - {name}")

            test_choice = _prompt_int("Select TEST number: ", default=-1)

            if test_choice == 0:
                leds.turn_off_all_leds()
                print("\nExiting test program\n")
                break

            if not 0 <= test_choice < len(test_options):
                print("Please input a valid test number.")
                continue

            selected_test = test_options[test_choice]

            if selected_test == "Turn all LEDs OFF":
                print("\nExecuting: Turn all LEDs OFF\n")
                leds.turn_off_all_leds()
                continue
            elif selected_test == "Turn all LEDs ON":
                print("\nExecuting: Turn all LEDs ON\n")
                leds.turn_on_all_leds()
                continue
            elif selected_test in LED_NAMES:
                _single_led_test(leds, selected_test)
    # Present menu with tests
    _interactive_menu()
