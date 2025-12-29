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
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio touch buttons module with debounce, per-button callbacks, and selftest
"""

import time
import threading
from typing import Callable

from RPi import GPIO
from oradio_logging import oradio_log
from system_sounds import play_sound

# -------- LOCAL constants --------
BUTTON_DEBOUNCE_TIME = 500          # ms, ignore rapid repeats
DEBOUNCE_SECONDS = BUTTON_DEBOUNCE_TIME / 1000.0
BOUNCE_MS = 10                      # hardware debounce in GPIO.add_event_detect

BUTTONS: dict[str, int] = {
    "Play": 9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop": 6,
}

LONG_PRESS_DURATION = 6  # seconds


class TouchButtons:
    """
    Handle GPIO-based touch buttons with debounce, short-press callbacks,
    and long-press callbacks. This class has **no knowledge** of the state machine.
    """

    OnPress = Callable[[], None]
    OnLongPress = Callable[[str], None]  # receives the button name (e.g. "Play")

    def __init__(self, on_press=None, on_long_press=None) -> None:
        """
        Args:
            on_press: dict mapping button name -> zero-arg callback (short press).
            on_long_press: dict mapping button name -> callback(button_name) (long press).
        """
        # Callbacks
        self._on_press: dict[str, TouchButtons.OnPress] = on_press or {}
        self._on_long_press: dict[str, TouchButtons.OnLongPress] = on_long_press or {}

        # Press tracking
        self.button_press_times: dict[str, float] = {}   # button -> press start (monotonic)
        self.last_trigger_times: dict[str, float] = {}   # button -> last accepted press time
        self.long_press_timers: dict[str, threading.Timer] = {}  # button -> Timer

        # Fast channel -> name lookup
        self.gpio_to_button = {pin: name for name, pin in BUTTONS.items()}

        self._setup_gpio()

    # ---------- Public wiring helpers ----------

    def set_press_callback(self, button_name: str, callback):
        """Register or clear the short-press callback for a specific button."""
        if callback is None:
            self._on_press.pop(button_name, None)
        else:
            self._on_press[button_name] = callback

    def set_long_press_callback(self, button_name: str, callback):
        """Register or clear the long-press callback for a specific button."""
        if callback is None:
            self._on_long_press.pop(button_name, None)
        else:
            self._on_long_press[button_name] = callback

    # ---------- GPIO setup / edge handling ----------

    def _setup_gpio(self) -> None:
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
            GPIO.add_event_detect(
                pin, GPIO.BOTH, callback=self._edge_callback, bouncetime=BOUNCE_MS
            )

    def _edge_callback(self, channel: int) -> None:
        """Unified handler for both press (falling) and release (rising) edges."""
        button_name = self.gpio_to_button.get(channel)
        if not button_name:
            return

        if GPIO.input(channel) == GPIO.LOW:
            self._handle_press(channel, button_name)
        else:
            self._handle_release(channel, button_name)

    # ---------- Press / release paths ----------

    def _handle_press(self, channel: int, button_name: str) -> None:
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

        timer = threading.Timer(
            LONG_PRESS_DURATION, self._long_press_timeout, args=(channel, button_name)
        )
        timer.daemon = True
        self.long_press_timers[button_name] = timer
        timer.start()

        # Immediate short-press feedback
        play_sound("Click")

        # Invoke short-press callback if present
        callback = self._on_press.get(button_name)
        if callback:
            try:
                callback()
            except Exception:  # pylint: disable=broad-exception-caught
                # Broad on purpose: we don't want a subscriber bug to kill GPIO callback threads.
                oradio_log.exception("TouchButtons: short-press callback failed for %s", button_name)

    def _handle_release(self, _channel: int, button_name: str) -> None:
        """Rising edge: cancel pending long-press timer (if any)."""
        timer = self.long_press_timers.pop(button_name, None)
        if timer:
            timer.cancel()
        # Short-press behavior already executed on falling edge.

    def _long_press_timeout(self, channel: int, button_name: str) -> None:
        """Fire long-press if still held after LONG_PRESS_DURATION."""
        if GPIO.input(channel) != GPIO.LOW:
            return  # released during wait; ignore

        # Disarm any timer entry; we’re executing now
        self.long_press_timers.pop(button_name, None)

        # Run long-press handler asynchronously (don’t block GPIO thread)
        threading.Thread(
            target=self._invoke_long_press, args=(button_name,), daemon=True
        ).start()

    def _invoke_long_press(self, button_name: str) -> None:
        """Invoker for long-press callbacks."""
        callback = self._on_long_press.get(button_name)
        if callback is None:
            oradio_log.info("LONG press on %s (no callback wired)", button_name)
            return
        try:
            callback(button_name)
        except Exception:  # pylint: disable=broad-exception-caught
            # Broad on purpose: external callback error must not kill our worker.
            oradio_log.exception("TouchButtons: long-press callback failed for %s", button_name)

    # ---------- Self-test ----------

    def selftest(self) -> bool:
        """Read each configured pin once; return True if all are HIGH/LOW."""
        try:
            for name, pin in BUTTONS.items():
                level = GPIO.input(pin)
                if level not in (GPIO.LOW, GPIO.HIGH):
                    oradio_log.error(
                        "TouchButtons selftest: invalid level on %s (BCM%d): %r",
                        name, pin, level
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


# ------------------ Standalone Test (no state machine) ------------------
if __name__ == "__main__":
    # pylint: disable=missing-class-docstring,missing-function-docstring
    import sys

    # Ensure clean slate before any GPIO users (helps when re-running standalone)
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.cleanup()
    except (RuntimeError, OSError):
        # Ignore cleanup errors; GPIO may already be in use or not initialized
        pass

    # Optional LED support for test mode (defensive: don't crash if busy)
    try:
        from led_control import LEDControl
        try:
            LED_CTRL = LEDControl()
        except (RuntimeError, OSError) as e:
            # GPIO busy or HW init error; continue without LEDs in standalone mode
            print(f"[Standalone] LEDControl unavailable ({e}). Continuing without LEDs.")
            LED_CTRL = None
    except ImportError:
        LED_CTRL = None

    def led_name(btn: str) -> str:
        return "LED" + btn  # e.g., 'Play' -> 'LEDPlay'

    def blink_led(btn: str) -> None:
        if not LED_CTRL:
            return
        LED_CTRL.turn_on_led(led_name(btn))
        time.sleep(0.1)
        LED_CTRL.turn_off_all_leds()

    def on_press_factory(name: str):
        def _cb() -> None:
            print(f"[SHORT] {name}")
            blink_led(name)
        return _cb

    def on_long_factory(name: str):
        # inner arg is intentionally ignored -> name is captured from the outer scope
        def _cb(_: str) -> None:
            print(f"[LONG]  {name}")
            blink_led(name)
        return _cb

    _touch_buttons = TouchButtons(
        on_press={n: on_press_factory(n) for n in BUTTONS},
        on_long_press={"Play": on_long_factory("Play")},  # demo: only Play has long-press
    )

    print("\nTouchButtons – Standalone Test")
    print("Press buttons (Ctrl+C to exit). Short/long presses print; LED blinks if present.\n")

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for t in list(_touch_buttons.long_press_timers.values()):
            t.cancel()
        GPIO.cleanup()
        sys.exit(0)
