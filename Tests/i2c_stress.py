#!/usr/bin/env python3
"""
I2C worst-case read-only stress for Oradio's I2CService.

- Spawns N threads that continuously call I2CService.read_byte/read_block
  against safe registers (no writes).
- Measures ops/sec, success/fail, and latency percentiles.
- Can optionally start an external "bus scanner" that probes many addresses
  with its own SMBus handle (bypassing I2CService lock) to simulate
  competing processes on the bus (use with care).

USAGE:
  python3 i2c_stress.py --duration 60 --threads 8
  python3 i2c_stress.py --duration 120 --threads 16 --external-scan

REQUIRES:
  - Your i2c_service.py accessible on PYTHONPATH / same folder.
  - smbus2 installed; run:  sudo apt-get install -y python3-smbus
"""
# --- make Oradio3/Python importable no matter where we run from ---
import os, sys
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CODE_DIR  = os.path.join(REPO_ROOT, 'Python')
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
# run with: python3 ~/Oradio3/Tests/i2c_stress.py
# in ~/Oradio3/Python 
# ----

import argparse
import random
import statistics
import threading
import time
from collections import defaultdict, deque

# Your modules
from i2c_service import I2CService
try:
    from smbus2 import SMBus  # optional for external scan
    HAS_SMBUS2 = True
except Exception:
    HAS_SMBUS2 = False


# ------- Targets (read-only, safe) -------
# name, addr, op, register, length (for block)
TARGETS = [
    ("MCP3021_DATA", 0x4D, "block", 0x00, 2),   # Oradio uses reg 0x00, len 2
    ("TSL2591_ID",   0x29, "byte",  0x12, 1),   # ID register (should be 0x50)
    ("TSL2591_ST",   0x29, "byte",  0x13, 1),   # STATUS register, read is safe
    # Intentionally non-existing (to exercise error handling)
    ("NACK_0x33",    0x33, "byte",  0x00, 1),
    ("NACK_0x51",    0x51, "block", 0x00, 4),
]

# (Optional) You may add more read-only targets you are certain are safe for your board.


def worker(service: I2CService, stop_evt: threading.Event, stats, latency_buf, throttle_ns):
    """
    Stress worker: perform random safe reads via I2CService until stop_evt is set.
    Collect per-target successes/failures and latencies (ns).
    """
    rnd = random.Random()
    while not stop_evt.is_set():
        name, addr, op, reg, length = rnd.choice(TARGETS)
        t0 = time.perf_counter_ns()

        ok = False
        try:
            if op == "byte":
                val = service.read_byte(addr, reg)
                ok = (val is not None)
            elif op == "block":
                data = service.read_block(addr, reg, length)
                ok = (data is not None and isinstance(data, list) and len(data) in (length,))  # length may vary if device NACKs
            else:
                pass
        except Exception:
            ok = False

        t1 = time.perf_counter_ns()
        dt = t1 - t0

        # record
        key = f"{name}@0x{addr:02X}"
        if ok:
            stats["ok"][key] += 1
            latency_buf.append(dt)
        else:
            stats["fail"][key] += 1

        # simple throttle if requested
        if throttle_ns > 0:
            # sleep the remaining time if this op was faster than the budget
            elapsed = dt
            if elapsed < throttle_ns:
                time.sleep((throttle_ns - elapsed) / 1e9)


def external_scanner(stop_evt: threading.Event, busno: int = 1, delay_s: float = 0.0):
    """
    Optional "other process" that rapidly probes many addresses using its own SMBus handle.
    This bypasses I2CService lock on purpose to simulate contention.
    Uses write_quick (some devices NACK; that's fine).
    """
    if not HAS_SMBUS2:
        return
    try:
        with SMBus(busno) as bus:
            addrs = [a for a in range(0x03, 0x78)]
            idx = 0
            while not stop_evt.is_set():
                addr = addrs[idx]
                idx = (idx + 1) % len(addrs)
                try:
                    # write_quick will often raise OSError; that's OK for stress
                    bus.write_quick(addr)
                except OSError:
                    pass
                if delay_s > 0.0:
                    time.sleep(delay_s)
    except Exception:
        # ignore fatal errors; stress is best-effort
        pass


def ns_to_ms(ns_vals):
    return [v / 1e6 for v in ns_vals]


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def main():
    ap = argparse.ArgumentParser(description="I2C read-only stress using I2CService")
    ap.add_argument("--duration", type=int, default=60, help="Test duration in seconds (default: 60)")
    ap.add_argument("--threads", type=int, default=8, help="Number of stress threads (default: 8)")
    ap.add_argument("--throttle-us", type=int, default=0,
                    help="Optional per-op budget in microseconds to avoid 100%% saturation (0 = unlimited)")
    ap.add_argument("--external-scan", action="store_true",
                    help="Start external scanner that probes addresses with its own SMBus handle (bypasses I2CService lock)")
    ap.add_argument("--scan-delay-ms", type=int, default=0,
                    help="Delay between external scan ops (ms). 0 = as fast as possible")
    args = ap.parse_args()

    print(f"== I2C stress starting: {args.threads} threads, {args.duration}s, throttle={args.throttle_us}µs, "
          f"external_scan={'on' if args.external_scan else 'off'} ==")

    service = I2CService()  # singleton
    stop_evt = threading.Event()

    # Shared stats
    stats = {
        "ok": defaultdict(int),
        "fail": defaultdict(int),
        "start_ts": time.monotonic(),
    }
    # Keep recent latencies to avoid unbounded memory (store last ~20000)
    latency_buf = deque(maxlen=20000)

    # Workers
    throttle_ns = args.throttle_us * 1000
    threads = []
    for _ in range(args.threads):
        t = threading.Thread(target=worker, args=(service, stop_evt, stats, latency_buf, throttle_ns), daemon=True)
        t.start()
        threads.append(t)

    # Optional external scanner
    if args.external_scan and HAS_SMBUS2:
        scan_delay = max(0.0, args.scan_delay_ms / 1000.0)
        tscan = threading.Thread(target=external_scanner, args=(stop_evt, 1, scan_delay), daemon=True)
        tscan.start()
        threads.append(tscan)
    elif args.external_scan and not HAS_SMBUS2:
        print("(!) smbus2 not available; external scan disabled")

    # Run
    end = time.monotonic() + args.duration
    try:
        while time.monotonic() < end:
            time.sleep(0.2)
    finally:
        stop_evt.set()
        for t in threads:
            t.join(timeout=3.0)

    elapsed = time.monotonic() - stats["start_ts"]
    total_ok = sum(stats["ok"].values())
    total_fail = sum(stats["fail"].values())
    total_ops = total_ok + total_fail

    lat_ms = sorted(ns_to_ms(list(latency_buf)))

    print("\n== I2C stress summary ==")
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Total ops: {total_ops}  |  OK: {total_ok}  |  FAIL: {total_fail}  |  OK rate: {total_ok/elapsed if elapsed>0 else 0:.1f}/s")
    if lat_ms:
        avg = statistics.mean(lat_ms)
        p50 = percentile(lat_ms, 50)
        p95 = percentile(lat_ms, 95)
        p99 = percentile(lat_ms, 99)
        worst = lat_ms[-1]
        print(f"Latency (ms): avg={avg:.3f}  p50={p50:.3f}  p95={p95:.3f}  p99={p99:.3f}  max={worst:.3f}")
    else:
        print("Latency: no successful ops recorded")

    print("\nPer-target results:")
    all_keys = sorted(set(list(stats["ok"].keys()) + list(stats["fail"].keys())))
    for k in all_keys:
        ok = stats["ok"][k]
        fail = stats["fail"][k]
        print(f"  {k:16s}  ok={ok:6d}  fail={fail:6d}")

    print("\nNOTE:")
    print(" - FAIL counts are expected for NACK_* targets and may increase if external scan is enabled.")
    print(" - If OK rate is very low or many timeouts occur, the bus may be saturated or devices are absent.")
    print(" - This script only performs reads; it will not change device state.")
    print("== done ==")


if __name__ == "__main__":
    main()