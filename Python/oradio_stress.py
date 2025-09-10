#!/usr/bin/env python3
"""
Oradio StateMachine stress driver.

Usage:
  python oradio_stress.py --min-delay 0.1 --max-delay 0.5 --max-transitions 20
  Press Q + Enter to stop early.
"""

import argparse
import random
import sys
import threading
import time

# Import the application module. This will initialize the services and state machine,
# but will NOT run app.main() because we are not executing oradio_control as __main__.
import oradio_control as app  # pylint: disable=wrong-import-position

STATES = [
    "StatePlay",
    "StatePreset1",
    "StatePreset2",
    "StatePreset3",
    "StateStop",
    "StateSpotifyConnect",
    "StatePlaySongWebIF",
    "StateUSBAbsent",
    "StateStartUp",
    "StateIdle",
    "StateWebService",
]


class StressController:
    """Hammer the state machine with random transitions on a background thread."""

    def __init__(self) -> None:
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._count = 0
        self._count_lock = threading.Lock()

    def _loop(self, min_d: float, max_d: float, max_count: int | None) -> None:
        """Worker loop that fires random transitions until stopped or limit reached."""
        while not self._stop_evt.is_set():
            nxt = random.choice(STATES)
            with self._count_lock:
                self._count += 1
                cnt = self._count

            print(f"[STRESS #{cnt}] → {nxt}   (threads: {threading.active_count()})")
            app.oradio_log.info("[STRESS #%s] → %s", cnt, nxt)

            app.state_machine.transition(nxt)

            if max_count is not None and cnt >= max_count:
                print(f"\n🛑 Reached max_transitions ({max_count}). Stopping stress test 🛑")
                self._stop_evt.set()
                break

            time.sleep(random.uniform(min_d, max_d))

        print(f"\n✅ Stress test finished. Total transitions: {self._count}")
        print(f"   Active threads now: {threading.active_count()}")

    def start(self, min_d: float, max_d: float, max_count: int | None) -> None:
        """Start the stress loop and a Q-listener to stop early."""
        self._thread = threading.Thread(
            target=self._loop, args=(min_d, max_d, max_count), daemon=True
        )
        self._thread.start()

        def _stop_on_q() -> None:
            while not self._stop_evt.is_set():
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip().lower() == "q":
                    print("\n🛑 Stopping STRESS test early via Q 🛑")
                    self._stop_evt.set()
                    return

        threading.Thread(target=_stop_on_q, daemon=True).start()

    def wait(self) -> None:
        """Block until the stress thread stops."""
        worker_thread = self._thread
        if worker_thread:
            worker_thread.join()


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the stress driver."""
    parser = argparse.ArgumentParser(description="Stress-test Oradio state machine")
    parser.add_argument("--min-delay", type=float, default=0.1, help="minimum delay between transitions")
    parser.add_argument("--max-delay", type=float, default=0.5, help="maximum delay between transitions")
    parser.add_argument("--max-transitions", type=int, default=20, help="auto-stop after this many transitions")
    return parser.parse_args()


def main() -> None:
    """Entry point for the stress driver."""
    args = parse_args()

    # Ensure the app started; state_machine is initialized at import time in app
    print("🔥 Starting STRESS test 🔥")
    print("Press Q + Enter at any time to stop the stress test early.\n")

    ctrl = StressController()
    ctrl.start(args.min_delay, args.max_delay, args.max_transitions)

    try:
        ctrl.wait()
    except KeyboardInterrupt:
        print("\n🛑 Stopping STRESS test via Ctrl-C 🛑")


if __name__ == "__main__":
    main()
