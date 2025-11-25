#!/usr/bin/env python3
"""
WARNING: NEEDS GPIO REWIRING ADAPTER
touch-buttons_responce_test.py — KPI-based responsiveness test (precise timing, silent by default)

Measures
--------
1) Detection (hit/miss): did a SHORT press get registered?
2) Latency: time from forcing the line LOW to the SHORT callback firing.

Fixed pulse widths tested: [100, 50, 25, 10, 5] ms

KPIs
----
A) KPIRESPONCETIME (default 100 ms):
   For REQUIRED widths (100, 50, 25 ms), for every button:
     * All repeats detected (no misses), and
     * Median latency <= KPIRESPONCETIME

B) KPIRESPONCEWIDTH (default 25 ms):
   The smallest tested pulse width at which *every* button achieves 100% detections
   (hits == repeats) must be <= KPIRESPONCEWIDTH.

Wiring for isolated test
------------------------
- EACH button net: RPi GPIO-IN (PUD_UP) + ~10k→3V3
- Simulator: RPi GPIO-OUT → 2k2 series → SAME net (idle HIGH, press = OUT drives LOW)

Tip: Stop any running service before testing:
  sudo systemctl stop oradio.service || sudo systemctl stop oradio-control.service

Examples
--------
# default (silent, KPI time=25 ms, KPI width=25 ms)
python3 touch-buttons_responce_test.py

# show per-press latencies and shrink bounce window to 1 ms
python3 touch-buttons_responce_test.py --verbose --bounce-ms 1

# if you've patched touch_buttons to allow true no-debounce (BOUNCE_MS=0)
python3 touch-buttons_responce_test.py --bounce-ms 0
"""

# --- make Oradio3/Python importable no matter where we run from ---
import os, sys
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CODE_DIR  = os.path.join(REPO_ROOT, 'Python')
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
# run with: python3 ../Tests/xxx.py    
# -----------------------------------------------------------------

import time
import argparse
import statistics
import threading
import importlib
from collections import defaultdict
from RPi import GPIO

# --- Import your touch buttons module/class ---
TB = importlib.import_module("touch_buttons")
TouchButtons = TB.TouchButtons

# --- Button maps (BCM) ---
INPUTS = {   # must match touch_buttons.BUTTONS wiring
    "Play":    9,
    "Preset1": 11,
    "Preset2": 5,
    "Preset3": 10,
    "Stop":    6,
}
SIM_OUT = {  # simulator OUT pins with 2k2 series resistors
    "Play":    27,
    "Preset1": 13,
    "Preset2": 26,
    "Preset3": 17,
    "Stop":    16,
}

# --- Test sequence & KPI widths ---
WIDTHS_MS = [100.0, 50.0, 25.0, 10.0, 5.0]
REQUIRED_WIDTHS = [100.0, 50.0, 25.0]  # widths that must meet the TIME KPI

# --- Measurement state ---
lock = threading.Lock()
pending_t0_ns = {name: None for name in INPUTS}  # last LOW start time per button
latencies_ms = defaultdict(list)                 # per width+button during a sweep
hits = defaultdict(int)                          # per width+button during a sweep


# --- Always-precise sleep: coarse sleep + final busy-wait (or pure spin for tiny waits) ---
def sleep_precise(seconds: float):
    if seconds <= 0:
        return
    # For long waits, sleep most of it, then spin ~2–3 ms for accuracy
    if seconds >= 0.010:
        target = time.perf_counter() + seconds
        coarse = seconds - 0.003
        if coarse > 0:
            time.sleep(coarse)
        while time.perf_counter() < target:
            pass
    else:
        # For short waits, pure spin
        target = time.perf_counter() + seconds
        while time.perf_counter() < target:
            pass


def setup_sim_outputs():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in SIM_OUT.values():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)


def cancel_pending_long_timers(tb: TouchButtons):
    # Defensive: prevents stray long-presses if the rising edge is coalesced by debounce
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
    # Longs are not part of this test; ignore them.
    def _cb(_btn: str):
        pass
    return _cb


def sim_press(btn_name: str, hold_s: float):
    """Drive simulator OUT LOW, record t0, hold, then release (precise timing)."""
    out_pin = SIM_OUT[btn_name]
    with lock:
        pending_t0_ns[btn_name] = time.perf_counter_ns()
    GPIO.output(out_pin, GPIO.LOW)
    sleep_precise(hold_s)
    GPIO.output(out_pin, GPIO.HIGH)


def median_or_none(vals):
    return statistics.median(vals) if vals else None


def run_sequence(tb: TouchButtons, order, repeats, widths_ms, gap_s, settle_s, verbose):
    """
    Run the width sequence, collect latencies and hits.

    Includes a live progress indicator:
      [Width  50.0 ms] ▓▓▓▓▓▓▓▓▓▓ 25/25   (spinner)
    """
    results = {}  # width -> {button -> dict(hits, total, lat_min, lat_med, lat_max)}
    spin = "|/-\\"
    for width in widths_ms:
        # reset per-width capture
        with lock:
            for k in list(latencies_ms.keys()):
                latencies_ms[k].clear()
            for b in order:
                hits[b] = 0
                pending_t0_ns[b] = None

        total_pulses = len(order) * repeats
        done = 0

        if verbose:
            print(f"\n[TEST] Width {width:.1f} ms — {repeats} repeats per button")

        w_s = width / 1000.0
        for btn in order:
            for _ in range(repeats):
                sim_press(btn, w_s)
                sleep_precise(settle_s)
                cancel_pending_long_timers(tb)  # defensive for tiny pulses
                sleep_precise(gap_s)
                # --- progress indicator (lightweight) ---
                done += 1
                frac = done / total_pulses
                bar_len = 10
                filled = int(round(frac * bar_len))
                bar = "▓" * filled + "░" * (bar_len - filled)
                spinner = spin[done % len(spin)]
                print(
                    f"\r[Width {width:5.1f} ms] {bar} {done:>2d}/{total_pulses:<2d}  {spinner}",
                    end="",
                    flush=True,
                )
        print("")  # newline after each width

        # compile per-width stats
        width_result = {}
        for btn in order:
            L = latencies_ms[btn][:]
            width_result[btn] = {
                "hits": hits[btn],
                "total": repeats,
                "lat_min": min(L) if L else None,
                "lat_med": median_or_none(L),
                "lat_max": max(L) if L else None,
            }
        results[width] = width_result
    return results


def print_table(order, results):
    print("\nRESULTS (hits/repeats and median latency per width)")
    header = "width | " + "  ".join(f"{b:>8s}" for b in order)
    print(header)
    print("-" * len(header))
    for width in WIDTHS_MS:
        row = [f"{width:5.1f} |"]
        wr = results.get(width, {})
        for b in order:
            r = wr.get(b, {})
            med = r.get("lat_med")
            med_s = f"{med:5.1f}ms" if med is not None else "   n/a"
            row.append(f"{r.get('hits',0)}/{r.get('total',0)}:{med_s:>7s}")
        print("  ".join(row))


def summarize_and_judge(order, results, kpi_ms, kpi_width_ms):
    """Summarize both KPIs and print verdicts."""
    # --- KPI A: TIME (median latency) on REQUIRED_WIDTHS ---
    overall_time_pass = True
    print("\nKPI SUMMARY — TIME")
    print(
        f"KPIRESPONCETIME: ≤ {kpi_ms:.1f} ms (median) "
        f"on widths: {', '.join(f'{w:.1f}' for w in REQUIRED_WIDTHS)}"
    )
    for width in REQUIRED_WIDTHS:
        wr = results[width]
        all_ok = True
        for b in order:
            r = wr[b]
            hits_ok = (r["hits"] == r["total"])
            med_ok = (r["lat_med"] is not None and r["lat_med"] <= kpi_ms)
            if not (hits_ok and med_ok):
                all_ok = False
        print(f"  Width {width:5.1f} ms -> {'PASS' if all_ok else 'FAIL'}")
        overall_time_pass &= all_ok

    # --- KPI B: WIDTH (minimum pulse width that yields 100% detection for all buttons) ---
    print("\nKPI SUMMARY — WIDTH")
    ok_widths = []
    for width in WIDTHS_MS:
        wr = results[width]
        if all(wr[b]["hits"] == wr[b]["total"] for b in order):
            ok_widths.append(width)
    if ok_widths:
        achieved_width = min(ok_widths)  # smallest tested width that fully passes detection
        width_pass = (achieved_width <= kpi_width_ms)
        print(f"KPIRESPONCEWIDTH: ≤ {kpi_width_ms:.1f} ms "
              f"(achieved: {achieved_width:.1f} ms) -> {'PASS' if width_pass else 'FAIL'}")
    else:
        achieved_width = float("inf")
        width_pass = False
        print(f"KPIRESPONCEWIDTH: ≤ {kpi_width_ms:.1f} ms "
              f"(achieved: >{max(WIDTHS_MS):.1f} ms) -> FAIL")

    overall_pass = (overall_time_pass and width_pass)
    print(
        f"\nOverall KPI verdict: "
        f"{'✅ PASS' if overall_pass else '❌ FAIL'}"
    )
    return overall_pass


def main():
    ap = argparse.ArgumentParser(
        description="KPI responsiveness test for TouchButtons (precise timing, silent by default)"
    )
    ap.add_argument("--kpi-ms",       type=float, default=25.0,
                    help="KPIRESPONCETIME threshold (ms)")
    ap.add_argument("--kpi-width-ms", type=float, default=25.0,
                    help="KPIRESPONCEWIDTH threshold (ms)")
    ap.add_argument("--repeats",   type=int,   default=5,
                    help="Repeats per button per width")
    ap.add_argument("--gap-ms",    type=float, default=700.0,
                    help="Gap between pulses on same button (ms)")
    ap.add_argument("--settle-ms", type=float, default=100.0,
                    help="Post-callback settle time (ms)")
    # Silent is now the default; use --sound to enable click sounds
    ap.add_argument("--sound",     action="store_true",
                    help="Enable system click sounds during test (default: silent)")
    ap.add_argument("--bounce-ms", type=int,   default=None,
                    help="Override touch_buttons.BOUNCE_MS at runtime (e.g., 1 or 0 if patched)")
    ap.add_argument("--buttons",   type=str,   default="Play,Preset1,Preset2,Preset3,Stop",
                    help="Comma-separated buttons to test")
    ap.add_argument("--verbose",   action="store_true",
                    help="Print per-press SHORT latencies")
    args = ap.parse_args()

    # Optional: override debounce gate in the module
    if args.bounce_ms is not None:
        try:
            TB.BOUNCE_MS = int(args.bounce_ms)
            print(f"[INFO] touch_buttons.BOUNCE_MS overridden to {TB.BOUNCE_MS} ms")
        except Exception as e:
            print(f"[WARN] Failed to override BOUNCE_MS: {e}")

    # Build order safely
    order = [b.strip() for b in args.buttons.split(",") if b.strip() in INPUTS and b.strip() in SIM_OUT]
    if not order:
        raise SystemExit("No valid buttons to test. Check --buttons and maps.")

    setup_sim_outputs()

    # Wire TouchButtons with our callbacks
    on_press = {n: on_short_factory(n, args.verbose) for n in order}
    on_long  = {n: on_long_factory(n, args.verbose) for n in order}

    # Silent by default; --sound enables clicks
    if args.sound:
        tb = TouchButtons(on_press=on_press, on_long_press=on_long)
    else:
        class _Silent:  # tiny shim to keep same code path without audio
            def play(self, *_a, **_k): pass
        tb = TouchButtons(on_press=on_press, on_long_press=on_long, sound_player=_Silent())

    print("\nTouchButtons — KPI responsiveness test (precise timing, silent by default)")
    print("Mapping (BCM):")
    for n in order:
        print(f"  {n:8s}: IN={INPUTS[n]:<2d}  OUT={SIM_OUT[n]:<2d}")
    print(f"[INFO] Using BOUNCE_MS={getattr(TB,'BOUNCE_MS','?')} ms")

    ok = tb.selftest()
    print(f"\n[SELFTEST] {'OK' if ok else 'FAIL'}\n")

    results = run_sequence(
        tb,
        order=order,
        repeats=args.repeats,
        widths_ms=WIDTHS_MS,
        gap_s=args.gap_ms / 1000.0,
        settle_s=args.settle_ms / 1000.0,
        verbose=args.verbose,
    )

    print_table(order, results)
    summarize_and_judge(order, results, kpi_ms=args.kpi_ms, kpi_width_ms=args.kpi_width_ms)

    cleanup(tb)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cleanup()