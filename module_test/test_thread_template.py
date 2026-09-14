#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on September 14, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Unit tests for ThreadTemplate's lifecycle hooks and crash self-restart.

    Runs anywhere: no hardware, no message bus, no network. The workers below are
    written for the test and do nothing but record what the template called.

    Covers what a subclass author relies on when setting restart_on_crash:
      - teardown() runs per attempt, on_stopped() once at the end
      - a worker that does not opt in still gets exactly one attempt
      - a crash budget that is spent gives up and reports
      - the budget decays, so an old crash does not count forever
      - a stop during the backoff cancels the restart
"""
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Main"))


def _stub_log_service() -> None:
    """
    Put a silent logger in place of log_service, before utilities imports it.

    The real one writes to the Oradio's own oradio.log on the device. These
    tests crash workers on purpose, so without this a single run leaves dozens
    of ERROR lines in the production log, from workers with names that appear
    nowhere in the code -- exactly the noise someone would be reading through
    during a real fault.

    test_wifi_service and test_rms_service stub log_service for the same
    reason; this keeps that consistent.
    """
    stub = types.ModuleType("log_service")

    class _Silent:
        """Accepts every logging call and does nothing with it."""

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    # setattr, not attribute assignment: mypy types a freshly created
    # ModuleType by what it declares, which for a stub is nothing.
    setattr(stub, "oradio_log", _Silent())
    setattr(stub, "ORADIO_LOG_LEVEL", "DEBUG")
    sys.modules["log_service"] = stub


_stub_log_service()

import utilities                        # noqa: E402  pylint: disable=wrong-import-position
from utilities import ThreadTemplate    # noqa: E402  pylint: disable=wrong-import-position

# The real constants are sized for a radio that must not thrash; these tests
# would take minutes at those values. Patched per test, never read at import.
FAST_BACKOFF = 0.02
FAST_WINDOW = 1.0

# Generous enough that a loaded CI box does not fail on timing, short enough
# that the suite stays quick.
SETTLE = 0.6


class _Recorder(ThreadTemplate):
    """
    Worker that records every hook the template calls.

    Args:
        crashes: How many do_work() calls raise before it starts succeeding.
                 Pass a large number for a worker that never recovers.
    """

    def __init__(self, crashes: int = 0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []
        self._crashes_left = crashes

    def setup(self) -> None:
        """Record that a fresh attempt began."""
        self.calls.append("setup")

    def do_work(self) -> None:
        """Raise while crashes remain, then record work."""
        if self._crashes_left > 0:
            self._crashes_left -= 1
            self.calls.append("crash")
            raise RuntimeError("deliberate")
        self.calls.append("work")

    def teardown(self) -> None:
        """Record the end of an attempt."""
        self.calls.append("teardown")

    def on_stopped(self) -> None:
        """Record that the worker ended for good."""
        self.calls.append("on_stopped")


class ThreadTemplateTestCase(unittest.TestCase):
    """Shared patching of the restart constants."""

    def setUp(self) -> None:
        """Shrink the backoff and window so the tests run in under a second."""
        self._originals = (
            utilities.CRASH_RESTART_BACKOFF,
            utilities.CRASH_RESTART_WINDOW,
        )
        utilities.CRASH_RESTART_BACKOFF = FAST_BACKOFF
        utilities.CRASH_RESTART_WINDOW = FAST_WINDOW

    def tearDown(self) -> None:
        """Put the real constants back, so one test cannot affect the next."""
        (
            utilities.CRASH_RESTART_BACKOFF,
            utilities.CRASH_RESTART_WINDOW,
        ) = self._originals

    @staticmethod
    def _attempts(worker: _Recorder) -> int:
        """How many times setup() ran, i.e. how many attempts were made."""
        return worker.calls.count("setup")


class TestHooks(ThreadTemplateTestCase):
    """teardown() and on_stopped() answer to different lifetimes."""

    def test_clean_stop_runs_each_hook_once(self):
        """A worker that is asked to stop tears down once and reports once."""
        worker = _Recorder(interval=0.02, name="clean")
        self.assertTrue(worker.safe_start(2))
        time.sleep(0.1)
        worker.safe_stop(2)

        self.assertEqual(worker.calls.count("teardown"), 1)
        self.assertEqual(worker.calls.count("on_stopped"), 1)
        self.assertEqual(worker.calls[-2:], ["teardown", "on_stopped"])

    def test_on_stopped_is_not_reported_between_attempts(self):
        """
        A worker that crashes and recovers reports nothing.

        The whole point of the split: teardown() runs per attempt, but
        on_stopped() must not announce a stop the next attempt undoes.
        """
        class Restarting(_Recorder):
            """A recorder that asks the template to restart it."""
            restart_on_crash = True

        worker = Restarting(crashes=2, interval=0.02, name="recovers")
        worker.safe_start(2)
        time.sleep(SETTLE)

        self.assertGreaterEqual(self._attempts(worker), 3)
        self.assertNotIn("on_stopped", worker.calls, "reported a stop it recovered from")
        self.assertIn("work", worker.calls, "never got past the crashes")

        worker.safe_stop(2)
        self.assertEqual(worker.calls.count("on_stopped"), 1)


class TestRestartOptIn(ThreadTemplateTestCase):
    """restart_on_crash is off unless a subclass asks for it."""

    def test_default_gets_one_attempt(self):
        """
        Without the opt-in a crash ends the worker, as it always did.

        This is the regression guard: the restart must not leak into classes
        whose author never checked that setup() can run twice.
        """
        worker = _Recorder(crashes=99, interval=0.02, name="default")
        worker.safe_start(2)
        time.sleep(SETTLE)

        self.assertEqual(self._attempts(worker), 1)
        self.assertEqual(worker.calls, ["setup", "crash", "teardown", "on_stopped"])
        self.assertTrue(worker.crashed)

    def test_budget_is_spent_and_then_reported(self):
        """A worker that never recovers gives up after the budget and reports once."""
        class Doomed(_Recorder):
            """A recorder that opts in but never recovers."""
            restart_on_crash = True

        worker = Doomed(crashes=99, interval=0.02, name="doomed")
        worker.safe_start(2)
        time.sleep(SETTLE)

        # One first attempt plus CRASH_RESTART_LIMIT restarts.
        self.assertEqual(self._attempts(worker), utilities.CRASH_RESTART_LIMIT + 1)
        self.assertEqual(worker.calls.count("on_stopped"), 1)
        self.assertTrue(worker.crashed, "the last exception must survive for the reporter")


class TestBudget(ThreadTemplateTestCase):
    """The budget decays, and a stop beats a pending restart."""

    def test_old_crashes_stop_counting(self):
        """
        A crash older than the window frees its slot again.

        Without this a subsystem that failed three times hours ago would be
        unrecoverable for the rest of the run.
        """
        class Restarting(_Recorder):
            """A recorder that asks the template to restart it."""
            restart_on_crash = True

        # The window has to be SHORTER than the backoff, or the next crash lands
        # inside it and the budget fills after all. That relationship is the
        # whole mechanism, so the test states it rather than picking numbers.
        utilities.CRASH_RESTART_BACKOFF = 0.08
        utilities.CRASH_RESTART_WINDOW = 0.04
        self.assertLess(utilities.CRASH_RESTART_WINDOW, utilities.CRASH_RESTART_BACKOFF)

        worker = Restarting(crashes=99, interval=0.02, name="decays")
        worker.safe_start(2)
        time.sleep(SETTLE)

        # With every crash ageing out immediately, the budget never fills, so
        # the worker keeps trying rather than giving up after LIMIT + 1.
        self.assertGreater(self._attempts(worker), utilities.CRASH_RESTART_LIMIT + 1)

        worker.safe_stop(2)

    def test_stop_during_backoff_cancels_the_restart(self):
        """A stop asked for while the backoff is running is honoured at once."""
        class Restarting(_Recorder):
            """A recorder that asks the template to restart it."""
            restart_on_crash = True

        utilities.CRASH_RESTART_BACKOFF = 5.0       # long enough to stop inside it

        worker = Restarting(crashes=99, interval=0.02, name="stopped")
        worker.safe_start(2)
        time.sleep(0.1)                             # let the first crash happen

        started = time.monotonic()
        worker.safe_stop(2)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 4.0, "safe_stop waited out the backoff")
        self.assertEqual(self._attempts(worker), 1, "restarted despite the stop")
        self.assertEqual(worker.calls.count("on_stopped"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
