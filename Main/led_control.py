#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 29, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Oradio LED control module, controlling the LED:
    * Turn a single LED on or off
    * Turn all LEDs on or off
    * Blink a LED continuously
    * Turn a LED on for a fixed duration (one-shot)

    Blinking is implemented as a background worker per LED, built on
    ThreadTemplate (utilities.py), so each blink can be cleanly started,
    stopped and restarted, and reports crashes instead of dying silently.

    LEDControl is called from several threads (state workers, the command
    handler, Timer threads, the main thread); every public method runs under
    one reentrant lock so their stop/start sequences cannot interleave.
"""
from threading import RLock, Timer, current_thread

##### Oradio modules ######################################
from log_service import oradio_log
from gpio_service import GPIOService
from singleton import singleton
from utilities import ThreadTemplate
from messaging import (
    Incidents,
    IncidentMessage,
    LED_SOURCE,
    LED_BLINK_START_FAILED,
    LED_BLINK_STOP_FAILED,
)

##### GLOBAL constants ####################################
from constants import LED_NAMES

class _BlinkWorker(ThreadTemplate):
    """
    Background worker that blinks a single LED on/off at a given cycle time.

    One instance is created lazily per LED, the first time that LED is ever
    blinked (see LEDControl._get_blink_worker), and then kept and reused for
    every later blink request for that same LED -- restarted via
    safe_start()/safe_stop() rather than replaced, since ThreadTemplate
    itself is restartable. This mirrors how MPDMonitor reuses a single
    _MPDMonitorWorker instance across repeated start()/stop() cycles.
    Because a worker is reused across blinks with different cycle times,
    cycle_time is not fixed at construction: it's set via set_cycle_time()
    before each safe_start() rather than passed to __init__.

    Note:
        do_work() itself sleeps out the on/off halves of the cycle via the
        interruptible stop_event.wait(), so ThreadTemplate's own interval
        sleep (passed as 0) provides no additional pacing here.
    """
    def __init__(self, leds_driver: GPIOService, led_name: str) -> None:
        """
        Args:
            leds_driver: The GPIOService used to actually switch the LED on/off.
                          Owned and constructed by the enclosing LEDControl,
                          passed in rather than constructed here, so all LEDs
                          share a single GPIOService instance.
            led_name:    Must be one of the names defined in LED_NAMES.
        """
        super().__init__(interval=0, name=f"Blink-{led_name}")
        self._leds_driver = leds_driver
        self._led_name = led_name
        # Configured via set_cycle_time() before each safe_start(); the
        # placeholder value here is never actually used for timing.
        self._cycle_time = 0.0

    def set_cycle_time(self, cycle_time: float) -> None:
        """
        Configure the on/off cycle duration to use for the next run.

        Must be called before safe_start() -- do_work() reads this once per
        cycle, so changing it while the worker is already blinking would
        only take effect on the next cycle rather than immediately, which
        is why callers stop the worker first (see LEDControl.control_blinking_led).

        Args:
            cycle_time: Duration in seconds of one complete on/off cycle.
        """
        self._cycle_time = cycle_time

    def do_work(self) -> None:
        """
        Run a single on/off cycle: LED on for the first half, off for the
        second half. Called repeatedly by the ThreadTemplate run loop, so
        each call is one full blink cycle. The stop_event.wait() calls
        double as the sleep for each half AND the interruptible wait, so a
        stop request lands within at most half a cycle instead of waiting
        out a full one.
        """
        half = self._cycle_time / 2
        self._leds_driver.set_led_on(self._led_name)
        if self._stop_event.wait(half):
            return  # Interrupted while on; teardown() turns it off.
        self._leds_driver.set_led_off(self._led_name)
        self._stop_event.wait(half)

    def teardown(self) -> None:
        """
        Always leave the LED off when blinking stops, whether the loop
        exited cleanly (stop requested while on or off) or do_work()
        crashed partway through a cycle.
        """
        self._leds_driver.set_led_off(self._led_name)

@singleton
class LEDControl:
    """
    Control the LEDs:
    * Turn a single LED on or off
    * Turn all LEDs on or off
    * Blink a LED continuously
    * Turn a LED on for a fixed duration (one-shot)

    Thread-safe: each public method holds self._lock (an RLock, since they
    call each other) for its whole run, so e.g. control_blinking_led()'s
    stop -> set_cycle_time -> safe_start cannot interleave with a
    turn_off_led() for the same LED from another thread.
    """
    def __init__(self) -> None:
        """
        Class constructor: set up class variables.
        Uses an instance of GPIOService for LED I/O.
        """
        self.leds_driver = GPIOService()
        # map led_name → _BlinkWorker; populated lazily (see _get_blink_worker)
        # and each entry reused for every later blink request for that LED.
        self.blink_workers: dict[str, _BlinkWorker] = {}
        # map led_name → pending one-shot off Timer; cancelled by any later
        # turn_on_led/turn_off_led/control_blinking_led for that LED, so an
        # old one-shot cannot switch off a LED someone has since set.
        self._oneshot_timers: dict[str, Timer] = {}
        # Blink workers whose crash has already been reported; see _stop_blink().
        self._crash_reported: set[str] = set()
        # Serialises all public methods; reentrant because they call each other.
        self._lock = RLock()
        oradio_log.debug("LEDControl initialized: All LEDs OFF")

    def turn_off_led(self, led_name: str) -> None:
        """
        Turn off a specified LED and wait for its blink worker to exit.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        with self._lock:
            if led_name in LED_NAMES:
                self._cancel_oneshot(led_name)
                self._stop_blink(led_name)
                self.leds_driver.set_led_off(led_name)
                oradio_log.debug("%s turned off", led_name)
            else:
                oradio_log.error("Invalid LED name: %s", led_name)

    def turn_on_led(self, led_name: str) -> None:
        """
        Turn on a specified LED, stopping any active blink worker first.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        with self._lock:
            if led_name in LED_NAMES:
                self.turn_off_led(led_name)
                self.leds_driver.set_led_on(led_name)
                oradio_log.debug("%s turned on", led_name)
            else:
                oradio_log.error("Invalid LED name: %s", led_name)

    def turn_off_all_leds(self) -> None:
        """
        Stop all blink workers and turn every LED off.
        """
        with self._lock:
            for led_name in LED_NAMES:
                self.turn_off_led(led_name)
        oradio_log.debug("All LEDs turned off and blinking stopped")

    def turn_on_all_leds(self) -> None:
        """
        Stop all blink workers and turn every LED on.
        """
        with self._lock:
            for led_name in LED_NAMES:
                self.turn_on_led(led_name)
        oradio_log.debug("All LEDs turned ON and blinking stopped")

    def oneshot_on_led(self, led_name: str, period: float = 3) -> None:
        """
        Turn on a specific LED and turn it off automatically after a delay.

        One pending one-shot per LED: a new one-shot, or any later
        turn_on_led/turn_off_led/control_blinking_led for the LED, cancels it.

        The period is rounded to one decimal place before use, as finer
        resolution is not perceptible.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
            period (float): Time in seconds before turning off the LED.
                            Rounded to one decimal place. Default is 3.
        """
        if period > 0:
            period = round(period, 1)
            if led_name in LED_NAMES:
                with self._lock:
                    # turn_on_led() also cancels any previous one-shot for this LED.
                    self.turn_on_led(led_name)
                    oradio_log.debug("%s turned on, will turn off after %s seconds", led_name, period)
                    oneshot_timer = Timer(period, self._oneshot_expired, args=(led_name,))
                    oneshot_timer.name = f"led-oneshot-{led_name}"
                    oneshot_timer.daemon = True
                    self._oneshot_timers[led_name] = oneshot_timer
                    oneshot_timer.start()
            else:
                oradio_log.error("Invalid LED name: %s", led_name)
        else:
            oradio_log.warning("Invalid period time of %f for one-shot of LED: %s", period, led_name)

    def get_led_state(self, led_name: str) -> bool:
        """
        Return the current state of a specified LED.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        Returns:
            True  if the LED is ON.
            False if the LED is OFF.
        """
        if led_name in LED_NAMES:
            with self._lock:
                return bool(self.leds_driver.get_led_state(led_name))
        oradio_log.error("Invalid LED name: %s", led_name)
        return False

    def control_blinking_led(self, led_name: str, cycle_time: float) -> None:
        """
        Start blinking a specified LED at the given cycle time.
        No default: every rate the user sees means something, and a caller that
        omitted one would blink at a number nobody named. The meanings are named
        by the caller -- ERROR_BLINK_CYCLE in constants, STARTUP_BLINK_CYCLE and
        WEBSERVICE_BLINK_CYCLE in oradio_control.

        If cycle_time is None or <= 0, the LED is turned off instead -- kept for
        callers that compute a rate. To simply switch a LED off, turn_off_led()
        says so directly.

        The LED is on for the first half of each cycle and off for the second:

            _________|^^^^^^^^^^^|____________|^^^^^^^^^^^^|____________|^^
                     |<====== cycle_time ====>|
                     |<== half =>|

        Args:
            led_name (str):       Must be one of the names defined in LED_NAMES
                                  (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
            cycle_time (float):   Duration in seconds of one complete on/off cycle.
                                  Pass None or a value <= 0 to stop blinking and turn the LED off.
        """
        with self._lock:
            if cycle_time is not None and cycle_time > 0:
                if led_name in LED_NAMES:
                    self._cancel_oneshot(led_name)
                    # Stop before reconfiguring: do_work() only picks up a new
                    # cycle_time on its next call, so a clean stop/start avoids
                    # running one stray cycle at the old duration.
                    self._stop_blink(led_name)
                    worker = self._get_blink_worker(led_name)
                    worker.set_cycle_time(cycle_time)
                    if worker.safe_start():
                        # A fresh run: a later crash of it is a new one to report.
                        self._crash_reported.discard(led_name)
                        oradio_log.debug("%s blinking started: %.3fs cycle", led_name, cycle_time)
                    else:
                        oradio_log.error("%s blink worker failed to start", led_name)
                        Incidents.publish(IncidentMessage(LED_SOURCE, LED_BLINK_START_FAILED))
                else:
                    oradio_log.error("Invalid LED name: %s", led_name)
            else:
                self.turn_off_led(led_name)
                oradio_log.debug("%s blinking stopped and turned off", led_name)

    def _cancel_oneshot(self, led_name: str) -> None:
        """
        Cancel the pending one-shot off Timer for led_name, if any.
        Called with self._lock held.
        """
        timer = self._oneshot_timers.pop(led_name, None)
        if timer is not None:
            timer.cancel()

    def _oneshot_expired(self, led_name: str) -> None:
        """
        Timer callback of oneshot_on_led(): turn the LED off, but only if this
        timer is still the LED's current one-shot. cancel() cannot stop a
        Timer that already fired and is waiting for the lock, so the identity
        check is what keeps a superseded one-shot from switching the LED off.
        """
        with self._lock:
            if self._oneshot_timers.get(led_name) is not current_thread():
                return
            # turn_off_led() pops this timer via _cancel_oneshot().
            self.turn_off_led(led_name)

    def _get_blink_worker(self, led_name: str) -> _BlinkWorker:
        """
        Return the reusable blink worker for led_name, creating it the
        first time this LED is blinked and reusing it on every later call.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES.

        Returns:
            _BlinkWorker: The (possibly newly created) worker for led_name.
        """
        # Under the lock so two first-time blinks of one LED can't each create
        # a worker, leaving the loser blinking with no reference to stop it.
        with self._lock:
            worker = self.blink_workers.get(led_name)
            if worker is None:
                worker = _BlinkWorker(self.leds_driver, led_name)
                self.blink_workers[led_name] = worker
            return worker

    def _stop_blink(self, led_name: str) -> None:
        """
        Signal the blink worker for the given LED to stop, if one exists,
        then block until it has fully exited. The worker itself is kept
        (not removed) so it can be reused for a later blink request. The
        LED is left in the off state by the worker's own teardown() before
        this returns. A no-op if the LED has never been blinked, since no
        worker exists for it yet. A worker that crashed (thread already dead)
        counts as stopped; its crash is reported once, not on every call.

        Args:
            led_name (str): Must be one of the names defined in LED_NAMES
                            (e.g. LED_PLAY, LED_STOP, LED_PRESET1, LED_PRESET2, LED_PRESET3).
        """
        with self._lock:
            worker = self.blink_workers.get(led_name)
            if worker is None or worker.safe_stop():
                return
            # A worker that crashed earlier keeps reporting crashed until its
            # next safe_start(), and turn_off_all_leds() runs on every state
            # transition. Its thread is dead, so it IS stopped (teardown() left
            # the LED off): report that crash once, not on every later stop.
            if worker.crashed and not worker.is_alive():
                if led_name not in self._crash_reported:
                    self._crash_reported.add(led_name)
                    oradio_log.error("%s blink worker had crashed: %s", led_name, worker.exception)
                    Incidents.publish(IncidentMessage(LED_SOURCE, LED_BLINK_STOP_FAILED))
                return
            oradio_log.error("%s blink worker did not stop cleanly", led_name)
            Incidents.publish(IncidentMessage(LED_SOURCE, LED_BLINK_STOP_FAILED))

##### Stand-alone entry point #############################

if __name__ == '__main__':
    print("The module test for led_control.py is at module_test/led_control_test.py")
