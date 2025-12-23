#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on April 28, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       4
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio touch buttons module with debounce, per-button callbacks, and selftest
"""

from threading import Timer, Thread
from multiprocessing import Queue

##### oradio modules ####################
from play_system_sound import PlaySystemSound
from oradio_logging import oradio_log
from gpio_service import GPIOService
from oradio_utils import safe_put

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500          # ms, ignore rapid repeats
DEBOUNCE_SECONDS = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS           = 10                      # hardware debounce in GPIO.add_event_detect
LONG_PRESS_DURATION = 2  # seconds

##### GLOBAL constants ####################
from oradio_const import (BUTTON_PLAY,BUTTON_STOP, BUTTON_PRESET1, BUTTON_PRESET2, BUTTON_PRESET3, BUTTON_NAMES, \
                         GREEN, YELLOW, RED, NC)


class TouchButtons:
    """
    Handle GPIO-based touch buttons with debounce, short-press callbacks,
    and long-press callbacks. This class has **no knowledge** of the state machine.
    """

    def __init__(self,queue : Queue):
        """
        Class constructor: setup class variables
        and create instance for GPIOService class for LED IO-service
        :arguments
            queue: the shared message queue
        :exceptions
            ValueError : when GPIOService initialization fails
        """
        try:
            self.button_driver = GPIOService()
        except (ValueError) as err:
            oradio_log.error(f"GPIO Initialization failed: {err}")
            raise ValueError("Invalid value provided") from err
        self.button_driver.set_button_edge_event_callback(self._button_event_callback)

        self.sound_player = PlaySystemSound()
        ## Note: PlaySystemSound has no exceptions, so no need to try

        self.message_queue = queue

        self.button_press_times: dict[str, float] = {}   # button -> press start (monotonic)
        self.last_trigger_times: dict[str, float] = {}   # button -> last accepted press time
        self.long_press_timers: dict[str, threading.Timer] = {}  # button -> Timer

    def _button_event_callback(self, button_state: bool, button_name: str) -> None:
        '''
        callback for button events
        :arguments
            button_state : [BUTTON_PRESSED | BUTTON_RELEASED
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]

        '''
        print(f"Button change event: {button_name} = {button_state}")
        now = time.monotonic()
        last = self.last_trigger_times.get(button_name, 0.0)
        if (now - last) < DEBOUNCE_SECONDS:
            return  # software debounce

        self.last_trigger_times[button_name] = now
        self.button_press_times[button_name] = now

        # Cancel any stale timer, then arm a fresh one
        prev = self.long_press_timers.pop(button_name, None)
        if prev:
            prev.cancel()

        timer = Timer(LONG_PRESS_DURATION,
                                self._long_press_timeout,
                                args=(button_name,))
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()

        # Immediate short-press feedback
        if self.sound_player:
            self.sound_player.play("Click")
            # Note: method does not report error or raises exceptions
        
        ### Send Message to message queue

    def _long_press_timeout(self, button_name: str) -> None:
        """Fire long-press if still held after LONG_PRESS_DURATION.
        If button is in the list of VALID_LONG_PRESS_BUTTONS,
        it is allowed to put message in queue to inform controls
        :arguments
            button_name : [BUTTON_PLAY | BUTTON_STOP |
                            BUTTON_PRESET1 | BUTTON_PRESET2 | BUTTON_PRESET3]
        """
        
        if not self.button_driver.get_button_state(button_name):
            return  # released during wait; ignore

        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)
        if button_name in VALID_LONG_PRESS_BUTTONS:
            safe_put(self.message_queue, message)

# ------------------ Standalone Test (no state machine) ------------------
if __name__ == "__main__":
    import sys
    import time

    from oradio_utils import setup_remote_debugging
    ### Change HOST_ADDRESS to your host computer local address for remote debugging
    HOST_ADDRESS = "192.168.178.52"
    DEBUG_PORT = 5678
    if not setup_remote_debugging(HOST_ADDRESS,DEBUG_PORT):
        print("The remote debugging error, check the remote IP connection")
        sys.exit()

    def _handle_message(message):
        command_source = message.get("source")
        state = message.get("state")
        error = message.get("error", None)

    def _check_for_new_message_in_queue(msg_queue):
        """Continuously read and handle messages from the shared queue."""
        while True:
            try:
                msg = msg_queue.get()  # blocking
                oradio_log.debug("Received message in Queue: %r", msg)
                _handle_message(msg)
            except KeyError as ex:
                # A required key like 'source' or 'state' is missing
                oradio_log.error("Malformed message (missing key): %s | msg=%r", ex, msg)
            except (TypeError, AttributeError) as ex:
                # msg wasn't a mapping/dict-like or had wrong types
                oradio_log.error("Invalid message format: %s | msg=%r", ex, msg)
            except (RuntimeError, OSError) as ex:
                # Unexpected runtime/OS errors during handling
                oradio_log.exception("Runtime error in process_messages: %s", ex)


    shared_queue = Queue()
    test_buttons = TouchButtons( shared_queue)

    # Create a thread to listen and process new messages in shared queue
    Thread(target=_check_for_new_message_in_queue, args=(shared_queue,), daemon=True).start()
    
    _ = input("Press any key to stop test")
    test_buttons.button_driver.gpio_cleanup()
