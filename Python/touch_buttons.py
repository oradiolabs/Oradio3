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
BUTTON_DEBOUNCE_TIME = 300

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
    """Handles GPIO-based touch buttons with debounce, long, and extra-long press detection."""

    def __init__(self, state_machine=None):
        """
        Initializes the button handler.
        :param state_machine: The state machine instance for handling transitions.
        """
        self.state_machine = state_machine
        self.sound_player = PlaySystemSound()
        self.led_control = LEDControl()

        # Track press and debounce
        self.button_press_times = {}      # For long press timing
        self.last_trigger_times = {}      # For debounce timing (seconds since epoch)
        self.gpio_to_button = {pin: name for name, pin in BUTTONS.items()}

        self._setup_gpio()

    def _setup_gpio(self):
        """Configures GPIO pins and sets up event detection with minimal bouncetime."""
        GPIO.setmode(GPIO.BCM)
        for pin in BUTTONS.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                pin,
                GPIO.FALLING,
                callback=self._button_callback,
                bouncetime=10
            )

    def _button_callback(self, channel):
        """Handles initial button press events with debounce."""
        button_name = self.gpio_to_button.get(channel)
        if not button_name:
            return

        # Debounce: ignore if within BUTTON_DEBOUNCE_TIME
        now = time.monotonic()
        last = self.last_trigger_times.get(button_name, 0)
        if (now - last) * 1000 < BUTTON_DEBOUNCE_TIME:
            return
        self.last_trigger_times[button_name] = now

        # Play click sound immediately
        self.sound_player.play("Click")

        # State transition
        new_state = f"State{button_name}"
        if self.state_machine:
            self.state_machine.transition(new_state)

        # Record for long press detection and spawn detector thread
        self.button_press_times[button_name] = now
        threading.Thread(
            target=self._detect_long_press,
            args=(channel, button_name),
            daemon=True
        ).start()

    def _blink_led(self):
        """Blinks all LEDs off after a short delay."""
        time.sleep(0.05)
        self.led_control.turn_off_all_leds()

    def _detect_long_press(self, channel, button_name):
        """Detects long presses without drifting under load."""
        # Calculate how much time has already elapsed since the button went down
        start = self.button_press_times.get(button_name, time.monotonic())
        elapsed = time.monotonic() - start

        # Sleep only the remaining time (if any)
        remaining = LONG_PRESS_DURATION - elapsed
        if remaining > 0:
            time.sleep(remaining)

        # Final guard: fire only if the button is still held
        if GPIO.input(channel) == GPIO.LOW:
            threading.Thread(
                target=self._long_press_handler,
                args=(button_name,),
                daemon=True
            ).start()

    def _long_press_handler(self, button_name):
        """Handles long press actions."""
        if self.state_machine:
            if button_name == "Play":
                self.state_machine.start_webservice()
            else:
                oradio_log.error(
                    "LONG press detected on button: %s (no action)", button_name
                )

    def cleanup(self):
        """Cleans up GPIO on exit."""
        GPIO.cleanup()

# ------------------ Standalone Test Mode ------------------
if __name__ == "__main__":
    import time

    LONG = LONG_PRESS_DURATION

    print("\nStarting Touch Buttons Test Mode…\n")
    print(f" • Short press (< {LONG}s): single LED blink + console msg")
    print(f" • Long press  (>= {LONG}s): double LED blink + console msg")
    print("Press Ctrl+C to exit.\n")

    # Instantiate without test_mode
    tb = TouchButtons(state_machine=None)

    # Monkey-patch a fake state machine for short presses
    class DummyStateMachine:
        def transition(self, new_state):
            btn = new_state.replace("State", "")
            print(f"[TEST] Short press on {btn!r}")
            led = f"LED{btn}"
            tb.led_control.turn_on_led(led)
            time.sleep(0.1)
            tb.led_control.turn_off_all_leds()

    tb.state_machine = DummyStateMachine()

    # Monkey-patch the long-press handler
    def test_long_press(button_name):
        print(f"[TEST] Long press on {button_name!r}")
        led = f"LED{button_name}"
        for _ in range(2):
            tb.led_control.turn_on_led(led)
            time.sleep(0.1)
            tb.led_control.turn_off_all_leds()
            time.sleep(0.1)

    tb._long_press_handler = test_long_press

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting test mode…")
    finally:
        tb.cleanup()
