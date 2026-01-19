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
#REVIEW Onno: Ergens, of hier of bij de LEDControl class, graag een paar zinnen over wat de module doet...
@summary: Oradio LED control module

"""
#REVIEW Onno: Algemeen:
#   - Ik zie de stand-alone tests van modules liever basic, puur op 'werkt het' gericht
#     Uitgebreidere testen, zoals performance en robuustheid graag in aparte module test files zetten
#   - Gebruik Google Docstrings style zoals al in veel ander modules.
#   - Je mag iets royaler/consequenter zijn met inline comments
#   - Wees consistent in je gebruik van lege regels.
#   - turn_off_led en turn_on_led geven een true/false terug, maar daar wordt nergens op gecontroleerd.
#     Dus óf overal controleren, óf geen bool retourneren. Bij oplossen van issue #408 komt dat dan later wel goed...
#   - oneshot en blinking gebruiken een tijdseenheid als argument. oneshot heeft een default, blinking niet, waarom?
#     Stel voor 2 lokale variabelen te definieren, als default voor oneshot en blinking te gebruiken.
#     En dan in oradio_control overal die ,2 als agument voor blinking weg te halen.
import time
from threading import Thread, Timer, Event

##### oradio modules ####################
from oradio_logging import oradio_log
from gpio_service import GPIOService

##### GLOBAL constants ####################
#REVIEW Onno: Op zich correct, maar zou zelfde stijl aanhouden als in andere modules:
#
# from oradio_const import (
#    GREEN, YELLOW, RED, NC,
#    LED_NAMES,
#)
from oradio_const import (LED_NAMES, GREEN, YELLOW, RED, NC)

class LEDControl:
    """Control LED states"""

    def __init__(self):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for LED IO-service
        :exceptions
            ValueError : when GPIOService initialization fails
        """
        try:
            self.leds_driver = GPIOService()
        except (ValueError) as err:
#REVIEW Onno: f"{...}" constructs vermijden, want wordt altijd geevalueerd, ook als log level hoger is. De aanbevolen construct is ("...%s...", value)
            oradio_log.error(f"GPIO Initialization failed: {err}")
#REVIEW Onno: We gebruiken geen exceptions om fouten te propageren. (onderdeel van issue #408)
            raise ValueError("Invalid value provided") from err
        self.blink_stop_events = {}       # map led_name → threading.Event()
        self.blinking_threads = {}        # map led_name → Thread
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

########## As a wrapper for oradio_control ######################
#REVIEW Onno: Waarom nu niet meteen oradio_control aanpassen? Dan kan deze method weg, kan je het ook niet vergeten...
    def turn_on_led_with_delay(self,
                               led_name:str,
                               period:float):
        '''
        As a wrapper for oradio_control, temporary solution
        Should be removed after rework on oradio_control
        redirecting to oneshot_on_led
        '''
        return self.oneshot_on_led(led_name, period)
###################################################################

#REVIEW Onno: Er wordt nergens op de return gecontroleerd -> None teruggeven
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

#REVIEW Onno: Duidelijker is een aparte stop_blink() method te maken, die in turn_led_off en turn_led_on te gebruiken
        # signal any blink thread to stop
        running_stop_event = self.blink_stop_events.pop(led_name, None)
        if running_stop_event:
            running_stop_event.set()

        # block until thread really finishes
        active_thread = self.blinking_threads.pop(led_name, None)
        if active_thread:
            active_thread.join()

#REVIEW Onno: inline comments graag consequent gebruiken: hier ontbreekt hij, bij led_on staat hij er wel.
        self.leds_driver.set_led_off(led_name)
        oradio_log.debug("%s turned off", led_name)
        return True

#REVIEW Onno: return type hint ontbreekt. Er wordt nergens op de return gecontroleerd -> None teruggeven
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

#REVIEW Onno: Je 'misbruikt' de led_off method nu. zie opmerking om blinking in aparte helper method te stoppen.
        # stop blinking silently (no 'turned off' log), then light it
        self.turn_off_led(led_name)
#REVIEW Onno: inline comments graag consequent gebruiken: hier wel, bij led_on ontbreekt hij.
        # Turn led ON
        self.leds_driver.set_led_on(led_name)
        oradio_log.debug("%s turned on", led_name)
        return True

    def turn_off_all_leds(self) ->None:
        """Stops all blink‐threads and turns every LED off."""
        for led_name in LED_NAMES:
            self.turn_off_led(led_name)
        oradio_log.debug("All LEDs turned off and blinking stopped")

#REVIEW Onno: return type hint ontbreekt
    def turn_on_all_leds(self):
        """Stops all blink‐threads and turns every LED on."""
        for led_name in LED_NAMES:
            self.turn_on_led(led_name)
        oradio_log.debug("All LEDs turned ON and blinking stopped")

#REVIEW Onno: Er wordt alleen in de stand-alone test case 3 op de return gecontroleerd, maar niets mee gedaan als false:
#             Stel daarom voor om None terug te geven, in method _single_led_test test case 3 niet op testen, in turn_on_led_with_delay niet te retourneren
    def oneshot_on_led(self,
                       led_name : str,
                       period: float=3) ->bool:
        """
        Turns on a specific LED and then turns it off after a delay.

        :arguments:
            led_name (str), precondition: must be [ LED_PLAY | LED_STOP |
                                                    LED_PRESET1 | LED_PRESET2 | LED_PRESET3] 
            period (float): Time in seconds before turning off the LED.Default = 3
        :return
#REVIEW Onno: True/false tekst copy/paste error
            True: Oneshot running for specified LED 
            False: Invalid LED name 
        """
#REVIEW Onno: Method naam 'oneshot_led_off' is andere stijl dan overige methods. Hernoem naar 'oneshot_off_led'
        def oneshot_led_off(led_name, period):
            self.turn_off_led(led_name)
            oradio_log.debug("%s turned off after %s seconds", led_name, period)

        if period <= 0:
            # no valid period, no timer started
            oradio_log.warning("Invalid period time of %f for oneshot of led: %s",period, led_name)
            return False
        period = round(period,1) # more accuracy not visible, so not required
        if led_name not in LED_NAMES:
            oradio_log.error("Invalid LED name: %s", led_name)
            return False

        # Stop any blinking for this LED and turn it on
        self.turn_on_led(led_name)
        oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, period)
        oneshot_timer = Timer(period,oneshot_led_off, args=(led_name,period))
        oneshot_timer.start()
        return True

#REVIEW Onno: Zou ook hier geen bool retourneren, net als de andere methods in deze class
#             Let wel op dat je dan de check in de method _single_led_test test case 4 verwijdert
    def control_blinking_led(self,
                             led_name: str,
                             cycle_time:float = None) -> bool:
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
#REVIEW Onno: verplaats docstring cycle time uitleg hierboven naar docstring van deze method
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
        stop_evt = Event()
        self.blink_stop_events[led_name] = stop_evt
        thread = Thread(target=_blink, daemon=True)
        thread.start()
        self.blinking_threads[led_name] = thread
        oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)
        return True

# Entry point for stand-alone operation
if __name__ == "__main__":
    import sys
    import math
    from oradio_utils import input_prompt_int, input_prompt_float
    from remote_debugger import setup_remote_debugging,DEBUGGER_NOT_CONNECTED, DEBUGGER_ENABLED

    print("\nStarting LED Control Module Test...\n")
    # try to setup a remote debugger connection, if enabled
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED:
        if connection_status == DEBUGGER_NOT_CONNECTED:
            print(f"{RED}A remote debugging error, check the remote IP connection{NC}")
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


#REVIEW Onno: Mooi die progress bar, maar voelt een beetje als 'gold-plating'van de test code
    LED_OFF     = "▄" # symbol for led off
    LED_ON      = "▀" # symbol for led on
    BAR_LENGTH  = 60 # Number of characters for the progress bar
    def _progress_bar(led_control:LEDControl,
                      led_name:str,
                      duration:int)-> float:
        '''
        progress bar
        extended ascii characters see at https://coding.tools/ascii-table
        :arguments
            led_name (str) = [ LED_PLAY | LED_STOP] |
                            LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
            seconds (int) : duration of progress bar
        :return led_on_timing (float, 1 decimal)
        '''
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
                    led_on_timing = round(elapsed,1)
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
    def _show_and_measure_blinking(led_control:LEDControl,
                                   led_name:str,
                                   cycle_time: float,
                                   stop_event : Event )-> float:
        # pylint: disable=too-many-locals
        ################################################################
        # motivation: for calculation purposes more vars are required
        #################################################################
        '''
        display the blinking state of selected led
        extended ascii characters see at https://coding.tools/ascii-table
        :arguments
            led_name (str) = [ LED_PLAY | LED_STOP] |
                            LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
            led_control : test instance of LEDControl
            cycle_time : the cycle time as float
            stop_event : Event to stop the test
        :return 
            state_time = the measured ON or OFF period of blink.
        '''
        # pylint: disable=too-many-branches
        ####################################################################
        # motivation: OK, but branches are rather simple and clearly defined
        ######################################################################
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

        line          = [" "] * LINE_LENGTH  # Initialize with spaces
        led_state     = led_control.leds_driver.get_led_state(led_name)
        start_time    = time.monotonic()
        half_time     = round_down((cycle_time/2),2)
        puls_length   = int(half_time/INTERVAL_TIME)
        mid_puls_position = int(puls_length/2)
        while not stop_event.is_set():
            # Get current LED state
            new_led_state = led_control.leds_driver.get_led_state(led_name)
            now = time.monotonic()
            state_time = 0.0
            if new_led_state != led_state:
                state_time = round_down((now - start_time),2)
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

    def _single_led_test(led_control:LEDControl,
                         selected_led:str) ->None:
        '''
        Test the selected LED functions
        :arguments 
            selected_led (str) = [ LED_PLAY | LED_STOP] |
                                  LED_PRESET1 | LED_PRESET2 | LED_PRESET3 ]
            led-driver = instance of LEDControl to use
        '''
        # pylint: disable=too-many-branches
        ####################################################################
        # motivation: OK, but branches are rather simple and clearly defined
        ######################################################################
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
                    if led_control.oneshot_on_led(selected_led,one_shot):
                        led_on_timing = _progress_bar(led_control, selected_led, one_shot+1 )
                        if led_on_timing == round(one_shot,1):
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
                        if led_control.control_blinking_led(selected_led, cycle_time):
                            _show_and_measure_blinking(led_control,
                                                       selected_led,
                                                       cycle_time,
                                                       stop_event)
                            led_control.turn_off_led(selected_led) # stop blinking
                        else:
                            print(f"{RED}Test Result: The blinking failed for {selected_led}")
                case _:
                    print("Please input a valid number.")

    def _interactive_menu():
        """Show menu with test options"""
        # pylint: disable=too-many-branches
        ####################################################################
        # motivation:
        # probably caused by match-case,
        # but branches are rather simple and clearly defined
        ######################################################################
        try:
            led_control = LEDControl()
        except (ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return

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
                        led_control.oneshot_on_led(led,one_shot)
                        print(f"\n{one_shot} sec ONESHOT ON for {led}\n")
                    _ = input("Press any key to stop blinking")
                    led_control.turn_off_all_leds()
                case 5:
                    print(f"\n running {test_options[5]}\n")
                    led_options = ["Quit"] + LED_NAMES
                    for idx, led_name in enumerate(led_options, start=0):
                        print(f" {idx} - {led_name}")
                    led_choice = input_prompt_int("Select a LED: ", default=-1)
                    match led_choice:
                        case 0:
                            print("\nExiting test program\n")
                        case 1 | 2 | 3 | 4 | 5:
                            _single_led_test(led_control, LED_NAMES[led_choice-1])
                case _:
                    print("Please input a valid number.")

    # Present menu with tests
    _interactive_menu()
