#!/usr/bin/env python3
"""
Automated KPI-based test script for mpd_control.py

- Functional tests + KPI evaluation (pass rate, avg/max duration, connect time, retry count)
- Stress Test (sequential rapid commands) + KPIs
- High-CPU Stress Test (CPU burners via multiprocessing) + KPIs
- Multithreaded Stress Test (concurrent calls) + KPIs
"""
# --- make Oradio3/Python importable no matter where we run from ---
import os, sys
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CODE_DIR  = os.path.join(REPO_ROOT, 'Python')
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
# run with: python3 ~/Oradio3/Tests/mpd_control_test.py
# in ~/Oradio3/Python 
# ----



import os
import re
import time
import random
import traceback
import threading
import multiprocessing as mp

from mpd_control import MPDControl
from oradio_logging import oradio_log

# ---------- Paths ----------
ORADIO_LOG = "/home/pi/Oradio3/logging/oradio.log"

# ---------- KPI thresholds ----------
KPI_THRESHOLDS = {
    "functional_pass_rate": 100.0,  # %
    "connect_time": 1.0,            # seconds
    "avg_duration": 0.5,            # seconds
    "max_duration": 2.0,            # seconds
    "max_retries": 0,               # count, within functional test window
}

STRESS_THRESHOLDS = {
    "avg_per_action": 0.12,         # seconds/action
    "max_per_action": 0.50,         # seconds/action
    "errors": 0,
    "retries": 0,
}

STRESS_CPU_THRESHOLDS = {
    "avg_per_action": 0.20,         # seconds/action (more lenient)
    "max_per_action": 1.00,         # seconds/action
    "errors": 0,
    "retries": 0,
}

STRESS_MT_THRESHOLDS = {
    "avg_per_action": 0.10,         # seconds/action
    "max_per_action": 0.60,         # seconds/action
    "errors": 0,
    "retries": 0,
}

# ---------- Small helpers ----------
def p95(values):
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(0.95 * (len(s) - 1)))))
    return s[idx]

# ---------- Test KPI result holder ----------
class TestResult:
    def __init__(self, name):
        self.name = name
        self.start = time.time()
        self.success = False
        self.error = None
        self.duration = 0.0

    def stop(self, success=True, error=None):
        self.duration = round(time.time() - self.start, 3)
        self.success = success
        self.error = error

    def summary(self):
        status = "PASS" if self.success else "FAIL"
        msg = f"{status:<6} {self.name:<35} {self.duration:>6.2f}s"
        if self.error:
            msg += f" | Error: {self.error}"
        return msg

# ---------- Core Test Runner ----------
class MPDTester:
    def __init__(self):
        self.client = MPDControl()
        self.results = []

    def run_test(self, name, func, *args, **kwargs):
        result = TestResult(name)
        try:
            func(*args, **kwargs)
            result.stop(success=True)
        except Exception as e:  # pylint: disable=broad-except
            oradio_log.error("Test %s failed: %s", name, e)
            result.stop(success=False, error=str(e))
            traceback.print_exc()
        self.results.append(result)

    def summary(self):
        print("\n=== MPD TEST SUMMARY ===")
        for r in self.results:
            print(r.summary())
        total = len(self.results)
        passed = sum(r.success for r in self.results)
        failed = total - passed
        print(f"\nTotal tests: {total} | Passed: {passed} | Failed: {failed}")
        return total, passed, failed

    def kpi_report(self, exclude_patterns=None):
        """
        Compute KPI metrics for functional tests, excluding tests that match any pattern
        (e.g., 'stress'), so stress results don't skew functional KPIs.
        """
        exclude_patterns = exclude_patterns or ["stress"]
        def _is_excluded(name):
            lname = name.lower()
            return any(p in lname for p in exclude_patterns)

        filtered = [r for r in self.results if not _is_excluded(r.name)]
        durations = [r.duration for r in filtered]
        avg_time = (sum(durations) / len(durations)) if durations else 0.0
        max_time = max(durations) if durations else 0.0
        connect_time = next((r.duration for r in filtered if "connect" in r.name.lower()), 0.0)

        print("\n--- KPI Metrics (Functional) ---")
        print(f"Average response time: {avg_time:.2f}s")
        print(f"Longest test duration: {max_time:.2f}s")
        print(f"MPD connect time: {connect_time:.2f}s")
        return {
            "avg_time": avg_time,
            "max_time": max_time,
            "connect_time": connect_time,
        }

    @staticmethod
    def check_log_for_retries(logfile=ORADIO_LOG, start_time=None, end_time=None):
        """
        Count retry messages within [start_time, end_time] window.
        If end_time is None, counts all lines newer than start_time.
        Expects log lines starting with: 'YYYY-MM-DD HH:MM:SS,'
        """
        if start_time is None:
            return 0
        try:
            with open(logfile, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return 0

        retry_count = 0
        for line in lines:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if not m:
                continue
            try:
                ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except Exception:  # pylint: disable=broad-except
                continue
            if ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue
            if "Retry " in line:
                retry_count += 1
        return retry_count

# ---------- CPU burn helpers ----------
def _cpu_burn_worker(stop_event: mp.Event):
    x = 0
    while not stop_event.is_set():
        for i in range(200_000):
            x = (x * 33 + i) % 1_000_003  # keep CPU busy (prevents trivial optimization)

def start_cpu_burners(workers: int):
    """
    Start 'workers' CPU-bound processes. Returns (procs, stop_event).
    Call stop_event.set(); then join each proc to stop them.
    """
    stop_event = mp.Event()
    procs = []
    for _ in range(max(1, workers)):
        p = mp.Process(target=_cpu_burn_worker, args=(stop_event,), daemon=True)
        p.start()
        procs.append(p)
    return procs, stop_event

def stop_cpu_burners(procs, stop_event):
    stop_event.set()
    for p in procs:
        try:
            p.join(timeout=2.0)
        except Exception:
            pass

def get_loadavg():
    try:
        return os.getloadavg()  # (1,5,15)
    except Exception:
        return (0.0, 0.0, 0.0)

# ---------- Stress Tests ----------
def stress_test_sequential(client, iterations=100, delay=0.02):
    """
    Rapid-fire core commands sequentially.
    Returns metrics dict with per-action timing stats and error count.
    """
    actions = [
        client.play,
        client.pause,
        client.stop,
        client.next,
        client.clear,
        lambda: client.play("Preset1"),
        lambda: client.play("Preset2"),
        lambda: client.play("Preset3"),
    ]
    errors = 0
    per_action_times = []

    start = time.time()
    for i in range(iterations):
        action = random.choice(actions)
        t0 = time.perf_counter()
        try:
            action()
        except Exception as e:  # pylint: disable=broad-except
            errors += 1
            oradio_log.error("Stress action error at i=%d: %s", i, e)
        t1 = time.perf_counter()
        per_action_times.append(t1 - t0)
        if delay:
            time.sleep(delay)
    total = time.time() - start

    return {
        "iterations": iterations,
        "total": total,
        "avg": (sum(per_action_times) / len(per_action_times)) if per_action_times else 0.0,
        "min": min(per_action_times) if per_action_times else 0.0,
        "max": max(per_action_times) if per_action_times else 0.0,
        "p95": p95(per_action_times),
        "errors": errors,
        "per_action_times": per_action_times,
    }

def stress_test_sequential_high_cpu(client, iterations=100, delay=0.02, workers=None):
    """
    Run the sequential rapid command stress test **while** CPU burners load the system.
    Returns metrics dict incl. loadavg before/after and worker count.
    """
    if workers is None:
        cores = os.cpu_count() or 1
        workers = max(1, cores - 1)

    load_before = get_loadavg()

    procs, stop_evt = start_cpu_burners(workers)
    time.sleep(0.1)  # let burners ramp up a touch

    actions = [
        client.play,
        client.pause,
        client.stop,
        client.next,
        client.clear,
        lambda: client.play("Preset1"),
        lambda: client.play("Preset2"),
        lambda: client.play("Preset3"),
    ]
    errors = 0
    per_action_times = []

    start = time.time()
    for i in range(iterations):
        t0 = time.perf_counter()
        try:
            random.choice(actions)()
        except Exception as e:  # pylint: disable=broad-except
            errors += 1
            oradio_log.error("High-CPU stress action error at i=%d: %s", i, e)
        t1 = time.perf_counter()
        per_action_times.append(t1 - t0)
        if delay:
            time.sleep(delay)
    total = time.time() - start

    stop_cpu_burners(procs, stop_evt)
    time.sleep(0.1)  # let loadavg reflect
    load_after = get_loadavg()

    return {
        "iterations": iterations,
        "workers": workers,
        "total": total,
        "avg": (sum(per_action_times) / len(per_action_times)) if per_action_times else 0.0,
        "min": min(per_action_times) if per_action_times else 0.0,
        "max": max(per_action_times) if per_action_times else 0.0,
        "p95": p95(per_action_times),
        "errors": errors,
        "per_action_times": per_action_times,
        "load_before": load_before,
        "load_after": load_after,
    }

def stress_test_multithreaded(client, threads=4, actions_per_thread=50, delay_max=0.03):
    """
    Fire commands from multiple threads concurrently.
    Returns metrics dict with timing stats and error count.
    """
    actions = [
        client.play,
        client.pause,
        client.stop,
        client.next,
        client.clear,
        lambda: client.play("Preset1"),
        lambda: client.play("Preset2"),
        lambda: client.play("Preset3"),
    ]

    per_action_times = []
    errors = 0
    lock = threading.Lock()

    def worker(tid: int):
        nonlocal errors
        local_times = []
        for i in range(actions_per_thread):
            t0 = time.perf_counter()
            try:
                random.choice(actions)()
            except Exception as e:  # pylint: disable=broad-except
                with lock:
                    errors += 1
                oradio_log.error("MT stress error t=%d i=%d: %s", tid, i, e)
            t1 = time.perf_counter()
            local_times.append(t1 - t0)
            if delay_max:
                time.sleep(random.uniform(0.0, delay_max))
        with lock:
            per_action_times.extend(local_times)

    start = time.time()
    ts = [threading.Thread(target=worker, args=(t, ), daemon=True) for t in range(threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    total = time.time() - start

    return {
        "threads": threads,
        "iterations": threads * actions_per_thread,
        "total": total,
        "avg": (sum(per_action_times) / len(per_action_times)) if per_action_times else 0.0,
        "min": min(per_action_times) if per_action_times else 0.0,
        "max": max(per_action_times) if per_action_times else 0.0,
        "p95": p95(per_action_times),
        "errors": errors,
        "per_action_times": per_action_times,
    }

# ---------- Define and run tests ----------
def run_all_tests():
    functional_start = time.time()
    tester = MPDTester()

    # Helper: ensure we're playing before calling an action (e.g., next/stop/pause)
    def play_then(action_name, action_fn, settle=0.05):
        def _inner():
            tester.client.play()
            time.sleep(settle)
            action_fn()
        tester.run_test(action_name, _inner)

    # Core connectivity
    tester.run_test("Connect to MPD", tester.client._connect_client)

    # Basic playback control (ordered + conditioned to exercise real transitions)
    tester.run_test("Play (no preset)", tester.client.play)
    play_then("Next track (while playing)", tester.client.next)
    play_then("Stop playback (while playing)", tester.client.stop)
    play_then("Pause playback (while playing)", tester.client.pause)

    # Preset playback
    for preset in ["Preset1", "Preset2", "Preset3"]:
        tester.run_test(f"Play {preset}", tester.client.play, preset=preset)

    # Playlist management
    tester.run_test("List directories", tester.client.get_directories)
    tester.run_test("List playlists", tester.client.get_playlists)
    tester.run_test("Add playlist dummy", tester.client.add, "TestList", None)
    tester.run_test("Remove playlist dummy", tester.client.remove, "TestList", None)

    # Database and search
    tester.run_test("Update database", tester.client.update_database)
    tester.run_test("Search song (pattern)", tester.client.search, "love")

    # Webradio detection
    tester.run_test("Check is_webradio (current)", tester.client.is_webradio)
    tester.run_test("Check is_webradio (preset1)", tester.client.is_webradio, "Preset1")

    # Songs listing
    tester.run_test("Get songs (Preset1)", tester.client.get_songs, "Preset1")

    # -------- Functional Reporting --------
    total, passed, failed = tester.summary()
    kpis = tester.kpi_report(exclude_patterns=["stress"])  # stress excluded by name anyway
    functional_end = time.time()
    functional_retries = tester.check_log_for_retries(
        logfile=ORADIO_LOG, start_time=functional_start, end_time=functional_end
    )

    # -------- Functional KPI Evaluation --------
    print("\n--- KPI EVALUATION (Functional) ---")
    all_ok = True

    pass_rate = (passed / total) * 100 if total else 0.0
    ok = pass_rate >= KPI_THRESHOLDS["functional_pass_rate"]
    print(f"{'✅' if ok else '❌'} Functional pass rate: {pass_rate:.1f}% (target {KPI_THRESHOLDS['functional_pass_rate']}%)")
    all_ok &= ok

    def kpi_line(label, value, limit, comparator, unit="s"):
        ok_local = comparator(value, limit)
        sym = "✅" if ok_local else "❌"
        msg = f"{sym} {label}: {value:.2f}{unit} (target ≤ {limit}{unit})" if unit else f"{sym} {label}: {value} (target ≤ {limit})"
        print(msg)
        return ok_local

    all_ok &= kpi_line("MPD connect", kpis["connect_time"], KPI_THRESHOLDS["connect_time"], lambda v, l: v <= l)
    all_ok &= kpi_line("Avg response time", kpis["avg_time"], KPI_THRESHOLDS["avg_duration"], lambda v, l: v <= l)
    all_ok &= kpi_line("Max response time", kpis["max_time"], KPI_THRESHOLDS["max_duration"], lambda v, l: v <= l)

    ok_retries = functional_retries <= KPI_THRESHOLDS["max_retries"]
    print(f"{'✅' if ok_retries else '❌'} Retry count: {functional_retries} (target ≤ {KPI_THRESHOLDS['max_retries']})")
    all_ok &= ok_retries

    print("\n🎯 All functional KPI targets met!" if all_ok else "\n⚠️ One or more functional KPI targets failed!")

    # -------- Separate Stress Test (Sequential) --------
    print("\nRunning stress test (sequential rapid commands)...")
    stress_start = time.time()
    stress = stress_test_sequential(tester.client, iterations=100, delay=0.02)
    stress_end = time.time()
    stress_retries = tester.check_log_for_retries(
        logfile=ORADIO_LOG, start_time=stress_start, end_time=stress_end
    )

    print("\n--- STRESS TEST REPORT (Sequential) ---")
    print(f"Actions: {stress['iterations']}")
    print(f"Total time: {stress['total']:.2f}s")
    print(f"Avg per action: {stress['avg']:.3f}s  |  Min: {stress['min']:.3f}s  |  Max: {stress['max']:.3f}s  |  P95: {stress['p95']:.3f}s")
    print(f"Errors: {stress['errors']}")
    print(f"Retry count in window: {stress_retries}")

    print("\n--- KPI EVALUATION (Stress) ---")
    stress_ok = True
    def stress_kpi(label, value, limit):
        ok_l = value <= limit
        sym = "✅" if ok_l else "❌"
        print(f"{sym} {label}: {value:.3f}s (target ≤ {limit}s)")
        return ok_l

    stress_ok &= stress_kpi("Avg per action", stress["avg"], STRESS_THRESHOLDS["avg_per_action"])
    stress_ok &= stress_kpi("Max per action", stress["max"], STRESS_THRESHOLDS["max_per_action"])
    ok_err = (stress["errors"] <= STRESS_THRESHOLDS["errors"])
    print(f"{'✅' if ok_err else '❌'} Action errors: {stress['errors']} (target ≤ {STRESS_THRESHOLDS['errors']})")
    stress_ok &= ok_err
    ok_ret = (stress_retries <= STRESS_THRESHOLDS["retries"])
    print(f"{'✅' if ok_ret else '❌'} Retry count: {stress_retries} (target ≤ {STRESS_THRESHOLDS['retries']})")
    stress_ok &= ok_ret
    print("\n🎯 Stress test KPIs met!" if stress_ok else "\n⚠️ Stress test KPIs failed!")

    # -------- High CPU Load Stress Test --------
    print("\nRunning stress test under high CPU load...")
    stress_cpu_start = time.time()
    stress_cpu = stress_test_sequential_high_cpu(tester.client, iterations=100, delay=0.02, workers=None)
    stress_cpu_end = time.time()
    stress_cpu_retries = tester.check_log_for_retries(
        logfile=ORADIO_LOG, start_time=stress_cpu_start, end_time=stress_cpu_end
    )

    lb1, lb5, lb15 = stress_cpu["load_before"]
    la1, la5, la15 = stress_cpu["load_after"]
    print("\n--- STRESS TEST REPORT (Sequential + High CPU) ---")
    print(f"Workers: {stress_cpu['workers']}  |  Actions: {stress_cpu['iterations']}")
    print(f"Loadavg before: 1m={lb1:.2f} 5m={lb5:.2f} 15m={lb15:.2f}")
    print(f"Loadavg after : 1m={la1:.2f} 5m={la5:.2f} 15m={la15:.2f}")
    print(f"Total time: {stress_cpu['total']:.2f}s")
    print(f"Avg per action: {stress_cpu['avg']:.3f}s  |  Min: {stress_cpu['min']:.3f}s  |  Max: {stress_cpu['max']:.3f}s  |  P95: {stress_cpu['p95']:.3f}s")
    print(f"Errors: {stress_cpu['errors']}")
    print(f"Retry count in window: {stress_cpu_retries}")

    print("\n--- KPI EVALUATION (Stress + High CPU) ---")
    def kpi_line3(label, value, limit):
        ok_l = value <= limit
        sym = "✅" if ok_l else "❌"
        print(f"{sym} {label}: {value:.3f}s (target ≤ {limit}s)")
        return ok_l

    stress_cpu_ok = True
    stress_cpu_ok &= kpi_line3("Avg per action", stress_cpu["avg"], STRESS_CPU_THRESHOLDS["avg_per_action"])
    stress_cpu_ok &= kpi_line3("Max per action", stress_cpu["max"], STRESS_CPU_THRESHOLDS["max_per_action"])
    ok_err2 = (stress_cpu["errors"] <= STRESS_CPU_THRESHOLDS["errors"])
    print(f"{'✅' if ok_err2 else '❌'} Action errors: {stress_cpu['errors']} (target ≤ {STRESS_CPU_THRESHOLDS['errors']})")
    stress_cpu_ok &= ok_err2
    ok_ret2 = (stress_cpu_retries <= STRESS_CPU_THRESHOLDS["retries"])
    print(f"{'✅' if ok_ret2 else '❌'} Retry count: {stress_cpu_retries} (target ≤ {STRESS_CPU_THRESHOLDS['retries']})")
    stress_cpu_ok &= ok_ret2
    print("\n🎯 High-CPU stress test KPIs met!" if stress_cpu_ok else "\n⚠️ High-CPU stress test KPIs failed!")

    # -------- Multithreaded Stress Test --------
    print("\nRunning stress test (multithreaded)...")
    mt_start = time.time()
    mt = stress_test_multithreaded(tester.client, threads=4, actions_per_thread=50, delay_max=0.03)
    mt_end = time.time()
    mt_retries = tester.check_log_for_retries(
        logfile=ORADIO_LOG, start_time=mt_start, end_time=mt_end
    )

    print("\n--- STRESS TEST REPORT (Multithreaded) ---")
    print(f"Threads: {mt['threads']}  |  Actions: {mt['iterations']}")
    print(f"Total time: {mt['total']:.2f}s")
    print(f"Avg per action: {mt['avg']:.3f}s  |  Min: {mt['min']:.3f}s  |  Max: {mt['max']:.3f}s  |  P95: {mt['p95']:.3f}s")
    print(f"Errors: {mt['errors']}")
    print(f"Retry count in window: {mt_retries}")

    print("\n--- KPI EVALUATION (Stress + Multithreaded) ---")
    def kpi_line_mt(label, value, limit):
        ok = value <= limit
        sym = "✅" if ok else "❌"
        print(f"{sym} {label}: {value:.3f}s (target ≤ {limit}s)")
        return ok

    mt_ok = True
    mt_ok &= kpi_line_mt("Avg per action", mt["avg"], STRESS_MT_THRESHOLDS["avg_per_action"])
    mt_ok &= kpi_line_mt("Max per action", mt["max"], STRESS_MT_THRESHOLDS["max_per_action"])
    ok_err_mt = (mt["errors"] <= STRESS_MT_THRESHOLDS["errors"])
    print(f"{'✅' if ok_err_mt else '❌'} Action errors: {mt['errors']} (target ≤ {STRESS_MT_THRESHOLDS['errors']})")
    mt_ok &= ok_err_mt
    ok_ret_mt = (mt_retries <= STRESS_MT_THRESHOLDS["retries"])
    print(f"{'✅' if ok_ret_mt else '❌'} Retry count: {mt_retries} (target ≤ {STRESS_MT_THRESHOLDS['retries']})")
    mt_ok &= ok_ret_mt
    print("\n🎯 Multithreaded stress test KPIs met!" if mt_ok else "\n⚠️ Multithreaded stress test KPIs failed!")

# ---------- Entry ----------
if __name__ == "__main__":
    print("Starting automated MPDControl KPI tests...\n")
    run_all_tests()