#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on October 13, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Race-condition tests for the StateMachine in oradio_control.py.

    Runs anywhere: no hardware, no MPD, no message bus. oradio_control.py
    cannot be imported -- importing it starts the whole Oradio -- so the
    StateMachine class, run_later() and the module constants are taken out of
    its source with the ast module and run against fakes for MPD, the LEDs
    and the sounds. Timers run 20x faster.

    Covers:
      - a worker overtaken while waiting for task_lock is skipped
      - nothing plays again after StateError
      - a delayed transition that already fired loses to a button press
      - a handler from an older generation cannot arm a timer
      - an LED-off timer leaves a newer state's blink alone
      - presets pressed before the library is ready play once, the last one
      - concurrent transitions and timers raise nothing

    Run:
        python3 -m unittest state_machine_race_test -v
        python3 state_machine_race_test.py
"""
import ast
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any

# The code under test; found relative to this file so the test runs from anywhere.
SOURCE = Path(__file__).resolve().parent.parent / "Main" / "oradio_control.py"

# All timers run this much faster than in production.
SCALE = 0.05

# What the fakes record: (kind, *args) tuples, in call order.
Call = tuple[Any, ...]


class _FastTimer(threading.Timer):
    """threading.Timer with its interval scaled down by SCALE."""

    def __init__(self, interval: float, *args: Any, **kwargs: Any) -> None:
        super().__init__(interval * SCALE, *args, **kwargs)


class _Recorder:
    """Shared call and log record for all fakes."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.log: list[tuple[str, str]] = []

    def clear(self) -> None:
        """Forget everything recorded so far."""
        self.calls.clear()
        self.log.clear()


class _FakeLog:
    """Stands in for oradio_log: every level records (level, message)."""

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    def __getattr__(self, level: str) -> Any:
        def _log(msg: str, *args: Any) -> None:
            self._recorder.log.append((level, msg % args if args else msg))
        return _log


class _FakeLeds:
    """Stands in for LEDControl: every method records ("led", name, *args)."""

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        def _led(*args: Any) -> None:
            self._recorder.calls.append(("led", name, *args))
        return _led


class _FakeMpd:
    """Stands in for MPDControl, with a configurable play() delay and result."""

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder
        self.slow = 0.0
        self.ready = threading.Event()
        self.play_result: bool | None = True
        self.queued = False

    def play(self, preset: str | None = None) -> bool | None:
        """Record the call, take self.slow seconds, return play_result."""
        self._recorder.calls.append(("play", preset))
        time.sleep(self.slow)
        # Like MPD: play() clears the queue and fills it only with music.
        self.queued = bool(self.play_result)
        return self.play_result

    def has_queue(self) -> bool:
        """Whether the last play() left something in the queue."""
        return self.queued

    def stop(self) -> None:
        """Record a stop."""
        self._recorder.calls.append(("stop",))

    def pause(self) -> None:
        """Record a pause."""
        self._recorder.calls.append(("pause",))

    def next(self) -> None:
        """Record a next."""
        self._recorder.calls.append(("next",))

    def is_webradio(self, preset: str | None = None) -> bool:
        """No preset is a web radio here."""
        del preset
        return False

    def wait_for_library(self, timeout: float | None = None) -> bool:
        """Wait on self.ready, with the timeout scaled like every other timer."""
        # 0 means "just ask", as in MPDControl; only None waits for ever.
        return self.ready.wait(None if timeout is None else timeout * SCALE)


def _extract_nodes(tree: ast.Module) -> list[ast.stmt]:
    """Return the StateMachine class, run_later() and the UPPER_CASE constants."""
    keep: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StateMachine":
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "run_later":
            keep.append(node)
        elif isinstance(node, ast.Assign) and all(
            isinstance(t, ast.Name) and t.id.isupper() and t.id != "HANDLERS" for t in node.targets
        ):
            keep.append(node)
    return keep


def _load(recorder: _Recorder) -> tuple[Any, dict[str, Any]]:
    """
    Build the StateMachine class against fakes.

    Returns:
        The StateMachine class and the namespace it runs in, so tests can
        reach the fakes (mpd_control, usb_present) through it.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    keep = _extract_nodes(tree)

    fake_threading = types.SimpleNamespace(
        **{name: getattr(threading, name) for name in dir(threading) if not name.startswith("__")}
    )
    fake_threading.Timer = _FastTimer

    usb_present = threading.Event()
    usb_present.set()
    usb_known = threading.Event()
    usb_known.set()

    namespace: dict[str, Any] = {
        "threading": fake_threading,
        "leds": _FakeLeds(recorder),
        "mpd_control": _FakeMpd(recorder),
        "oradio_log": _FakeLog(recorder),
        "play_sound": lambda sound: recorder.calls.append(("sound", sound)),
        "usb_present": usb_present,
        "usb_known": usb_known,
        "startup_idle_held": threading.Event(),
        "web_service_active": threading.Event(),
        "oradio_web_service": types.SimpleNamespace(stop=lambda: recorder.calls.append(("websvc_stop",))),
        "oradio_wifi_service": types.SimpleNamespace(has_connectivity=lambda: True),
        "has_internet": lambda: True,
        "PLAY_STATES": {"StatePlay", "StatePreset1", "StatePreset2", "StatePreset3"},
        "PRESETS_BY_STATE": {"StatePreset1": "Preset1", "StatePreset2": "Preset2", "StatePreset3": "Preset3"},
    }

    # Constants the extracted code uses but does not define itself (LED and
    # sound names imported from elsewhere): their own name is a fine value.
    defined = {
        target.id
        for node in keep if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    for node in ast.walk(ast.Module(body=keep, type_ignores=[])):
        if isinstance(node, ast.Name) and node.id.isupper() and node.id not in namespace and node.id not in defined:
            namespace[node.id] = node.id

    # pylint: disable=exec-used; running the extracted source is the point:
    # the module itself cannot be imported without starting the Oradio.
    exec(compile(ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[])), str(SOURCE), "exec"), namespace)

    namespace["PRESETS"] = {
        "Preset1": ("LED_PRESET1", "SOUND_1"),
        "Preset2": ("LED_PRESET2", "SOUND_2"),
        "Preset3": ("LED_PRESET3", "SOUND_3"),
    }
    return namespace["StateMachine"], namespace


# pylint: disable=protected-access; the races live in the state machine's internals
class StateMachineRaceTest(unittest.TestCase):
    """Interleavings that used to break the state machine."""

    recorder: _Recorder
    state_machine_class: Any
    namespace: dict[str, Any]
    mpd: _FakeMpd

    @classmethod
    def setUpClass(cls) -> None:
        cls.recorder = _Recorder()
        cls.state_machine_class, cls.namespace = _load(cls.recorder)
        cls.mpd = cls.namespace["mpd_control"]

    def setUp(self) -> None:
        self.recorder.clear()
        self.mpd.slow = 0.0
        self.mpd.play_result = True
        self.mpd.queued = False
        self.mpd.ready.set()
        self.namespace["usb_present"].set()

    def _machine(self, state: str = "StateIdle") -> Any:
        """A fresh state machine in `state`."""
        machine = self.state_machine_class()
        machine.state = state
        return machine

    def _plays(self) -> list[Any]:
        """The presets play() was called with, in order."""
        return [call[1] for call in self.recorder.calls if call[0] == "play"]

    def test_overtaken_worker_is_skipped(self) -> None:
        """Preset1 then Preset2 queued behind a slow handler: only Preset2 plays."""
        machine = self._machine()
        self.mpd.slow = 0.3
        machine.transition("StatePlay")
        time.sleep(0.05)                    # slow handler now holds task_lock
        machine.transition("StatePreset1")
        machine.transition("StatePreset2")
        time.sleep(1.0)
        self.assertEqual(self._plays(), [None, "Preset2"])

    def test_no_play_after_state_error(self) -> None:
        """A Play worker queued before StateError never runs after it."""
        machine = self._machine()
        self.mpd.slow = 0.3
        machine.transition("StatePreset1")
        time.sleep(0.05)
        machine.transition("StatePlay")
        machine.transition("StateError")
        time.sleep(1.0)
        sequence = [
            call[0] if call[0] != "play" else f"play:{call[1]}"
            for call in self.recorder.calls if call[0] in ("play", "stop")
        ]
        self.assertEqual(sequence, ["play:Preset1", "stop"])

    def test_fired_timer_loses_to_button(self) -> None:
        """A delayed transition already firing while Play is pressed does nothing."""
        machine = self._machine("StateStop")
        machine._handler_generation = machine._generation
        with machine._lock:
            machine._arm_delayed_transition("StopToIdle", 4.0, "StateIdle", from_state="StateStop")
            time.sleep(4.0 * SCALE + 0.1)   # fired; its callback now waits for _lock
            machine.transition("StatePlay")
        time.sleep(0.3)
        self.assertEqual(machine.state, "StatePlay")

    def test_stale_handler_does_not_arm(self) -> None:
        """A handler from an older generation cannot arm a timer."""
        machine = self._machine("StateStop")
        machine._handler_generation = machine._generation - 1
        machine._arm_delayed_transition("StopToIdle", 4.0, "StateIdle", from_state="StateStop")
        self.assertEqual(machine._delayed_timers, {})

    def test_led_timer_leaves_newer_blink_alone(self) -> None:
        """Stop's LED-off timer does not switch off a USB-absent blink started after it."""
        machine = self._machine("StatePlay")
        machine.transition("StateStop")
        time.sleep(0.05)
        self.namespace["usb_present"].clear()
        machine.transition("StateUSBAbsent")
        time.sleep(4 * SCALE + 0.3)
        offs = [
            call for call in self.recorder.calls
            if call[:2] == ("led", "turn_off_led") and call[2] == "LED_STOP"
        ]
        self.assertEqual(offs, [])

    def test_one_retry_plays(self) -> None:
        """Preset1, Preset2, Preset1 before the library is ready: Preset1 plays once."""
        machine = self._machine()
        self.mpd.ready.clear()
        self.mpd.play_result = None
        for state in ("StatePreset1", "StatePreset2", "StatePreset1"):
            machine.transition(state)
            time.sleep(0.1)
        self.recorder.clear()
        self.mpd.play_result = True
        self.mpd.ready.set()
        time.sleep(0.5)
        retries = [t.name for t in threading.enumerate() if t.name.startswith("preset-retry")]
        self.assertEqual(self._plays(), ["Preset1"])
        self.assertEqual(retries, [])

    def _sounds(self) -> list[Any]:
        """The sounds played, in order."""
        return [call[1] for call in self.recorder.calls if call[0] == "sound"]

    def test_empty_preset_pressed_again(self) -> None:
        """An empty preset pressed twice says "no music" twice, never "next song"."""
        machine = self._machine()
        self.mpd.play_result = False
        machine.transition("StatePreset1")
        time.sleep(0.3)
        machine.transition("StatePreset1")
        time.sleep(0.3)
        self.assertEqual(self._plays(), ["Preset1", "Preset1"])
        self.assertEqual(self._sounds().count("SOUND_PRESET_EMPTY"), 2)
        self.assertNotIn("SOUND_NEXT", self._sounds())
        self.assertNotIn(("next",), self.recorder.calls)

    def test_playing_preset_pressed_again_is_next(self) -> None:
        """A preset with music pressed again still skips to the next song."""
        machine = self._machine()
        machine.transition("StatePreset1")
        time.sleep(0.3)
        machine.transition("StatePreset1")
        time.sleep(0.1)
        self.assertEqual(self._plays(), ["Preset1"])
        self.assertIn(("next",), self.recorder.calls)
        self.assertIn("SOUND_NEXT", self._sounds())

    def test_empty_default_preset_on_play(self) -> None:
        """Play with an empty default preset says "no music", and again on a second press."""
        machine = self._machine()
        self.mpd.play_result = False
        machine.transition("StatePlay")
        time.sleep(0.3)
        machine.transition("StatePlay")
        time.sleep(0.3)
        self.assertEqual(self._plays(), [None, None])
        self.assertEqual(self._sounds().count("SOUND_PRESET_EMPTY"), 2)
        self.assertNotIn("SOUND_NEXT", self._sounds())

    def test_playing_play_pressed_again_is_next(self) -> None:
        """Play pressed again while music plays still skips to the next song."""
        machine = self._machine()
        machine.transition("StatePlay")
        time.sleep(0.3)
        machine.transition("StatePlay")
        time.sleep(0.1)
        self.assertEqual(self._plays(), [None])
        self.assertIn("SOUND_NEXT", self._sounds())
        self.assertNotIn("SOUND_PRESET_EMPTY", self._sounds())

    def test_play_before_library_ready(self) -> None:
        """Play pressed during the boot scan plays once the library is ready."""
        machine = self._machine()
        self.mpd.ready.clear()
        machine.transition("StatePlay")
        time.sleep(0.2)
        self.assertEqual(self._plays(), [])
        self.mpd.ready.set()
        time.sleep(0.5)
        self.assertEqual(self._plays(), [None])
        self.assertIn(("led", "turn_on_led", "LED_PLAY"), self.recorder.calls)

    def test_concurrent_transitions(self) -> None:
        """Three threads hammering transition() raise nothing and log no errors."""
        machine = self._machine("StateStop")
        self.mpd.slow = 0.001
        errors: list[str] = []

        def hammer(states: list[str]) -> None:
            try:
                for i in range(200):
                    machine.transition(states[i % len(states)])
            # Broad catch: any exception at all is the failure being tested for.
            except Exception as ex_err:     # pylint: disable=broad-exception-caught
                errors.append(repr(ex_err))

        threads = [
            threading.Thread(target=hammer, args=(states,))
            for states in (["StateStop", "StatePlay"], ["StateStartUp", "StateIdle"], ["StatePreset1", "StateStop"])
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        time.sleep(1.0)
        self.assertEqual(errors, [])
        self.assertEqual([entry for entry in self.recorder.log if entry[0] == "error"], [])


if __name__ == "__main__":
    unittest.main()
