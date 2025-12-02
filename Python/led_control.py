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
from RPi import GPIO

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import LEDS

class LEDControl:
    """Control LED states"""

    def __init__(self):
        """Class constructor: setup class variables"""
        ## Review Henk  #####################################################
        # Graag de GPIO.setmode() op slecht 1 plek initialiseren.
        # Nu op 2 plekken, nl ook in touch_buttons.py
        # Mijn voorstel zou zijn om een aparte gpio_service.py module te maken
        # waarbinnen de GPIO configuratie opgezet wordt en aansturing van de gpio pinnen gedaan wordt.
        # De led_control module weet in principe niets van de GPIO, maar biedt wel
        # extra services voor aan/uit zetten leds, blinking, delays, etc
        # Voor een betere abstracties zouden we in die module de volgende functies moeten aanbieden
        # def GPIO_config()
        #    GPIO.setwarnings(False)
        #    GPIO.setmode(GPIO.BCM)
        #    for _, pin in LEDS.items():
        #       GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
        #    for pin in BUTTONS.values():
        #       GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        #       try:
        #          GPIO.remove_event_detect(pin)
        #       except RuntimeError:
        #          pass
        #
        # Class GPIOControl()
        #   def __init__(self)
        #      ## things to setup or initialize
        #
        #   # pin_name = [LED_PLAY, LED_PRESET1, LED_PRESET2, LED_PRESET3, LED_STOP,
        #                    BUTTON_PLAY, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, BUTTON_STOP
        #   def turn_on_led(led_name:str)
        #   def turn_off_led(led_name:str)
        #   def turn_off_all_leds()
        #   def short_blink_led(led_name:str)
        #   def long_blink_led(led_name:str)
        #   def _control_blinking_led( led_name:str, cycle_time:int
        #
        ##########################################################################
        GPIO.setmode(GPIO.BCM)                                # moved to GPIO_service.py
        for _, pin in LEDS.items():                           # moved to GPIO_service.py
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)      # moved to GPIO_service.py
        ###############################################################################
        self.blink_stop_events = {}       # map led_name → threading.Event()
        self.blinking_threads = {}        # map led_name → Thread
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name, log: bool = True):
        """Turns off a specific LED and waits for its blink‐thread to exit."""
          ### Review Henk  #############
        # Return True or False
        # True = Blinking started
        # False = Failure
        # or if it makes sense return a success/error status
        ################################################
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # signal any blink thread to stop
        evt = self.blink_stop_events.pop(led_name, None)
        if evt:
            evt.set()

        # block until thread really finishes
        thread = self.blinking_threads.pop(led_name, None)
        if thread:
            thread.join()

        # now safe to drive off
        # Review Henk:
        # moved to GPIO_service.py
        GPIO.output(LEDS[led_name], GPIO.HIGH)
        if log:
            oradio_log.debug("%s turned off", led_name)


    def turn_on_led(self, led_name):
        """Turns on a specific LED (stops blinking if active)."""
         ### Review Henk  #############
        # Return True or False
        # True = Blinking started
        # False = Failure
        # or if it makes sense return a success/error status
        ################################################
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # stop blinking silently (no 'turned off' log), then light it
        self.turn_off_led(led_name, log=False)
        # Review Henk:
        # moved to GPIO_service.py
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_log.debug("%s turned on", led_name)

    def turn_off_all_leds(self):
        """Stops all blink‐threads and turns every LED off."""
        # stop all threads
        for evt in self.blink_stop_events.values():
            evt.set()
        for thread in self.blinking_threads.values():
            thread.join()
        self.blink_stop_events.clear()
        self.blinking_threads.clear()

        # drive every pin HIGH
        # Review Henk:
        # moved to GPIO_service.py
        for pin in LEDS.values():
            GPIO.output(pin, GPIO.HIGH)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_led_with_delay(self, led_name, delay=3):
        """
        Turns on a specific LED and then turns it off after a delay.

        Args:
            led_name (str): The name of the LED to control.
            delay (float): Time in seconds before turning off the LED.
        """
         ### Review Henk  #############
        # Return True or False
        # True = Blinking started
        # False = Failure
        # or if it makes sense return a success/error status
        ################################################
        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return

        # Stop any blinking for this LED and turn it on
        self.turn_off_led(led_name)
        # Review Henk:
        # moved to GPIO_service.py
        GPIO.output(LEDS[led_name], GPIO.LOW)
        oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, delay)

        def delayed_off():
            time.sleep(delay)
            # Review Henk:
            # moved to GPIO_service.py
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_log.debug("%s turned off after %s seconds", led_name, delay)
        ####################################################################
        ## Review Henk:
        # Propoose to use a Timer thread, to prevent the sleep in the thread
        # from threading import Timer
        # delay_timer = Timer(delay, delayed_off)
        # delay_timer.start()
        ######################################################################
        threading.Thread(target=delayed_off, daemon=True).start()

    def control_blinking_led(self, led_name, cycle_time=None):
        """
        Blink using an Event for instant stop, not long sleeps.
        """
        ##############################################
        # review Henk
        # specify the arguments led_name and cycle_time
        ###############################################

        if led_name not in LEDS:
            oradio_log.error("Invalid LED name: %s", led_name)
            return
        ### Review Henk  #############
        # Return True or False
        # True = Blinking started
        # False = Failure
        # or if it makes sense return a success/error status
        ################################################

        # stop any existing blink
        old_evt = self.blink_stop_events.pop(led_name, None)
        if old_evt:
            old_evt.set()
        old_thread = self.blinking_threads.pop(led_name, None)
        if old_thread:
            old_thread.join()

        # if no cycle_time, just turn off
        if not cycle_time:
            # Review Henk:
            # moved to GPIO_service.py
            GPIO.output(LEDS[led_name], GPIO.HIGH)
            oradio_log.debug("%s blinking stopped and turned off", led_name)
            return

        # start new blink thread
        stop_evt = threading.Event()
        self.blink_stop_events[led_name] = stop_evt

        def _blink():
            pin = LEDS[led_name]
            half = cycle_time / 2
            while not stop_evt.is_set():
                GPIO.output(pin, GPIO.LOW)
                if stop_evt.wait(half):
                    break
                GPIO.output(pin, GPIO.HIGH)
                if stop_evt.wait(half):
                    break
            GPIO.output(pin, GPIO.HIGH)

        thread = threading.Thread(target=_blink, daemon=True)
        thread.start()
        self.blinking_threads[led_name] = thread
        oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)


    def selftest(self) -> bool:
        """
        Minimal LED self-test: runs a short sequence
        LEDStop → LEDPreset3 → LEDPreset2 → LEDPreset1 → LEDPlay,
        each on for 0.1s. Returns True on success, False if any LED name is invalid.
        """
        ############
        # Review Henk:
        # Deze selftest controleert of de opgegeven LEDs in sequence goed zijn, zoals
        # ze gedefinieerd zijn als constants.
        # Mijn voorstel zou zijn om de testen of de leds ook werkelijk aan of uit staan
        # door de GPIO status op te vragen van de geactiveerde led.
        # Hiermee test je of de leds werkelijk aan of uit staan.
        #######################################################################
        sequence = ["LEDStop", "LEDPreset3", "LEDPreset2", "LEDPreset1", "LEDPlay"]
        try:
            self.turn_off_all_leds()
            for name in sequence:
                if name not in LEDS:
                    oradio_log.error("LEDControl selftest: %s not in LEDS map", name)
                    return False
                self.turn_on_led(name)
                time.sleep(0.1)
                self.turn_off_all_leds()
            oradio_log.info("LEDControl selftest OK (ran sequence)")
            return True

        except Exception as exc:  # pylint: disable=broad-exception-caught
            oradio_log.error("LEDControl selftest FAILED: %s", exc)
            return False

# Entry point for stand-alone operation
if __name__ == "__main__":

    print("\nStarting LED Control Standalone Test...\n")

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

    def _run_led_action_menu(leds: LEDControl, selected_led: str) -> None:
        """Inner menu to run actions for a selected LED."""
        input_selection = (
            "\nSelect an action for the LED:\n"
            " 0 - Return to LED selection\n"
            f" 1 - Turn {selected_led} ON\n"
            f" 2 - Turn {selected_led} OFF\n"
            f" 3 - Blink {selected_led}\n"
            f" 4 - Turn {selected_led} ON and OFF after delay\n"
            " 5 - Turn ALL LEDs OFF\n"
            "Select: "
        )
        while True:
            function_nr = _prompt_int(input_selection, default=-1)

            match function_nr:
                case 0:
                    print("\nReturning to LED selection...\n")
                    return
                case 1:
                    print(f"\nExecuting: Turn ON {selected_led}\n")
                    leds.turn_on_led(selected_led)
                case 2:
                    print(f"\nExecuting: Turn OFF {selected_led}\n")
                    leds.turn_off_led(selected_led)
                case 3:
                    cycle = _prompt_float("Enter blink cycle time (seconds): ")
                    if cycle is None or cycle <= 0:
                        print("Please enter a positive number.")
                        continue
                    print(f"\nExecuting: Blinking {selected_led} every {cycle}s\n")
                    leds.control_blinking_led(selected_led, cycle)
                case 4:
                    wait = _prompt_float("Enter delay before turning off (seconds): ")
                    if wait is None or wait < 0:
                        print("Please enter a non-negative number.")
                        continue
                    print(f"\nExecuting: Turning ON {selected_led} and OFF after {wait} seconds\n")
                    leds.turn_on_led_with_delay(selected_led, wait)
                case 5:
                    print("\nExecuting: Turn OFF all LEDs\n")
                    leds.turn_off_all_leds()
                case _:
                    print("Please input a valid number.")

    def interactive_menu():
        """Show menu with test options"""
        try:
            leds = LEDControl()
        except (RuntimeError, ValueError) as ex_err:
            print(f"Initialization failed: {ex_err}")
            return

        led_options = ["Quit", "Run selftest"] + list(LEDS.keys()) + ["Turn all LEDs OFF"]

        while True:
            # --- LED selection ---
            print("\nSelect a LED:")
            for idx, name in enumerate(led_options, start=0):
                print(f" {idx} - {name}")

            led_choice = _prompt_int("Select LED number: ", default=-1)

            if led_choice == 0:
                leds.turn_off_all_leds()
                GPIO.cleanup()
                print("\nExiting test program\n")
                break

            if not 0 <= led_choice < len(led_options):
                print("Please input a valid number.")
                continue

            selected_led = led_options[led_choice]

            if selected_led == "Run selftest":
                print("\nExecuting: Selftest\n")
                selftest_ok = leds.selftest()
                print("Selftest:", "OK" if selftest_ok else "FAILED")
                continue

            if selected_led == "Turn all LEDs OFF":
                print("\nExecuting: Turn all LEDs OFF\n")
                leds.turn_off_all_leds()
                continue

            _run_led_action_menu(leds, selected_led)

    # Present menu with tests
    interactive_menu()
