#!/usr/bin/env python3
"""
Automated test script for mpd_control.py

Runs predefined test cases against MPDControl methods and reports
PASS/FAIL with timing metrics and optional KPI tracking.
"""

import time
import traceback
from mpd_control import MPDControl
from oradio_logging import oradio_log

# --- Test KPI structure ---
class TestResult:
    def __init__(self, name):
        self.name = name
        self.start = time.time()
        self.success = False
        self.error = None
        self.duration = 0

    def stop(self, success=True, error=None):
        self.duration = round(time.time() - self.start, 3)
        self.success = success
        self.error = error

    def summary(self):
        status = "PASS" if self.success else "FAIL"
        msg = f"{status:<6} {self.name:<30} {self.duration:>6.2f}s"
        if self.error:
            msg += f" | Error: {self.error}"
        return msg


# --- Core Test Runner ---
class MPDTester:
    def __init__(self):
        self.client = MPDControl()
        self.results = []

    def run_test(self, name, func, *args, **kwargs):
        result = TestResult(name)
        try:
            func(*args, **kwargs)
            result.stop(success=True)
        except Exception as e:
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

    def kpi_report(self):
        print("\n--- KPI Metrics ---")
        durations = [r.duration for r in self.results]
        if durations:
            avg_time = sum(durations) / len(durations)
            max_time = max(durations)
            print(f"Average response time: {avg_time:.2f}s")
            print(f"Longest test duration: {max_time:.2f}s")


# --- Define Test Cases ---
def run_all_tests():
    tester = MPDTester()

    # Basic MPD connectivity
    tester.run_test("Connect to MPD", tester.client._connect_client)

    # Core playback controls
    tester.run_test("Play (no preset)", tester.client.play)
    tester.run_test("Pause playback", tester.client.pause)
    tester.run_test("Play (no preset)", tester.client.play)
    tester.run_test("Next track", tester.client.next)
    tester.run_test("Play (no preset)", tester.client.play)
    tester.run_test("Stop playback", tester.client.stop)

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

    # Summary
    tester.summary()
    tester.kpi_report()


# --- Entry point ---
if __name__ == "__main__":
    print("Starting automated MPDControl tests...\n")
    run_all_tests()