#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on November 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio GPIO low-level access module
    For I/O pins related to buttons and leds

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""
from time import sleep, perf_counter
from threading import Event
from singleton import singleton
from RPi import GPIO
from typing import Tuple, Optional

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import ( LED_PLAY,LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3, LED_NAMES, \
                         BUTTON_PLAY,BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, \
                         BUTTON_NAMES, BUTTON_PRESSED, BUTTON_RELEASED, \
                         TEST_ENABLED, TEST_DISABLED, \
                         GREEN, YELLOW, RED, NC)

##### Local constants ####################

# LED GPIO PINS
LEDS: dict[str, int] = {
    LED_PLAY:    15,
    LED_PRESET1: 24,
    LED_PRESET2: 25,
    LED_PRESET3:  7,
    LED_STOP:    23
}
# BUTTONS GPIO PINS
BUTTONS: dict[str, int] = {
    BUTTON_PLAY:    9,
    BUTTON_PRESET1: 11,
    BUTTON_PRESET2: 5,
    BUTTON_PRESET3: 10,
    BUTTON_STOP:    6,
}
BOUNCE_MS = 10  # hardware debounce in GPIO.add_event_detect
LED_ON  = True
LED_OFF = False

#REVIEW Onno: Algemeen:
#   - Stel voor de Google Docstrings style te gebruiken, zoals al in veel ander modules.
#   - Je hebt heel veel 1-regelige methods, maar je checkt nergens of de argumenten ok zijn, of de actie succes/fail geeft. Graag even toelichten waarom.
#   - Waarom gebruik je voor docstrings de ene keer ''', de andere keer """ ?
#   - De ene keer gebruik je dubbel-quotes voor variabelen, de andere keer single quotes. Waarom?
#   - Pylint issues fixen
#     Mijn voorstel is om de methods nergens iets terug te laten geven, maar wel een error te loggen.

@singleton
class GPIOService:
    """
    Thread-safe class for GPIO control and status.
    - Set the output pins for the configured LED pins 
    - Reading the inputs for the configured BUTTON pins
    - Callback for button change event
    - Log info/warnings/errors for debugging.
    :Raises
    :Attributes
        GPIO_MODULE_TEST:
            TEST_DISABLED = The module test is disabled (default)
            TEST_ENABLED  = The module test is enabled, additional code is provided
    """
    GPIO_MODULE_TEST = TEST_DISABLED
    def __init__(self) -> None:
        """
        Initialize and setup the GPIO
        """
        self.edge_event_callback = None
        # Fast channel -> name lookup
        self.gpio_to_button = {}
        # The GPIO.BCM refers to a numbering system used in the RPi.GPIO library for Raspberry Pi,
        # The GPIO pins are based on the Broadcom chip's pin numbers, so set to GPIO.BCM
        GPIO.setmode(GPIO.BCM)
        # Initialize the configured LED pins
        for _, pin in LEDS.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            # Note: GPIO.setup can raise a RunTimeError
        oradio_log.debug("LEDControl initialized: All LEDs OFF")
        # Initialize the BUTTON pins
        for button_name, pin in BUTTONS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Note: GPIO.setup can raise a RunTimeError            
            # dictionary for a fast channel -> name lookup
            self.gpio_to_button[pin] = button_name
            # Ensure clean slate; ignore if not previously set
            GPIO.remove_event_detect(pin)
            # The remove_event_detect is a silent function, will not raise error or exception
            # will disable event detection if active
            GPIO.add_event_detect(
                    pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS
                    )
            # Note: GPIO.sadd_event_detect can raise a RunTimeError            
        oradio_log.debug("Buttons initialized")

################## methods for the LED pins ######################
#REVIEW Onno: Voor alle LED en Button methods: Wat is de penalty om een parameter check toe te voegen? iets als if arg in ( ..., ..., ... ) else error
    def set_led_on(self,led_name:str) -> None:
        """
        Turns ON the specified LED.
        :Args 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        """
        if led_name not in LED_NAMES:
            oradio_log.error(f"Unknown led name:{led_name}")
        else:
            GPIO.output(LEDS[led_name], GPIO.LOW)

    def set_led_off(self,led_name:str) -> None:
        """
        Turns OFF the specified LED.
        :Args 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        """
        if led_name not in LED_NAMES:
            oradio_log.error(f"Unknown led name:{led_name}")
        else:
            GPIO.output(LEDS[led_name], GPIO.HIGH)

    def get_led_state(self,led_name:str) -> Tuple[bool, Optional[str]]:
        """
        Get the state off the specified LED.
        :Args 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | 
                                                   LED_PRESET3 ]
        :Returns
            True = LED is ON
            False = LED is OFF
            None = Unknown led_name
        """
        if led_name not in LED_NAMES:
            oradio_log.error(f"Unknown led name:{led_name}")
            led_state = None
        else:
            led_state = not self._read_pin_state(LEDS[led_name])
            # Note led on ==> GPIO.LOW,
        return led_state

######### methods for BUTTON pins ########################
    def set_button_edge_event_callback(self,callback) -> None:
        """
        Set the callback for a change (edge_event) on a button state
        The callback will process the change event
        :Args
            callback (Callable): the reference to the callback function, upon an button event
        :Returns
        """
        if callable(callback):
            self.edge_event_callback = callback
        else:
            oradio_log.error("Callback function does not exists")

    def gpio_cleanup(self) -> None:
        """
        Reset the GPIO pins to their default state.
        It resets any ports which have been used and puts the port in default state
        The default state is input-mode.
        Mainly used in test environments, to get pins in the default state 
        """
        GPIO.cleanup()

    def _edge_callback(self, channel: int)->None:
        """
        Unified handler for both press (falling) and release (rising) edges.
        One callback as button handling is the same for rising as for falling edge.
        Only difference is the state of the button. To prevent duplicated-code
        Called by gpio event detection.
        When channel has a known button_name, the configured callback is called
        :Args 
            channel (int) is the I/O-pin which detected an edge event
        :Attributes
            GPIO_MODULE_TEST
                TEST_ENABLED :
                    * extra timestamp data added to callback
                                for performance measurements
                    * state = BUTTON_PRESSED
                TEST_DISABLED = Default mode, no extra data for testing
        :Returns
            False (default): when channel refers to an unknown pin/button_name
            True : The button_name of the pin is found and callback is called 
        """
        if self.GPIO_MODULE_TEST == TEST_ENABLED:
            button_event_ts = perf_counter() # timestamp the start of this function
        button_data = {}
        button_name = self.gpio_to_button[channel]
        if not button_name:
            return
        button_value = GPIO.input(channel)
        if button_value == GPIO.LOW:
            state = BUTTON_PRESSED
        else:
            state = BUTTON_RELEASED
        button_data["state"] = state
        button_data["name"]  = button_name
        if self.edge_event_callback:
            if self.GPIO_MODULE_TEST == TEST_ENABLED:
                # When TEST_ENABLED, the test requires the button_data to be BUTTON_PRESSED
                button_data["state"] = BUTTON_PRESSED
                button_data["data"] = button_event_ts
            self.edge_event_callback(button_data)
        else:
            oradio_log.error("no callback function found")

    def _read_pin_state(self, io_pin: int) -> bool:
        """
        read the state of the specified io-pin
        :Args
            io_pin: int = which pin to read
        :Returns
            True = pin is HIGH
            False = pin is LOW
        """
        return(bool(GPIO.input(io_pin)))

    def get_button_state(self,button_name:str) -> Tuple[bool, Optional[str]]:
        """
        Get the state off the specified button.
        :Args 
            button_name (str) precondition: must be [ BUTTON_PLAY | BUTTON_STOP] |
                                                   BUTTON_PRESET1 | BUTTON_PRESET2 | 
                                                   BUTTON_PRESET3 ]
        :Returns
            True = BUTTON is ON (so pressed/touched)
            False = BUTTON is OFF (so not pressed/touched)
        """
        if button_name not in BUTTON_NAMES:
            oradio_log.error(f"Unknown button name:{button_name}")
            button_state = None
        else:
            state = not self._read_pin_state(BUTTONS[button_name])
            # Note: a pressed button has value GPIO.LOW
        return state

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

###################################################################################################

# Entry point for stand-alone operation
if __name__ == '__main__':
#################################################################
#    module test for gpio_service
    from oradio_utils import input_prompt_int
    from threading import Thread
    import sys

    button_state = {True: f"{YELLOW}1", False: f"{NC}0"}

    from remote_debugger import setup_remote_debugging, DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED

    # try to setup a remote debugger connection, if enabled
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
            sys.exit()

    def keyboard_input(event:Event):
        """
        wait for keyboard input with return, and set event if input detected
        :Args
            event = The specified event will be set upon a keyboard input
        :post_condition:
            the event is set
        """
        _=input("Press Return on keyboard to stop this test")
        event.set()

    def _buttons_testing(test_gpio) -> None:
####################################################################
# pylint: disable=too-many-branches
# motivation: indeed, but the code is not too complex to understand
###################################################################

        """
        module tests for the BUTTONS
        :Args
            test_gpio : instance of the GPIO class under test
        :Returns
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
                    # stop the event driven callback
                    for button_name, pin in BUTTONS.items():
                        GPIO.remove_event_detect(pin)
                    stop_event = Event()
                    keyboard_thread = Thread(target=keyboard_input,
                                             args=(stop_event,))
                    keyboard_thread.start()
                    while not stop_event.is_set():
                        full_state_text = ""
                        active_buttons = []
                        for button_name in BUTTON_NAMES:
                            state = test_gpio.get_button_state(button_name)
                            state_text = button_state[state]
                            full_state_text = full_state_text + "," + state_text
                            if state:
                                active_buttons.append(button_name)
                        print(full_state_text + YELLOW + " : {}".format(active_buttons))
                        sleep(0.2)
                    del stop_event
                    # activate the callback events
                    for button_name, pin in BUTTONS.items():
                        # pylint: disable=protected-access
                        #########################################################################
                        # motivation: the method has a local scope, but this method
                        # is used within the test module, so required for testing purposes
                        ###########################################################################
                        GPIO.add_event_detect(
                            pin, GPIO.BOTH, callback=test_gpio._edge_callback, bouncetime=BOUNCE_MS
                        )
                case 2:
                    print(f"\n running {button_test_options[2]}\n")
                    test_gpio.set_button_edge_event_callback(_button_event_callback)
                    print("Touch a button and check results. To stop test press RETURN!")
                    while True:
                        _ = input("Press RETURN key to stop test and continue to main-menu\n")
                        break
                case _:
                    print("Please input a valid number.")

        test_gpio.set_button_edge_event_callback(_button_event_callback)
        print("Touch a button and check results. To stop test press RETURN!")
        while True:
            _ = input("Press RETURN key to stop test and continue to main-menu\n")
            break
        return

    def _button_event_callback(button_data: dict) -> None:
        """
        callback for button events testing
        :Args
            button_data = { 'name': str,   # name of button
                            'state': str,  # state of button Pressed/Released
                            'error' : str  # error 
                            }
                            if TEST_ENABLED a data key is added
                            {
                            'data': float
                            }
        """
        print(f"Button change event: {button_data['name']} = {button_data['state']}")

    def _single_led_test(test_gpio:GPIOService) ->None:
        """
        Test the selected LED functions
        :Args 
            test_gpio : instance of gpio service to be use
        """
        # pylint: disable=too-many-branches
        #####################################################
        # motivation:
        # probably caused by match-case
        # code is still readable and not complex
        ###################################################
        _all_leds_off(test_gpio)
        led_name_option = ["Quit"] + LED_NAMES
        selection_done = False
        while not selection_done:
            for idx, led_name in enumerate(led_name_option, start=0):
                print(f" {idx} - {led_name}")
            led_choice = input_prompt_int("Select a LED: ", default=-1)
            match led_choice:
                case 0:
                    print("\nReturning to previous selection...\n")
                    selection_done = True
                case 1 | 2 | 3 | 4 | 5: # 5 leds
                    selected_led_name = LED_NAMES[led_choice-1]
                    selection_done = True
                    print(f"\nThe selected LED is {selected_led_name} using pin {LEDS[led_name]}\n")
                case _:
                    print("Please input a valid test option.")
        if led_choice == 0: #Quit
            return

        led_pin_options = ["Quit"]\
                        + ["Turn LED-pin ON"]\
                        + ["Turn LED-pin OFF"]

        test_active = True
        while test_active:
            # --- Show test menu with the selection options---
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
        :Args
            test_gpio should be an instance of GPIOService
        """
        for led_name in LED_NAMES:
            test_gpio.set_led_off(led_name)

    def _leds_testing(test_gpio: GPIOService) -> None:
        """
        module tests for the LEDS
        :Args
            test_gpio should be an instance of GPIOService
        """
        # pylint: disable=too-many-branches
        ####################################################################
        # motivation:
        # match-case is more readable, but causes extra branches.
        # only 1 level if-else within a case, so still readable
        #######################################################################
        # create a led-pin selection list
        led_pin_options =   ["Quit"] +\
                            ["All leds-pins ON"] +\
                            ["All leds-pins OFF"] +\
                            ["Single LED test"]
        test_active = True
        while test_active:
            # --- LED selection ---
            print(f"\n{NC}Select a test option:")
            for idx, name in enumerate(led_pin_options, start=0):
                print(f"{NC} {idx} - {name}")

            test_choice = input_prompt_int("Select test number: ", default=-1)
            match test_choice:
                case 0:
                    print("\nExiting led testing\n")
                    test_active = False
                case 1:
                    print(f"\n running {led_pin_options[1]}\n")
                    all_pins_state = LED_ON # the LED pins ON state
                    for led_name in LED_NAMES:
                        test_gpio.set_led_on(led_name)
                        all_pins_state = all_pins_state and test_gpio.get_led_state(led_name)
                    if all_pins_state is LED_ON:
                        print(f"{GREEN} Test Result: All the LEDs are ON")
                    else:
                        print(f"{RED} Test Result: Not all the LEDS are ON !!")
                case 2:
                    print(f"\n running {led_pin_options[2]}\n")
                    all_pins_state = LED_OFF
                    for led_name in LED_NAMES:
                        test_gpio.set_led_off(led_name)
                        all_pins_state = all_pins_state or test_gpio.get_led_state(led_name)
                    if all_pins_state is LED_OFF:
                        print(f"{GREEN} Test Result: All the LEDs are OFF")
                    else:
                        print(f"{RED} Test Result: Not all the LEDS are OFF !!")
                case 3:
                    print(f"\n running {led_pin_options[3]}\n")
                    _single_led_test(test_gpio)
                case _:
                    print("Please input a valid test option.")

    def _interactive_menu() -> None:
        """
        Show menu with test options
        """
        try:
            test_gpio = GPIOService()
        except (RuntimeError, ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return

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
#REVIEW Onno: afsluited NC color reset ontbreekt
                    print(f"{YELLOW}Please input a valid number.")

        sys.exit()
    # Present menu with tests
    _interactive_menu()
