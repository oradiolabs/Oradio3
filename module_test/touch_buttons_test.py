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
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Module test for touch_buttons functions
    * Testing BUTTONS touched
    * Class extensions for button simulations

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from time import sleep, perf_counter
from RPi import GPIO

##### Oradio modules ######################################
from log_service import oradio_log, DEBUG, CRITICAL
from touch_buttons import TouchButtons, BUTTON_DEBOUNCE_TIME
from gpio_service import BUTTONS, GPIOService
from utilities import input_prompt
from module_test_harness import KeyPressStopWaiter, module_test_session
from module_test_metrics import TimingData
from messaging import Commands, Incidents

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    BUTTON_NAMES,
    BUTTON_PLAY,
    BUTTON_PRESSED,
    BUTTON_SHORT_PRESS
)
# pylint: disable=protected-access
# motivation: for test purposes need to test the local methods

class TestTouchButtons:
    """
    Subclass of TouchButtons for testing purposes.

    Composes a real TouchButtons instance with a TestGPIOService, and wires
    the GPIO edge-event callback to TouchButtons' internal handler so that
    button simulations exercise the same code path as production.

    Owns the TimingData instance used for performance/debounce statistics:
    production TouchButtons holds no timing state of its own, so this test
    class is where it lives.
    """
    def __init__(self) -> None:
        # Create an instance of actual TouchButtons
        # and add an instance of TestGPIOService,
        # which creates a composition of TouchButtons, TestGPIOService and TimingData
        self.touch_buttons = TouchButtons()
        self.timing_data = TimingData()
        self.button_gpio = TestGPIOService(self.timing_data)
        # Add performance measurement methods
        # Register callback FIRST
        self.touch_buttons.button_gpio.set_button_edge_event_callback(self.touch_buttons._button_event_callback)
        # THEN enable interrupts
        self.button_gpio.gpio_service.enable_button_events()

class TestGPIOService:
    """
    Class with additional methods for testing purposes only
    Based on GPIOService baseclass
    Args:
        The new class inherits from GPIOService, and extends it with extra test methods:
        * simulate_button_play_events_burst()
        * simulate_all_buttons_events_burst()
        * simulate_button_press_and_release()
    """
    def __init__(self, timing_data: TimingData) -> None:
        """
        create test class, adding a composition of GPIOService class

        Args:
            timing_data (TimingData): Shared stats object (owned by
                TestTouchButtons) that burst simulations record
                valid/neglected presses and latency timing into.
        """
        self.gpio_service = GPIOService()
        self.timing_data = timing_data

    def _simulate_edge_event(self, button_name: str) -> None:
        """
        Directly invoke the registered edge-event callback for the given
        button, without touching real GPIO pin state.

        GPIOService itself has no test/simulation mode: it only ever
        reports the pin state it actually reads. A burst test needs to
        simulate presses far faster than physically toggling a pin would
        allow, so this bypasses GPIOService._edge_callback (and therefore
        the real pin read) entirely and calls the registered callback
        directly -- always reporting BUTTON_PRESSED with a perf_counter
        timestamp attached under "data", for latency measurement.

        touch_buttons.TouchButtons._button_event_callback() returns True if
        the press was accepted, False if it was discarded by the debounce
        window. On acceptance this records the button as a valid callback
        and measures acceptance latency against the timestamp submitted
        above; on rejection it records a neglected press instead.

        Args:
            button_name (str): One of BUTTON_PLAY, BUTTON_STOP,
                BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3.
        """
        if not callable(self.gpio_service.edge_event_callback):
            oradio_log.error("No callback function found")
            return

        submit_time = perf_counter()
        accepted = self.gpio_service.edge_event_callback({
            "state": BUTTON_PRESSED,
            "name": button_name,
            "data": submit_time,
        })

        if accepted is False:
            self.timing_data.neglected_callbacks[button_name] += 1
        else:
            timdat = self.timing_data
            timdat.valid_callbacks[button_name] += 1
            timdat.sum_count += 1
            duration = perf_counter() - submit_time
            timdat.sum_time += duration
            timdat.max_time = max(timdat.max_time, duration)
            timdat.min_time = min(timdat.min_time, duration)

    def simulate_button_play_events_burst(self, burst_freq: float, waiter: KeyPressStopWaiter) -> int:
        """
        Simulate a button press by submitting a callback for BUTTON_PLAY
        Args:
            burst_freq = number of events per second
            waiter = KeyPressStopWaiter whose `stopping` property ends the burst
        Returns:
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        while not waiter.stopping:
            self._simulate_edge_event(BUTTON_PLAY)
            nr_of_events += 1
            sleep(1/burst_freq)
        return nr_of_events

    def simulate_all_buttons_events_burst(self, burst_freq: float, waiter: KeyPressStopWaiter) -> int:
        """
        Simulate all button press by submitting a callback for all buttons in a sequence
        Args:
            burst_freq = nr of events per second
            waiter = KeyPressStopWaiter whose `stopping` property ends the burst
        Returns:
            nr_of_events = the number of event callback submitted
        """
        nr_of_events = 0
        while not waiter.stopping:
            for button in BUTTON_NAMES:
                self._simulate_edge_event(button)
                nr_of_events += 1
            sleep(1/burst_freq)
        return nr_of_events

    def simulate_button_press_and_release(self, button_name: str, press_timing: float) -> None:
        """
        Simulate a button press according to specified press timing,
        by submitting a callback for the specified button.

        Temporarily disables edge detection on the pin while its direction
        is flipped to OUTPUT, since changing pin direction on a pin that
        still has GPIO.add_event_detect armed can raise or misbehave.
        Edge detection is restored along with the pin's original INPUT mode.

        This test toggles the real GPIO pin (unlike the burst simulations
        above), so it goes through the real GPIOService._edge_callback path
        and its real pin read -- no simulated button_data is needed here.

        Args:
            button_name = name of button [ BUTTON_PLAY | BUTTON_STOP] |
                                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3 ]
            press_timing = press time in float seconds
        """
        pin = BUTTONS[button_name]

        # Suspend edge detection before changing pin direction.
        GPIO.remove_event_detect(pin)

        # Set the button pin to an output with GPIO, LOW as a button press
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(pin, GPIO.LOW)
        self.gpio_service._edge_callback(pin)
        # Show a progressing time indicator during press period
        start_time = perf_counter()
        elapsed_time = 0.0
        while elapsed_time < press_timing:
            sleep(0.2)
            print(f"{YELLOW}*", end=" ", flush=True)
            elapsed_time = perf_counter() - start_time
        print(f"{YELLOW}button press timing was {press_timing}{NC} ")
        # Set the button pin to GPIO, HIGH as a button release
        GPIO.output(pin, GPIO.HIGH)
        self.gpio_service._edge_callback(pin)
        # Reset the button pin back to an input
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Restore edge detection to match normal operating state.
        GPIO.add_event_detect(pin, GPIO.BOTH,
                              callback=self.gpio_service._edge_callback,
                              bouncetime=BUTTON_DEBOUNCE_TIME)


def _stop_all_long_press_timer(test_buttons: TestTouchButtons) -> None:
    """
    Args:
        test_buttons = instance of the class TestTouchButtons
    """
    for button_name in BUTTON_NAMES:
        timer = test_buttons.touch_buttons.long_press_timers.pop(button_name, None)
        if timer:
            timer.cancel()

##### globals statistics for button callbacks #############

def evaluate_test_results(test_buttons: TestTouchButtons, nr_of_events: int) -> None:
    """
    evaluate the timing test results
    Args:
        nr_of_events = the number of events submitted by gpio callback
        test_buttons = instance of TestTouchButtons
    """
    timdat = test_buttons.timing_data
    print(f"{YELLOW}==============================================================")
    print (f"min_time={round(timdat.min_time, 4)},"
           f"max_time={round(timdat.max_time, 4)},"
           f" sum_count={timdat.sum_count},"
           f" avg_time={round(timdat.avg_time, 4)}"
        )
    print(f"number of submitted callbacks = {nr_of_events}")
    print(f"Valid callbacks = {timdat.valid_callbacks}")
    print(f"Neglected callbacks = {timdat.neglected_callbacks}")
    print(f"======================================================================={NC}")

def _callback_test(buttons: TestTouchButtons) -> None:
    """
    Callback test that submits a simulated short-press callback for each
    of the buttons in turn. Edge-driven interrupts are already enabled by
    TestTouchButtons.__init__, so they are not re-enabled here.

    Args:
        buttons = instance of TestTouchButtons
    """
    button_data = {}
    for button_name in BUTTON_NAMES:
        button_data["state"] = BUTTON_SHORT_PRESS + button_name
        button_data['name']  = button_name
        buttons.touch_buttons._button_event_callback(button_data)
        sleep(1)

def _single_button_play_burst_test(test_buttons: TestTouchButtons, burst_freq: float) -> None:
    """
    Single_button burst test for PLAY_BUTTON, continues until <Return> button pressed
    When finished all long_press_timers are stopped
    Args:
        test_buttons = instance of TestTouchButtons
        burst_freq = frequency of submitting event callbacks, shall be >0
    * input requested for burst frequency used in callback simulation
    * resets all timing data
    * stop the logging temporary, but setting log-level to CRITICAL
    """
    test_buttons.timing_data._reset_timing_data()
    waiter = KeyPressStopWaiter()
    waiter.safe_start()
    oradio_log.set_level(CRITICAL)
    nr_of_events = test_buttons.button_gpio.simulate_button_play_events_burst(
                                                            burst_freq,
                                                            waiter)
    # module test is enabled
    oradio_log.set_level(DEBUG)
    waiter.safe_stop()
    evaluate_test_results(test_buttons, nr_of_events)
    _stop_all_long_press_timer(test_buttons)

def _all_button_burst_test(test_buttons: TestTouchButtons, burst_freq: float) -> None:
    """
    All_button burst test, continues until <Return> button pressed
    When finished all long_press_timers are stopped
    Args:
        test_buttons = instance of TestTouchButtons
        burst_freq = frequency of submitting event callbacks, shall be >0
    * input requested for burst frequency used in callback simulation
    * resets all timing data
    * stop the logging temporary, but setting log-level to CRITICAL
    """
    test_buttons.timing_data._reset_timing_data()
    waiter = KeyPressStopWaiter()
    waiter.safe_start()
    oradio_log.set_level(CRITICAL)
    nr_of_events = test_buttons.button_gpio.simulate_all_buttons_events_burst(
                                                                        burst_freq,
                                                                        waiter)
    # module test is enabled
    oradio_log.set_level(DEBUG)
    waiter.safe_stop()
    evaluate_test_results(test_buttons, nr_of_events)
    _stop_all_long_press_timer(test_buttons)

def _btn_press_release_cb_test(test_buttons: TestTouchButtons) -> None:
    """
    Button press/release test for a user-selected button, with user
    specified press-ON time. Stops when press-ON timing = 0
    * input requested for button-name and press-timing used in callback simulation
    * resets all timing data
    * stop the logging temporary, but setting log-level to CRITICAL
    When finished all long_press_timers are stopped
    Args:
        buttons = instance of TestTouchButtons
    """
    test_buttons.timing_data._reset_timing_data()
    stop_test = False
    button_name_options = ["Quit"] + BUTTON_NAMES
    selection_done = False
    while not selection_done:
        for idx, button_name in enumerate(button_name_options, start=0):
            print(f" {idx} - {button_name}")
        button_choice = input_prompt("Select a Button: ", int, -1)
        match button_choice:
            case 0:
                print("\nReturning to previous selection...\n")
                selection_done = True
            case n if 1 <= n <= len(BUTTON_NAMES):
                selected_button_name = BUTTON_NAMES[button_choice-1]
                selection_done = True
                print(f"\nThe selected BUTTON is {selected_button_name}\n")
            case _:
                print("Please input a valid test option.")
    print("Specify the button-pressed timing in seconds (float), 0 = stop test")
    while not stop_test:
        button_pressed_time = input_prompt(
            f"Button-press timing ({selected_button_name}) in seconds (float):", float, 0.0)
        if button_pressed_time == 0:
            stop_test = True
        else:
            test_buttons.button_gpio.simulate_button_press_and_release(
                                                        selected_button_name,
                                                        button_pressed_time)
            sleep(0.5)
            stop_test = True
    _stop_all_long_press_timer(test_buttons)

def _burst_test_button(test_buttons: TestTouchButtons, test_choice: int):
    """
    Run a burst test for a BUTTON_PLAY or all buttons with a custom frequency
    Args:
        test_buttons: instance used for testing
        test_choice: the requested test number = [3...6]
    """

    if test_choice in (3, 5): # Needed for input text
        condition = '>'
    else:
        condition = '<'
    input_text = (f"Specify the event frequency, must be {condition}"
                  f"{int(1000/BUTTON_DEBOUNCE_TIME)} :")
    burst_freq = input_prompt(input_text, float, 2.0)
    if burst_freq == 0:
        print(f"{YELLOW}invalid frequency{NC}")
    else:
        if test_choice in (3, 4):
            _single_button_play_burst_test(test_buttons, burst_freq)
        else:
            _all_button_burst_test(test_buttons, burst_freq)

def _start_module_test():
    """
    Show menu with test options
    """
    # pylint: disable=duplicate-code
    test_buttons = TestTouchButtons()

    test_options = ["Quit"] + \
                    ["Pressing a button and check console output "] + \
                    ["Send for each button a button callback and check console output"] +\
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
        test_choice = input_prompt("Select test number: ", int, -1)

        match test_choice:
            case 0:
                print("\nExiting test program\n")
                test_buttons.button_gpio.gpio_service.gpio_cleanup()
                test_active = False
            case 1:
                print(f"\n running {test_options[1]}\n")
                # wait for console output printed by DebugMessageHandler
                _ = input("Press any Return key to stop test")
            case 2:
                print(f"\n running {test_options[2]}\n")
                _callback_test(test_buttons)
                _ = input("Press any Return key to stop test")
            case 3 | 4 | 5 | 6:
                print(f"\n running {test_options[test_choice]}\n")
                _burst_test_button(test_buttons, test_choice)
            case 7:
                print(f"\n running {test_options[7]}\n")
                _btn_press_release_cb_test(test_buttons)
            case _:
                print("Please input a valid number.")
    oradio_log.set_level(DEBUG)

if __name__ == '__main__':
    with module_test_session(Commands, Incidents):
        _start_module_test()
