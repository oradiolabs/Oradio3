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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio touch buttons module with debounce and standalone test mode
"""
import time
import threading
from RPi import GPIO

##### oradio modules ####################
from oradio_logging import oradio_log
from play_system_sound import PlaySystemSound
from led_control import LEDControl  # Import LED control module

##### LOCAL constants ####################
# Debounce time in milliseconds to ignore rapid repeat triggers
BUTTON_DEBOUNCE_TIME = 500

# Define button GPIO mappings globally
BUTTONS = {
    "Play": 9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop": 6
}

# Press durations
LONG_PRESS_DURATION = 6         # Seconds for long press


class TouchButtons:
    """Handles GPIO-based touch buttons with debounce, immediate short-press actions,
    and continuous-hold long-press detection optimized for speed."""

    def __init__(self, state_machine=None):
        """
        Initializes the button handler.
        :param state_machine: The state machine instance for handling transitions.
        """
        self.state_machine = state_machine
        self.sound_player = PlaySystemSound()
        self.led_control = LEDControl()

        # Press tracking
        self.button_press_times = {}      # For press timestamps
        self.last_trigger_times = {}      # For software debounce (seconds since epoch)
        self.long_press_timers = {}       # button_name -> threading.Timer
        self.long_press_fired = set()     # button_names where long press already handled

        # Public overridable long-press handler; test code may assign to this
        self.long_press_handler = self._default_long_press_handler

        self.gpio_to_button = {pin: name for name, pin in BUTTONS.items()}

        self._setup_gpio()

    def _setup_gpio(self):
        """Configures GPIO pins and sets up edge detection (both edges) with a single callback."""
        GPIO.setmode(GPIO.BCM)
        for pin in BUTTONS.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                pin,
                GPIO.BOTH,
                callback=self._edge_callback,
                bouncetime=10
            )

    def _edge_callback(self, channel):
        """Unified handler for both press (falling) and release (rising) edges."""
        button_name = self.gpio_to_button.get(channel)
        if not button_name:
            return

        level = GPIO.input(channel)
        if level == GPIO.LOW:
            self._handle_press(channel, button_name)
        else:
            self._handle_release(channel, button_name)

    def _handle_press(self, channel, button_name):
        """Handle falling-edge: immediate short-press action plus start long-press timer."""
        now = time.monotonic()
        last = self.last_trigger_times.get(button_name, 0)
        if (now - last) * 1000 < BUTTON_DEBOUNCE_TIME:
            return  # software debounce
        self.last_trigger_times[button_name] = now
        self.button_press_times[button_name] = now

        prev_timer = self.long_press_timers.pop(button_name, None)
        if prev_timer:
            prev_timer.cancel()
        self.long_press_fired.discard(button_name)

        timer = threading.Timer(
            LONG_PRESS_DURATION,
            self._long_press_timeout,
            args=(channel, button_name)
        )
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()

        # Immediate feedback (short press semantics)
        self.sound_player.play("Click")
        new_state = f"State{button_name}"
        if self.state_machine:
            self.state_machine.transition(new_state)

    def _handle_release(self, _channel, button_name):
        """Handle rising-edge: cancel pending long-press detection if any."""
        timer = self.long_press_timers.pop(button_name, None)
        if timer:
            timer.cancel()

        if button_name in self.long_press_fired:
            self.long_press_fired.discard(button_name)

        # Short press already handled on falling edge; nothing else needed.

    def _long_press_timeout(self, channel, button_name):
        """Fires if the button has been held continuously for LONG_PRESS_DURATION."""
        if GPIO.input(channel) != GPIO.LOW:
            return

        self.long_press_fired.add(button_name)
        self.long_press_timers.pop(button_name, None)

        # Run the (possibly overridden) handler asynchronously
        threading.Thread(
            target=self._run_long_press_handler,
            args=(button_name,),
            daemon=True
        ).start()

    def _run_long_press_handler(self, button_name):
        """Invoker for the public long_press_handler attribute."""
        try:
            self.long_press_handler(button_name)
        except Exception:  # pylint: disable=broad-exception-caught
            # Defensive: ensure user-supplied handler failures do not crash the timer path
            oradio_log.exception("Exception in long press handler for %s", button_name)

    def _default_long_press_handler(self, button_name):
        """Default long-press behavior if not overridden."""
        if self.state_machine:
            if button_name == "Play":
                self.state_machine.start_webservice()
            else:
                oradio_log.info(
                    "LONG press detected on button: %s (no action)", button_name
                )

    def _blink_led(self):
        """Blinks all LEDs off after a short delay."""
        time.sleep(0.05)
        self.led_control.turn_off_all_leds()

    def cleanup(self):
        """Cleans up GPIO on exit. Also cancels any pending timers."""
        for timer in list(self.long_press_timers.values()):
            timer.cancel()
        GPIO.cleanup()


# ------------------ Standalone Test Mode ------------------
if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    LONG = LONG_PRESS_DURATION

    print("\nStarting Touch Buttons Test Mode…\n")
    print(f" • Short press (< {LONG}s): single LED blink + console msg")
    print(f" • Long press  (>= {LONG}s): double LED blink + console msg")
    print("Press Ctrl+C to exit.\n")

    # Instantiate without a real state machine
    tb = TouchButtons(state_machine=None)

    class DummyStateMachine:
        """Simple stub state machine for test mode to show short presses."""
        def transition(self, new_state):
            """Handle a short-press transition by blinking the corresponding LED."""
            btn = new_state.replace("State", "")
            print(f"[TEST] Short press on {btn!r}")
            led = f"LED{btn}"
            tb.led_control.turn_on_led(led)
            time.sleep(0.1)
            tb.led_control.turn_off_all_leds()

    tb.state_machine = DummyStateMachine()

    def test_long_press(button_name):
        """Test-mode replacement for long-press handling."""
        print(f"[TEST] Long press on {button_name!r}")
        led = f"LED{button_name}"
        for _ in range(2):
            tb.led_control.turn_on_led(led)
            time.sleep(0.1)
            tb.led_control.turn_off_all_leds()
            time.sleep(0.1)

    tb.long_press_handler = test_long_press

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting test mode…")
    finally:
        tb.cleanup()

# Restore checking or duplicate code
# pylint: enable=duplicate-code
