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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio GPIO low-level access module
    For I/O pins related to buttons and leds

@references:
    https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio
"""




##### oradio modules ####################
from oradio_const import ( LED_PLAY,LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3, LED_NAMES, \
                         BUTTON_PLAY,BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, \
                         GREEN, YELLOW, RED, NC)

from oradio_logging import oradio_log
from singleton import singleton
try:
    import RPi.GPIO as GPIO
except RuntimeError:
    oradio_log.error("Error importing RPi.GPIO. Check privileges!)")

##### GLOBAL constants ####################

##### Local constants ####################
################## LED GPIO PINS ##########################
LEDS: dict[str, int] = {
    LED_PLAY:    15,
    LED_PRESET1: 24,
    LED_PRESET2: 25,
    LED_PRESET3:  7,
    LED_STOP:    23
}
################## BUTTONS GPIO PINS ##########################
BUTTONS: dict[str, int] = {
    BUTTON_PLAY:    9,
    BUTTON_PRESET1: 11,
    BUTTON_PRESET2: 5,
    BUTTON_PRESET3: 10,
    BUTTON_STOP:    6,
}
BOUNCE_MS = 10  # hardware debounce in GPIO.add_event_detect
BUTTON_PRESSED = "button pressed"
BUTTON_RELEASED = "button released"

@singleton
class GPIOService:
    """
    Thread-safe class for GPIO control and status.
    - Set the output pins for the configured LED pins 
    - Reading the inputs for the configured BUTTON pins
    - Callback for button change event
    - Logs errors for debugging.
    """
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
        # Initialize the defined LED pins
        for _, pin in LEDS.items():
            try:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            except RuntimeError as err:
                oradio_log.debug("Error setting LED output: %s for pin %s",err,pin)
                raise ValueError("Invalid value provided") from err

        oradio_log.debug("LEDControl initialized: All LEDs OFF")
        # Initialize the BUTTON pins
        for button_name, pin in BUTTONS.items():
            try:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            except RuntimeError as err:
                oradio_log.debug("Error setting BUTTON input: %s for pin %s", err,pin)
                raise ValueError("Invalid value provided") from err
            # dictionary for a fast channel -> name lookup
            self.gpio_to_button[pin] = button_name
            # Ensure clean slate; ignore if not previously set
            GPIO.remove_event_detect(pin)
            # The remove_event_detect is a silent function, will not raise error or exception
            # will disable event detection if active
            try:
                GPIO.add_event_detect(
                    pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS
                )
            except RuntimeError as err:
                oradio_log.debug("Error setting up event detection: %s for pin %s",err,pin)
                raise ValueError("Invalid value provided") from err

################## methods for the LED pins ######################
    def set_led_on(self,led_name:str) -> None:
        '''
        Turns ON the specified LED.
        :arguments 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        '''
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_log.debug("Led %s is turned ON", led_name)
        return True

    def set_led_off(self,led_name:str) -> None:
        """
        Turns OFF the specified LED.
        :arguments 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        """
        GPIO.output(LEDS[led_name], GPIO.HIGH)
        oradio_log.debug("Led %s is turned OFF", led_name)
        return

    def get_led_state(self,led_name:str) -> bool:
        """
        Get the state off the specified LED.
        :arguments 
            led_name (str) precondition: must be [ LED_PLAY | LED_STOP] |
                                                   LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
        :return
            True = LED is ON
            False = LED is OFF
        """
        led_state = not self._read_pin_state(LEDS[led_name])
        return led_state

######### methods for BUTTON pins ########################
    def set_button_edge_event_callback(self,callback) -> bool:
        '''
        Set the callback for a change (edge_event) on a button state
        The callback will process the event
        
        :arguments
            callback : the reference to the callback function, upon an button event
        :return
            False : callback function does not exists
            True: callback found and configured
        '''
        if callback:
            self.edge_event_callback = callback
            return True
        print("callback function does not exists")
        return False

    def gpio_cleanup(self) -> None:
        '''
        Reset the GPIO pins to their default state
        '''
        GPIO.cleanup()

    def _edge_callback(self, channel: int) -> bool:
        """
        Unified handler for both press (falling) and release (rising) edges.
        When channel has a known button_name, the configured callback is called
        :argument 
            channel (int) is the I/O-pin which detected an edge event
        :return
            False (default): when channel refers to an unknown pin/button_name
            True : The button_name of the pin is found and callback is called 
        """
        button_name = self.gpio_to_button[channel]
        if not button_name:
            return
        button_value = GPIO.input(channel)
        if button_value == GPIO.LOW:
            button_state = BUTTON_PRESSED
        else:
            button_state = BUTTON_RELEASED

        self.edge_event_callback(button_state, button_name)

    def _read_pin_state(self, io_pin: int) -> bool:
        '''
        read the state of the specified io-pin
        :arguments
            io_pin: int = which pin to read
        :return
            True = pin is HIGH
            False = pin is LOW
        '''
        return(bool(GPIO.input(io_pin)))


# Entry point for stand-alone operation
if __name__ == '__main__':

    '''
    module test for gpio_service 
    Note:
    in case remote python debugging is required:
    * change the HOST_ADDRESS to your PC's local IP address 
    * run the Python Debug Server in your IDE
    * call module test with argument -d remote
        >python gpio_service.py -s remote
    '''
    from oradio_utils import setup_remote_debugging
    import sys

    ### Change HOST_ADDRESS to your host computer local address for remote debugging
    HOST_ADDRESS = "192.168.178.52"
    DEBUG_PORT = 5678
    if not setup_remote_debugging(HOST_ADDRESS,DEBUG_PORT):
        print("The remote debugging error, check the remote IP connection")
        sys.exit  # pylint: disable=pointless-statement

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

    def _buttons_testing(test_gpio) -> None:
        '''
        module tests for the BUTTONS
        :arguments
            test_gpio : instance of the GPIO class under test
        :return
            True : OK
            False: Error condition
        '''
        if not test_gpio.set_button_edge_event_callback(_button_event_callback):
            print("button_edge_event_callback not found!")
            return
        print("Touch a button and check results. To stop test press RETURN!")
        while True:
            _ = input("Press RETURN key to stop test and continue to main-menu\n")
            break
        return

    def _button_event_callback(button_state: bool, button_name: str) -> None:
        '''
        callback for button events testing
        
        '''
        print(f"Button change event: {button_name} = {button_state}")

#    def _read_pin_state(io_pin: int) -> bool:
#        '''
#        read the state of the specified io-pin
#        :arguments
#            io_pin: int = which pin to read
#        :return
#            True = pin is HIGH
#            False = pin is LOW
#        '''
#        return(bool(GPIO.input(io_pin)))

    def _run_led_pin_action_menu(leds_gpio: GPIOService, selected_led_pin: str) -> None:
        """
        Inner menu to run actions for a selected LED pin.
        :argument leds_gpio : instance of gpio service to be use
        :argument selected_led_pin : the name of the led-pin to use
        :return None  when action menu is stopped
        """

        led_pin_options = ["Quit"]\
                        + [f"Turn {selected_led_pin} ON"]\
                        + [f"Turn {selected_led_pin} OFF"]
        while True:
            # --- Show test menu with the selection options---
            for idx, name in enumerate(led_pin_options, start=0):
                print(f"{NC} {idx} - {name}")

            led_pin_choice = _prompt_int("Select test number: ", default=-1)
            match led_pin_choice:
                case 0:
                    print("\nReturning to main menu selection...\n")
                    return
                case 1:
                    print(f"\nExecuting: Turn ON {selected_led_pin}\n")
                    leds_gpio.set_led_on(selected_led_pin)
                    pin_state = leds_gpio.get_led_state(selected_led_pin)
                    if pin_state is False:
                        print(f"{GREEN}Test Result: The selected LED {selected_led_pin} is ON")
                    else:
                        print(f"{RED}Test Result: The selected LED {selected_led_pin} is NOT ON !!")
                case 2:
                    print(f"\nExecuting: Turn OFF {selected_led_pin}\n")
                    leds_gpio.set_led_off(selected_led_pin)
                    pin_state = leds_gpio.get_led_state(selected_led_pin)
                    #pin_state = _read_pin_state(LEDS[selected_led_pin])
                    if pin_state is True:
                        print(f"{GREEN}Test Result: The selected LED {selected_led_pin} is OFF")
                    else:
                        print(f"{RED}Test Result: The selected LED {selected_led_pin} is NOT OFF")
                case _:
                    print("Please input a valid number.")


    def _all_leds_off(test_gpio) -> None:
        '''
        Switch off all LEDs
        '''
        for led_name in LED_NAMES:
            test_gpio.set_led_off(led_name)

    def _leds_testing(test_gpio) -> None:
        '''
        module tests for the LEDS
        :arguments
            test_gpio : instance of the GPIO class under test
        '''
        # pylint: disable=too-many-branches

        # create a led-pin selection list
        led_pin_options = ["Quit"] + LED_NAMES + ["All leds-pins ON"] + ["All leds-pins OFF"]
        test_active = True
        while test_active:
            # --- LED selection ---
            print(f"\n{NC}Select a LED pin:")
            for idx, name in enumerate(led_pin_options, start=0):
                print(f"{NC} {idx} - {name}")

            led_pin_choice = _prompt_int("Select LED number: ", default=-1)

            if led_pin_choice == 0:
                print("\nExiting led testing\n")
                test_active = False
            elif not 0 <= led_pin_choice < len(led_pin_options):
                print(f"{YELLOW}Please input a valid number.")
            elif led_pin_options[led_pin_choice] == "All leds-pins ON":
                all_pins_state = False # the LED pins ON state
                for led_name in LED_NAMES:
                    test_gpio.set_led_on(led_name)
                    all_pins_state = all_pins_state or test_gpio.get_led_state(led_name)
                if all_pins_state is False:
                    print(f"{GREEN} Test Result: All the LEDs are ON")
                else:
                    print(f"{RED} Test Result: Not all the LEDS are ON !!")
            elif led_pin_options[led_pin_choice] == "All leds-pins OFF":
                all_pins_state = True # the led-pins -OFF state
                for led_name in LED_NAMES:
                    test_gpio.set_led_off(led_name)
                    all_pins_state = all_pins_state or test_gpio.get_led_state(led_name)
                if all_pins_state is True:
                    print(f"{GREEN} Test Result: All the LEDs are OFF")
                else:
                    print(f"{RED} Test Result: Not all the LEDS are OFF !!")
            else:
                _all_leds_off(test_gpio)
                selected_led_pin = led_pin_options[led_pin_choice]
                _run_led_pin_action_menu(test_gpio, selected_led_pin)
        return

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
        while test_active:
            test_options = ["QUIT"] + ["LEDS"] + ["BUTTONS"]
            for idx, name in enumerate(test_options, start=0):
                print(f"{NC} {idx} - {name}")
            test_choice = _prompt_int("Select LED or BUTTON testing: ", default=-1)
            if not 0 <= test_choice < len(test_options):
                print(f"{YELLOW}Please input a valid number.")
            elif test_options[test_choice] == "LEDS":
                _leds_testing(test_gpio)
            elif test_options[test_choice] == "BUTTONS":
                _buttons_testing(test_gpio)
            else:
                print("\nExiting test program\n")
                test_gpio.gpio_cleanup()
                test_active = False
        sys.exit  # pylint: disable=pointless-statement
    # Present menu with tests
    _interactive_menu()
