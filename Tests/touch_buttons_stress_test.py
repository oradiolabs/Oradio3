#!/usr/bin/env python3
"""
touch-buttons_stress_test.py — High-CPU-load stress test for touch_buttons.py (final)

Purpose
-------
Exercise the Oradio touch button stack (GPIO -> RPi.GPIO -> touch_buttons.py -> callbacks)
under controlled CPU load while injecting simulated button pulses. We measure:

  • Hit/miss (timeouts)  — did the short-press callback arrive before the deadline?
  • Latency (ms)         — time from injected FALLING edge to short-press callback execution
  • Robustness knobs     — CPU burner processes, duty cycle, core isolation, debounce gaps

Quick wiring recap (for the simulator)
--------------------------------------
Each button net has:
  RPi GPIO-IN (PUD_UP, as in touch_buttons.BUTTONS)
  ↔ real touch IC output (IQS227 etc., *disconnect during these isolated tests*)
  ↔ 10k pull-up to 3V3

Simulator OUT (this script) drives the SAME net through ~2k2:
  RPi GPIO-OUT (SIM_OUT) -> 2k2 -> button net (LOW = press, HIGH = release)

Critical: if the real IC output is still connected and is *push-pull*, it may hold the net
HIGH and block the simulator. For clean isolated tests, disconnect the real IC.

What’s in here
--------------
- Default "realistic worst case" profile:
    width=100 ms, repeats=50 per button, gap=800 ms, callback-deadline=300 ms
    burners = max(1, cpu-1), duty=1.0, burner nice+15, auto core isolation
- Auto core isolation when workers>0 (test pinned to last core; burners to others)
- Optional --no-isolate to disable isolation; warns if running 100% duty without isolation
- Median latency KPI (<= 100 ms) + zero timeouts required for PASS
- Explicit timeout counting; progress indicator; per-button stats (median/p95/max)
- Precise sleeper to avoid OS scheduling noise in short waits

How latency is measured
-----------------------
Latency = time from our injected FALLING edge (setting OUT pin LOW) to the moment the
short-press callback runs in touch_buttons.py. This includes:
  • RPi.GPIO edge detection & its bouncetime gate (BOUNCE_MS, default 10 ms)
  • scheduling delay of the callback thread
  • Python overhead
Expect a floor around ~BOUNCE_MS + a few ms (your typical medians ≈ 11–12 ms are normal).

What a timeout means
--------------------
A timeout occurs if the short-press callback did not arrive within --cb-deadline-ms after the
press was injected. Reasons include excessive CPU contention, too-small gap (still in debounce),
or other system activity delaying callback execution.

Recommended workflow
--------------------
1) Stop Oradio services to prevent interference:
     sudo systemctl stop oradio.service || sudo systemctl stop oradio-control.service
2) Ensure real touch IC outputs are disconnected for isolated tests.
3) Run with defaults (heavy but realistic):
     python3 touch-buttons_stress_test.py
4) If you see timeouts at 100% duty, try/confirm isolation and niceness:
     python3 touch-buttons_stress_test.py --isolate --nice-burners 15
5) Tighten/loosen the scenario using --workers, --duty, --gap-ms, --width-ms.

Usage examples (mirroring successful runs we observed)
------------------------------------------------------
# Baseline, no extra load (should PASS cleanly):
python3 touch-buttons_stress_test.py --workers 0 --width-ms 100 --gap-ms 800 --cb-deadline-ms 300

# Moderate load with lower-priority burners (also PASSed):
python3 touch-buttons_stress_test.py --workers 2 --duty 0.5 --nice-burners 15 --width-ms 150 --gap-ms 800

# Realistic worst case: duty=1.0 with isolation (PASSed in tests):
python3 touch-buttons_stress_test.py --workers 2 --duty 1.0 --nice-burners 15 --isolate

Lessons learned (from experiments so far)
-----------------------------------------
• GAP matters: --gap-ms must exceed effective debounce windows and give the release edge time
  to settle. 800 ms is a safe guard for isolated testing.

• Isolation helps: With burner duty=1.0, isolating the test to one core and sending burners
  to the others avoids artificial timeouts due to CPU starvation.

• Niceness helps too: Raising burner niceness (e.g., +15) lowers their scheduling priority,
  improving callback timeliness.

• BOUNCE_MS != 0: RPi.GPIO rejects bouncetime=0. Keep ≥1 ms if you override. Default 10 ms is
  fine and explains ≈12 ms median latency floor.

• “Both edges” in touch_buttons: using GPIO.BOTH is acceptable for this design because short-
  press is actioned on FALLING; RISING is only used to cancel the long-press timer. Our tests
  cancel long timers between pulses to avoid leakage into the next step.

• Logs and sound: Enabling click sounds or very verbose logging adds jitter. The script runs
  silent by default; use --sound / --verbose only when needed.

• Deadline vs. loss: A larger --cb-deadline-ms (e.g., 300 ms) helps distinguish late callbacks
  from truly lost ones under heavy load.

Tip: If you ever observe sporadic misses with workers=0, verify no other services are using GPIO
or doing heavy I/O on the Pi, and ensure the real touch IC is not still driving the net.
"""
# --- make Oradio3/Python importable no matter where we run from ---
import os, sys
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CODE_DIR  = os.path.join(REPO_ROOT, 'Python')
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
    
# run with: python3 ../Tests/touch_buttons_stress_test.py    
# -----------------------------------------------------------------

import time
import random
import argparse
import statistics
import threading
import importlib
import multiprocessing as mp
from collections import defaultdict
from RPi import GPIO

# ---- Import your touch buttons module/class ----
TB = importlib.import_module("touch_buttons")
TouchButtons = TB.TouchButtons

# ---- Button maps (BCM) ----
# Must match touch_buttons.BUTTONS wiring
INPUTS = {
    "Play":    9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop":    6,
}
# Simulator OUT pins (through ~2k2 series to the same nets)
SIM_OUT = {
    "Play":    27,
    "Preset1": 13,
    "Preset2": 26,
    "Preset3": 17,
    "Stop":    16,
}

# ---- Defaults / KPIs ----
DEFAULT_WIDTH_MS = 100.0
DEFAULT_REPEATS_PER_BUTTON = 50
KPI_RESP_TIME_MS_DEFAULT = 100.0         # median latency threshold
DEBOUNCE_GUARD_GAP_MS = 800.0            # gap between pulses
SETTLE_AFTER_RELEASE_MS = 120.0          # small wait after release
CALLBACK_DEADLINE_MS = 300.0             # maximum wait for callback before timeout

# ---- Measurement state ----
lock = threading.Lock()
pending_t0_ns = {name: None for name in INPUTS}  # LOW start time per button
latencies_ms = defaultdict(list)                 # per button
hits = defaultdict(int)
timeouts = defaultdict(int)

# ---- Precise sleep helper ----
def sleep_precise(seconds: float):
    if seconds <= 0:
        return
    if seconds >= 0.010:
        target = time.perf_counter() + seconds
        coarse = seconds - 0.003
        if coarse > 0:
            time.sleep(coarse)
        while time.perf_counter() < target:
            pass
    else:
        target = time.perf_counter() + seconds
        while time.perf_counter() < target:
            pass

# ---- CPU load (multi-process) ----
def _burner(run_flag: mp.Event, duty: float, period_s: float, nice_inc: int, aff_cpus=None):
    # lower priority so test gets scheduled first
    try:
        if nice_inc:
            os.nice(int(nice_inc))
    except Exception:
        pass
    try:
        if aff_cpus:
            os.sched_setaffinity(0, set(aff_cpus))
    except Exception:
        pass

    x = 0x12345678
    while run_flag.is_set():
        t0 = time.perf_counter()
        busy_until = t0 + duty * period_s
        while time.perf_counter() < busy_until and run_flag.is_set():
            # tight compute loop
            x ^= (x << 13) & 0xFFFFFFFF
            x ^= (x >> 17)
            x ^= (x << 5) & 0xFFFFFFFF
        remaining = period_s - (time.perf_counter() - t0)
        if remaining > 0:
            time.sleep(remaining)

def start_cpu_load(workers: int, duty: float, period_s: float, nice_inc: int, isolate: bool):
    run_flag = mp.Event()
    run_flag.set()
    procs = []

    cpu_count = os.cpu_count() or 1
    all_cpus = list(range(cpu_count))
    burner_aff = None
    test_aff = None

    if isolate and cpu_count >= 2:
        # reserve last core for test; burners get the rest
        test_aff = [all_cpus[-1]]
        burner_aff = all_cpus[:-1]
        try:
            os.sched_setaffinity(0, set(test_aff))
            print(f"[ISO ] Pinned test to CPU(s): {test_aff}")
        except Exception as e:
            print(f"[ISO ] Affinity set failed for test (continuing): {e}")
            test_aff = None
            burner_aff = None

    for _ in range(max(0, workers)):
        p = mp.Process(target=_burner,
                       args=(run_flag, duty, period_s, nice_inc, burner_aff),
                       daemon=True)
        p.start()
        procs.append(p)
    if burner_aff:
        print(f"[ISO ] Pinned burners to CPU(s): {burner_aff}")
    return run_flag, procs

def stop_cpu_load(run_flag: mp.Event, procs):
    try:
        run_flag.clear()
    except Exception:
        pass
    for p in procs or []:
        try:
            p.join(timeout=1.0)
        except Exception:
            pass

# ---- GPIO helpers ----
def setup_sim_outputs():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in SIM_OUT.values():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

def cancel_pending_long_timers(tb: TouchButtons):
    for t in list(tb.long_press_timers.values()):
        try:
            t.cancel()
        except Exception:
            pass
    tb.long_press_timers.clear()

def cleanup(tb=None):
    try:
        for p in SIM_OUT.values():
            GPIO.output(p, GPIO.HIGH)
    except Exception:
        pass
    if tb is not None:
        cancel_pending_long_timers(tb)
    GPIO.cleanup()

# ---- Callbacks ----
def on_short_factory(btn_name: str, verbose: bool):
    def _cb():
        t_cb = time.perf_counter_ns()
        with lock:
            t0 = pending_t0_ns.get(btn_name)
            if t0 is not None:
                lat_ms = (t_cb - t0) / 1e6
                latencies_ms[btn_name].append(lat_ms)
                hits[btn_name] += 1
                pending_t0_ns[btn_name] = None
                if verbose:
                    print(f"[SHORT] {btn_name:8s}  latency={lat_ms:6.2f} ms")
            else:
                if verbose:
                    print(f"[WARN ] {btn_name:8s} short without pending press")
    return _cb

def on_long_factory(_btn_name: str, _verbose: bool):
    def _cb(_btn: str):
        # Long press not used in this stress test
        pass
    return _cb

# ---- Simulation press ----
def sim_press(btn_name: str, hold_s: float, cb_deadline_s: float):
    """Drive OUT LOW -> hold -> HIGH, wait up to cb_deadline for callback; count timeout if none."""
    out_pin = SIM_OUT[btn_name]
    with lock:
        pending_t0_ns[btn_name] = time.perf_counter_ns()
    GPIO.output(out_pin, GPIO.LOW)
    sleep_precise(hold_s)
    GPIO.output(out_pin, GPIO.HIGH)

    # Wait up to deadline for callback to clear pending_t0_ns
    deadline = time.perf_counter() + cb_deadline_s
    while time.perf_counter() < deadline:
        with lock:
            if pending_t0_ns[btn_name] is None:
                return
        time.sleep(0)
    # timeout -> count and clear
    with lock:
        if pending_t0_ns[btn_name] is not None:
            timeouts[btn_name] += 1
            pending_t0_ns[btn_name] = None

# ---- Sequence generation (debounce-safe & randomized) ----
def make_sequence(buttons, repeats_per_button):
    seq = []
    for _ in range(repeats_per_button):
        block = random.sample(buttons, k=len(buttons))
        seq.extend(block)
    return seq

# ---- Stats helpers ----
def p95(vals):
    if not vals:
        return None
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, int(0.95 * (len(s) - 1))))
    return s[idx]

def run_stress(tb: TouchButtons, buttons, width_ms, repeats_per_button, gap_ms, settle_ms, cb_deadline_ms, verbose):
    with lock:
        for k in buttons:
            pending_t0_ns[k] = None
            latencies_ms[k].clear()
            hits[k] = 0
            timeouts[k] = 0

    seq = make_sequence(buttons, repeats_per_button)
    total = len(seq)
    done = 0
    spin = "|/-\\"

    w_s       = width_ms / 1000.0
    gap_s     = gap_ms / 1000.0
    settle_s  = settle_ms / 1000.0
    deadline_s= cb_deadline_ms / 1000.0

    print(f"[INFO] Stress sequence: {total} pulses ({repeats_per_button} per button), width={width_ms:.1f} ms")
    for btn in seq:
        sim_press(btn, w_s, deadline_s)
        sleep_precise(settle_s)
        cancel_pending_long_timers(tb)
        sleep_precise(gap_s)
        done += 1
        # progress
        frac = done / total
        bar_len = 20
        filled = int(round(frac * bar_len))
        bar = "▓" * filled + "░" * (bar_len - filled)
        spinner = spin[done % len(spin)]
        print(f"\r[RUN ] {bar} {done:>4d}/{total:<4d}  {spinner}", end="", flush=True)
    print("")

def print_results(buttons, kpi_ms):
    print("\nRESULTS under CPU load")
    print("button   | hits/timeouts/total   median   p95     max   verdict")
    print("----------------------------------------------------------------")
    overall_pass = True
    for b in buttons:
        h  = hits[b]
        to = timeouts[b]
        total = h + to
        vals = latencies_ms[b]
        med = statistics.median(vals) if vals else None
        p95v = p95(vals)
        mx  = max(vals) if vals else None

        med_s = f"{med:6.1f}ms" if med is not None else "   n/a "
        p95_s = f"{p95v:6.1f}ms" if p95v is not None else "   n/a "
        mx_s  = f"{mx:6.1f}ms" if mx is not None else "   n/a "

        pass_btn = (to == 0) and (med is not None and med <= kpi_ms)
        overall_pass &= pass_btn
        print(f"{b:8s} | {h:3d}/{to:3d}/{total:<3d}     {med_s}  {p95_s}  {mx_s}   -> {'PASS' if pass_btn else 'FAIL'}")

    print("\nOverall verdict: " + ("✅ PASS" if overall_pass else "❌ FAIL"))
    return overall_pass

def main():
    ap = argparse.ArgumentParser(description="High-CPU-load stress test for touch_buttons.py (isolated wiring)")

    # Main knobs
    ap.add_argument("--width-ms",        type=float, default=DEFAULT_WIDTH_MS, help="Pulse width (ms)")
    ap.add_argument("--repeats",         type=int,   default=DEFAULT_REPEATS_PER_BUTTON, help="Triggers per button")
    ap.add_argument("--gap-ms",          type=float, default=DEBOUNCE_GUARD_GAP_MS, help="Gap between pulses (ms)")
    ap.add_argument("--settle-ms",       type=float, default=SETTLE_AFTER_RELEASE_MS, help="Post-release settle (ms)")
    ap.add_argument("--cb-deadline-ms",  type=float, default=CALLBACK_DEADLINE_MS, help="Max wait for callback before timeout (ms)")
    ap.add_argument("--kpi-ms",          type=float, default=KPI_RESP_TIME_MS_DEFAULT, help="Median latency KPI (ms)")

    # Load settings
    ap.add_argument("--workers",         type=int,   default=max(1, (os.cpu_count() or 2) - 1), help="CPU burner processes")
    ap.add_argument("--duty",            type=float, default=1.0, help="CPU burner duty cycle [0..1]")
    ap.add_argument("--nice-burners",    type=int,   default=15,  help="Niceness increment for burners (more => lower priority)")

    # Isolation: auto when workers>0 (unless user explicitly disables)
    iso = ap.add_mutually_exclusive_group()
    iso.add_argument("--isolate",    dest="isolate", action="store_true",  help="Pin test to one core; burners to the others")
    iso.add_argument("--no-isolate", dest="isolate", action="store_false", help="Do not isolate cores")
    ap.set_defaults(isolate=None)  # None => auto (enable if workers>0)

    # Misc
    ap.add_argument("--sound",           action="store_true",      help="Enable click sounds (default: silent)")
    ap.add_argument("--bounce-ms",       type=int,   default=None, help="Override touch_buttons.BOUNCE_MS at runtime (clamped ≥1)")
    ap.add_argument("--verbose",         action="store_true",      help="Print per-press latencies")

    args = ap.parse_args()

    # Optional debounce override (clamp to ≥1, as RPi.GPIO rejects 0)
    if args.bounce_ms is not None:
        try:
            val = int(args.bounce_ms)
            if val < 1:
                print(f"[INFO] Requested BOUNCE_MS={val} clamped to 1 ms")
                val = 1
            TB.BOUNCE_MS = val
            print(f"[INFO] touch_buttons.BOUNCE_MS overridden to {TB.BOUNCE_MS} ms")
        except Exception as e:
            print(f"[WARN] Failed to override BOUNCE_MS: {e}")

    # Auto-isolate when burners enabled unless user said --no-isolate
    if args.isolate is None:
        if args.workers > 0:
            args.isolate = True
            print("[ISO ] Auto-enabled core isolation (workers>0). Use --no-isolate to disable.")
        else:
            args.isolate = False

    if args.workers > 0 and args.duty >= 1.0 and not args.isolate:
        print("[WARN] Running 100% burner duty without isolation may cause artificial timeouts.")

    # Setup GPIO and TouchButtons wiring
    buttons = list(INPUTS.keys())
    setup_sim_outputs()
    on_press = {n: on_short_factory(n, args.verbose) for n in buttons}
    on_long  = {n: on_long_factory(n, args.verbose) for n in buttons}

    if args.sound:
        tb = TouchButtons(on_press=on_press, on_long_press=on_long)
    else:
        class _Silent:
            def play(self, *_a, **_k): pass
        tb = TouchButtons(on_press=on_press, on_long_press=on_long, sound_player=_Silent())

    # Profile header
    cpu_count = os.cpu_count() or 1
    print("\nTouchButtons — CPU load stress test")
    print("Mapping (BCM):")
    for n in buttons:
        print(f"  {n:8s}: IN={INPUTS[n]:<2d}  OUT={SIM_OUT[n]:<2d}")
    print(f"[INFO] Using BOUNCE_MS={getattr(TB,'BOUNCE_MS','?')} ms")
    ok = tb.selftest()
    print(f"[SELFTEST] {'OK' if ok else 'FAIL'}\n")

    print(f"[PROF] CPUs={cpu_count} | workers={args.workers} duty={args.duty:.2f} "
          f"nice+{args.nice_burners} | isolate={'yes' if args.isolate else 'no'} | "
          f"width={args.width_ms:.1f} ms gap={args.gap_ms:.0f} ms deadline={args.cb_deadline_ms:.0f} ms")

    # Start CPU load
    run_flag = None
    procs = []
    print(f"[LOAD] Starting {args.workers} worker(s) with duty={args.duty:.2f}, nice+{args.nice_burners} …")
    run_flag, procs = start_cpu_load(
        workers=args.workers,
        duty=max(0.0, min(1.0, args.duty)),
        period_s=0.01,
        nice_inc=args.nice_burners,
        isolate=args.isolate,
    )

    try:
        run_stress(
            tb,
            buttons=buttons,
            width_ms=args.width_ms,
            repeats_per_button=args.repeats,
            gap_ms=args.gap_ms,
            settle_ms=args.settle_ms,
            cb_deadline_ms=args.cb_deadline_ms,
            verbose=args.verbose,
        )
        print_results(buttons, kpi_ms=args.kpi_ms)
    finally:
        stop_cpu_load(run_flag, procs)
        cleanup(tb)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CTRL-C] Aborting …")