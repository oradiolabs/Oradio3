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
@summary:       Oradio touch buttons module with debounce and standalone test mode and selftest
"""

import time
import threading
from RPi import GPIO

# oradio modules
from oradio_logging import oradio_log
from play_system_sound import PlaySystemSound

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500          # ms, ignore rapid repeats
DEBOUNCE_SECONDS = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS = 10                      # hardware debounce in GPIO.add_event_detect

BUTTONS = {
    "Play": 9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop": 6,
}

LONG_PRESS_DURATION = 6  # seconds


class TouchButtons:
    """Handle GPIO-based touch buttons with debounce, short-press action,
    and long-press detection. No LED dependency in the class itself.
    """

    def __init__(self, state_machine=None, sound_player=None):
        self.state_machine = state_machine
        self.sound_player = sound_player or PlaySystemSound()

        # Press tracking
        self.button_press_times = {}     # button -> press start (monotonic)
        self.last_trigger_times = {}     # button -> last accepted edge time
        self.long_press_timers = {}      # button -> Timer

        # Public, overridable by test harness:
        self.long_press_handler = self._default_long_press_handler

        # Fast channel -> name lookup
        self.gpio_to_button = {pin: name for name, pin in BUTTONS.items()}

        self._setup_gpio()

    # ---------- GPIO setup / edge handling ----------

    def _setup_gpio(self):
        """Configure pins and install both-edge detection with single callback."""
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in BUTTONS.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Ensure clean slate; ignore if not previously set
            try:
                GPIO.remove_event_detect(pin)
            except RuntimeError:
                pass
            GPIO.add_event_detect(pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS)

    def _edge_callback(self, channel):
        """Unified handler for both press (falling) and release (rising) edges."""
        button_name = self.gpio_to_button.get(channel)
        if not button_name:
            return

        if GPIO.input(channel) == GPIO.LOW:
            self._handle_press(channel, button_name)
        else:
            self._handle_release(channel, button_name)

    # ---------- Press / release paths ----------

    def _handle_press(self, channel, button_name):
        """Falling edge: do short-press action and arm long-press timer."""
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

        timer = threading.Timer(LONG_PRESS_DURATION, self._long_press_timeout, args=(channel, button_name))
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()

        # Immediate short-press feedback
        self.sound_player.play("Click")
        if self.state_machine:
            self.state_machine.transition(f"State{button_name}")

    def _handle_release(self, _channel, button_name):
        """Rising edge: cancel pending long-press timer (if any)."""
        timer = self.long_press_timers.pop(button_name, None)
        if timer:
            timer.cancel()
        # Short-press behavior already executed on falling edge.

    def _long_press_timeout(self, channel, button_name):
        """Fire long-press if still held after LONG_PRESS_DURATION."""
        if GPIO.input(channel) != GPIO.LOW:
            return  # released during wait; ignore

        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)

        # Run (overridable) handler asynchronously
        threading.Thread(target=self._run_long_press_handler, args=(button_name,), daemon=True).start()

    def _run_long_press_handler(self, button_name):
        """Invoker for the public long_press_handler attribute."""
        self.long_press_handler(button_name)

    # ---------- Default actions ----------

    def _default_long_press_handler(self, button_name):
        """Default long-press behavior if not overridden (only Play is bound)."""
        if self.state_machine and button_name == "Play":
            self.state_machine.start_webservice()
        else:
            oradio_log.info("LONG press on %s (no default action)", button_name)

    # ---------- Self-test ----------

    def selftest(self) -> bool:
        """Read each configured pin once; return True if all are HIGH/LOW."""
        try:
            for name, pin in BUTTONS.items():
                level = GPIO.input(pin)
                if level not in (GPIO.LOW, GPIO.HIGH):
                    oradio_log.error(
                        "TouchButtons selftest: invalid level on %s (BCM%d): %r", name, pin, level
                    )
                    return False
                oradio_log.debug(
                    "TouchButtons selftest: %s (BCM%d) level=%s",
                    name, pin, "LOW" if level == GPIO.LOW else "HIGH",
                )
            oradio_log.info("TouchButtons selftest OK")
            return True
        except RuntimeError as err:
            # Typical RPi.GPIO hardware access error
            oradio_log.error("TouchButtons selftest FAILED: %s", err)
            return False


# ------------------ Standalone Test (compact) ------------------
if __name__ == "__main__":
    # pylint: disable=missing-class-docstring,missing-function-docstring
    import sys

    # Optional LED support for test mode (narrow exception)
    try:
        from led_control import LEDControl
        LED_CTRL = LEDControl()
    except ImportError:
        LED_CTRL = None

    class DummyStateMachine:
        def __init__(self):
            self.state = "StateIdle"
        def transition(self, new_state):
            print(f"[DummyStateMachine] Transition: {self.state} → {new_state}")
            self.state = new_state

    def explain():
        print(
            "\nTouchButtons – Standalone Test\n"
            "1) Self-test\n"
            "2) Live button test (LED blinks 0.1s if available)\n"
            "3) Exit\n"
        )

    def cleanup(touch: TouchButtons):
        for timer in list(touch.long_press_timers.values()):
            timer.cancel()
        GPIO.cleanup()

    def led_name(btn: str) -> str:
        return "LED" + btn  # e.g., 'Play' -> 'LEDPlay'

    def blink_led(btn: str):
        if not LED_CTRL:
            return
        LED_CTRL.turn_on_led(led_name(btn))
        time.sleep(0.1)
        LED_CTRL.turn_off_all_leds()

    def do_selftest(touch: TouchButtons):
        print("\nRunning self-test...")
        print("✅ PASSED\n" if touch.selftest() else "❌ FAILED\n")

    def do_live_test(touch: TouchButtons):
        print(
            "\nLive test: press buttons (Ctrl+C to stop). "
            "Short/long presses print; LED blinks if present.\n"
        )
        # Patch handlers so defaults (that may hit state machine) are bypassed
        original_long = getattr(touch, "long_press_handler", None)
        original_press = getattr(touch, "_handle_press")

        def on_long_press(name: str):
            print(f"[LONG]  {name}")
            blink_led(name)

        def patched_handle_press(channel, name):
            original_press(channel, name)  # run normal short-press mechanics
            print(f"[SHORT] {name}")
            blink_led(name)

        touch.long_press_handler = on_long_press
        setattr(touch, "_handle_press", patched_handle_press)

        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nStopping live test…\n")
        finally:
            if original_long is not None:
                touch.long_press_handler = original_long
            setattr(touch, "_handle_press", original_press)

    # --- menu ---
    SM = DummyStateMachine()
    TB = TouchButtons(state_machine=SM)
    explain()

    while True:
        choice = input("Select (1=self, 2=live, 3=exit): ").strip()
        if choice == "1":
            do_selftest(TB)
        elif choice == "2":
            do_live_test(TB)
        elif choice == "3":
            break
        else:
            print("Invalid option.\n")

    cleanup(TB)
    sys.exit(0)
