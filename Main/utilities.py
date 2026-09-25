#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Miscellaneous Oradio utility functions
    Following services provided:
        * Raspberry Pi serial number lookup
        * systemd service status check
        * Internet connectivity check
        * Generic shell command execution
        * Loading and storing presets.json
        * Console input prompting with type conversion and a default fallback
        * Restartable background worker template (ThreadTemplate)
"""
import json
import socket
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from typing import Any, NoReturn, TypeVar
from collections.abc import Callable
from threading import Thread, Event, Lock

##### Oradio modules ######################################
from log_service import oradio_log

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    PRESETS_FILE,
    USB_SYSTEM,
)

##### LOCAL constants #####################################
DNS_HOST    = "google.com"
DNS_TIMEOUT = 0.5   # seconds; short on purpose - callers should fail fast
                    # rather than block on a flaky or just-woken WiFi radio.

# Row prefix used by `vcgencmd otp_dump` for the Raspberry Pi serial number.
SERIAL_OTP_ROW = "28:"

JOIN_TIMEOUT = 5.0  # seconds; timeout for thread to start/stop

# Keys whose values must never reach the log, and what is written instead.
SECRET_KEYS = frozenset({"pswd"})
SECRET_MASK = "********"

# Self-restart budget for ThreadTemplate workers that opt in (restart_on_crash).
#
# A budget rather than "always retry": a worker whose dependency is gone crashes
# again the moment it is restarted, and an unbounded loop turns a dead sensor
# into a busy core and a log nobody can read.
#
# And a budget that decays rather than one per process: three crashes on a
# Tuesday must not leave a subsystem unrecoverable for the rest of the week. The
# window is what makes "three in a row" mean a persistent fault instead of three
# unrelated hiccups hours apart.
CRASH_RESTART_LIMIT   = 3       # restarts allowed within the window, so four attempts in all
CRASH_RESTART_WINDOW  = 900.0   # seconds a crash keeps counting against the budget
CRASH_RESTART_BACKOFF = 5.0     # seconds before an attempt, so a fast-failing loop stays slow

# How long restart_service() waits for a unit to report active again, and how
# often it asks. Generous: mpd re-reads its database on start, which on a full
# USB drive is seconds rather than milliseconds.
SERVICE_RESTART_TIMEOUT = 20.0  # seconds
SERVICE_POLL_INTERVAL   = 0.5   # seconds

# Budget for restarting a system service as a mitigation, used by
# incident_service. Smaller and slower-decaying than the crash budget above: a
# service restart is disruptive -- mpd stops the music, NetworkManager drops the
# connection -- so it is worth fewer attempts over a longer window.
SERVICE_RESTART_LIMIT  = 2       # restarts allowed within the window
SERVICE_RESTART_WINDOW = 1800.0  # seconds a restart keeps counting

# DeferredStarter defaults. The timeout is generous on purpose: the cost of
# waiting is a background thread doing nothing, while the cost of giving up too
# early is a subsystem that stays down for the rest of the boot.
DEFERRED_START_TIMEOUT = 60.0   # seconds to keep waiting for the dependency
DEFERRED_POLL_INTERVAL = 1.0    # seconds between availability checks

T = TypeVar("T")

class ThreadTemplate:
    """
    Template for a restartable background worker with start/stop/crash detection.

    Subclass and override:
        setup(): One-time init, runs at the start of each run.
        do_work(): The repeated unit of work.
        teardown(): One-time cleanup, runs when the run is stopping.

    Unlike a raw Thread, one ThreadTemplate instance can be safe_start()ed,
    safe_stop()ped, and safe_start()ed again any number of times: each
    safe_start() creates a fresh internal Thread (since a Thread object
    itself can only ever be run once) and resets the events/exception state
    left over from the previous run.

    safe_start() and safe_stop() are themselves safe to call concurrently
    from multiple threads (e.g. a watchdog thread and the main thread both
    driving the same instance): a dedicated lifecycle lock serializes the
    check-and-mutate sequence on `_thread` so two overlapping calls can't
    race and orphan a Thread object.
    """

    # Opt-in self-restart. False by default: the base class cannot know whether
    # a subclass survives having setup() run again, nor whether restarting it
    # is even meaningful -- a worker that exists for the duration of one song
    # is not the same as a poller that should never stop.
    #
    # Before setting this True, check that teardown() gives back exactly what
    # setup() takes. Whatever it misses is held twice after a restart.
    restart_on_crash: bool = False

    def __init__(self, *, interval: float = 1.0, name: str | None = None) -> None:
        """
        Initializes the worker.

        Args:
            interval: Seconds to wait between do_work() calls. Defaults to 1.0.
            name: Thread name. Defaults to the subclass's class name if not given.
        """
        self._interval = interval
        self._name = name or self.__class__.__name__

        self._stop_event = Event()      # set by safe_stop(), checked by run()
        self._started_event = Event()   # set once setup() finishes (or crashes)

        # _exception is written from run() (the worker thread) and read from
        # the crashed/exception properties, typically from the main thread.
        # Guarded by _exception_lock rather than relying on the GIL, so this
        # stays correct on interpreters without one.
        self._exception_lock = Lock()
        self._exception: Exception | None = None

        # Monotonic timestamps of crashes inside CRASH_RESTART_WINDOW. Only
        # touched by run(), on the worker thread.
        self._crash_times: list[float] = []

        # Guards the check-then-mutate sequences in safe_start()/safe_stop()
        # that read and write self._thread. Without this, two concurrent
        # safe_start() calls could both pass the "already running" check
        # before either assigns self._thread, each starting its own Thread
        # and the second assignment silently orphaning the first (nobody
        # keeps a reference to it, so it can no longer be stopped). It also
        # prevents safe_start() and safe_stop() from tearing on the same
        # instance, e.g. safe_stop() joining a thread that safe_start() is
        # in the middle of replacing.
        # Note: run() never calls safe_start()/safe_stop() on itself, and
        # nothing below holds this lock while blocking on _started_event.wait()
        # or _thread.join() -- see the comments in safe_start()/safe_stop() --
        # so there is no deadlock risk between the worker thread and callers.
        self._lifecycle_lock = Lock()

        # The actual OS thread, (re)created fresh on each safe_start() since
        # a Thread object itself can only ever be started once.
        self._thread: Thread | None = None

    # --- lifecycle -----------------------------------------------------

    def safe_start(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """
        Starts (or restarts) the worker and waits until setup() has completed.

        Creates a fresh internal Thread each call and resets the state left
        over from any previous run, so this can be called again after
        safe_stop() to run the same instance a second (or later) time.

        Thread-safe: the "is it already running" check and the creation/
        start of the new Thread happen under `_lifecycle_lock`, so two
        threads calling safe_start() on the same instance at the same time
        can't both slip past the check and each start their own Thread.

        Args:
            timeout: Max seconds to wait for setup() to finish.

        Returns:
            True if the thread reported ready within timeout,
            False if it's already running, failed to start, or setup() did
            not complete in time. Note that True does NOT mean setup()
            succeeded -- check the crashed property afterward to distinguish
            "timed out" from "started and immediately crashed".
        """
        # Hold the lock only for the check-and-mutate part (creating and
        # starting the Thread object). The actual wait for readiness happens
        # below, outside the lock, so a concurrent safe_stop() isn't blocked
        # from proceeding once this run is underway.
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                oradio_log.debug("%s is already running", self._name)
                return False

            # Reset state left over from a previous run so this run starts clean.
            self._stop_event.clear()
            self._started_event.clear()
            with self._exception_lock:
                self._exception = None

            # A fresh Thread object every time -- Thread.start() itself refuses
            # to run twice, so restartability requires a new one per run.
            self._thread = Thread(target=self.run, name=self._name, daemon=True)

            try:
                # Spawns the OS thread and schedules run(). Can raise
                # RuntimeError if the OS can't allocate a new thread.
                self._thread.start()
            except RuntimeError:
                oradio_log.error("%s failed to start thread", self._name)
                return False

        # Block here until run() signals that setup() has completed,
        # rather than assuming the thread is ready as soon as it's spawned.
        # Deliberately done outside `_lifecycle_lock`: waiting here can take
        # up to `timeout` seconds, and holding the lock that long would
        # needlessly block a concurrent safe_stop() from even signalling
        # the thread to stop.
        started_ok = self._started_event.wait(timeout)
        if not started_ok:
            oradio_log.error("%s failed to start within %ss", self._name, timeout)
        return started_ok

    def run(self) -> None:
        """
        Internal thread entry point. Do not call directly -- use safe_start().

        Runs setup() once, then calls do_work() immediately, then repeatedly
        again every interval seconds until stop() is called, then runs teardown().
        (do_work() fires right after setup() rather than waiting out the first
        interval, so a poller doesn't sit idle before its first check.) Any
        exception raised by setup() or do_work() is caught, logged, and stored
        rather than propagated, since exceptions raised inside a thread's run()
        are never seen by the caller of start().
        """
        # One pass per attempt. Without restart_on_crash this runs exactly once
        # and behaves as it always did.
        while True:
            try:
                self.setup()

                # Signal readiness only after setup() completes successfully.
                self._started_event.set()

                while not self._stop_event.is_set():
                    self.do_work()
                    # Doubles as the sleep interval AND the interruptible
                    # wait -- stop() setting the event wakes this up
                    # immediately instead of waiting out the full interval.
                    self._stop_event.wait(self._interval)

            # Broad catch is intentional: setup() and do_work() are overridden by
            # subclasses, so we can't predict what they might raise.
            except Exception as exc:      # pylint: disable=broad-exception-caught
                with self._exception_lock:
                    self._exception = exc
                oradio_log.error("%s crashed", self._name)
                # Unblock safe_start() even if setup() itself crashed, so callers
                # waiting on safe_start() don't hang for the full timeout.
                self._started_event.set()
                crashed = True
            else:
                crashed = False

            # teardown() belongs to one attempt, so it runs between attempts as
            # well as after the last one: whatever setup() took has to be given
            # back before setup() takes it again.
            self.teardown()

            if not self._restart_after_crash(crashed):
                break

        # Out of the loop: this worker has ended and is not coming back, which
        # is what on_stopped() is for. Reporting it inside the loop would
        # announce a stop that the next attempt is about to undo.
        self.on_stopped()


    def _restart_after_crash(self, crashed: bool) -> bool:
        """
        Decide whether run() should try again, and wait out the backoff if so.

        Args:
            crashed: True when the attempt that just ended raised.

        Returns:
            True when the loop should run setup() again.

        Says no to a clean exit, to a stop that was asked for, and to a class
        that did not opt in -- so for everything except an opted-in worker that
        crashed, this is where run() ends, exactly as it did before.

        Says no once the budget is spent, and leaves the recorded exception
        in place, so the worker ends the way an unguarded crash always did and
        on_stopped() reports it. The budget decays: a crash stops counting
        after CRASH_RESTART_WINDOW, so a subsystem that failed three times this
        morning can still recover this afternoon.
        """
        if not crashed or not self.restart_on_crash or self._stop_event.is_set():
            return False

        now = monotonic()
        self._crash_times = [t for t in self._crash_times if now - t < CRASH_RESTART_WINDOW]
        self._crash_times.append(now)

        if len(self._crash_times) > CRASH_RESTART_LIMIT:
            oradio_log.error(
                "%s used its restart budget (%d allowed within %.0fs) and is not coming back",
                self._name, CRASH_RESTART_LIMIT, CRASH_RESTART_WINDOW,
            )
            return False

        oradio_log.warning(
            "%s crashed (%d of %d within %.0fs); restarting in %.0fs",
            self._name, len(self._crash_times), CRASH_RESTART_LIMIT,
            CRASH_RESTART_WINDOW, CRASH_RESTART_BACKOFF,
        )

        # Waiting on the stop event, not sleeping: a stop() during the backoff
        # takes effect at once instead of after the full delay, and returns
        # True, which the check below turns into "do not restart".
        if self._stop_event.wait(CRASH_RESTART_BACKOFF):
            return False

        # A fresh attempt, so the recorded exception is from the previous one.
        # Cleared here rather than in setup(), so crashed stays True for anyone
        # reading it during the backoff.
        with self._exception_lock:
            self._exception = None

        return True

    def safe_stop(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """Signals the worker to stop and waits for it to finish.

        After this returns, safe_start() can be called again to run the
        same instance again from a clean state.

        Thread-safe: reading/clearing `self._thread` is done under
        `_lifecycle_lock`, so a concurrent safe_start() can't replace
        `self._thread` with a new Thread while this call is still looking
        at (or about to clear) the old one.

        Args:
            timeout: Max seconds to wait for the thread to exit.

        Returns:
            True if the thread finished within timeout, or if it was never
            started. False if it's still alive afterward (e.g. do_work() is
            blocked on something that ignores _stop_event), or if it crashed.
            Note that a stuck thread cannot be forcibly killed -- False just
            tells you it happened.
        """
        # Grab a reference to the current thread under the lock, so we're
        # guaranteed to join() the same Thread object we checked for None --
        # not one a concurrent safe_start() swapped in afterward.
        with self._lifecycle_lock:
            # Snapshot into a local variable rather than reading self._thread
            # again further down: the lock is released before the blocking
            # join() below, so self._thread could be reassigned by a
            # concurrent safe_start() in the meantime. `thread` stays a
            # stable reference to the run we're actually stopping.
            thread = self._thread
            if thread is None:
                oradio_log.debug("%s was not started", self._name)
                return True
            self._stop_event.set()  # tells run()'s loop condition to exit

        # join() can block for up to `timeout` seconds; done outside the
        # lock so a concurrent safe_start() (e.g. on a fresh run after this
        # one) isn't blocked from doing its own quick, lock-protected setup.
        thread.join(timeout)

        with self._lifecycle_lock:
            if thread.is_alive():
                oradio_log.error("%s did not stop within %ss", self._name, timeout)
                return False

            if self.crashed:
                oradio_log.error("%s crashed with exception: %s", self._name, self.exception)
                return False

            # Cleanup and return stopped. Only clear self._thread if it's
            # still the same object we joined -- guards against the (rare)
            # case where a concurrent safe_start() already replaced it with
            # a new run in the time between the join() above and this block.
            if self._thread is thread:
                self._thread = None
            return True

    def is_alive(self) -> bool:
        """
        Whether the underlying thread currently exists and is running.

        Mirrors threading.Thread.is_alive(). ThreadTemplate holds a Thread
        rather than being one, so callers ask this instead.
        """
        return self._thread is not None and self._thread.is_alive()

    @property
    def name(self) -> str:
        """The worker's name, as passed to __init__ (or the class name by default)."""
        return self._name

    @property
    def stopping(self) -> bool:
        """
        True once safe_stop() has been called for the current run, even
        before the thread has actually exited. Reset to False at the start
        of the next safe_start(). Useful inside a long-running do_work() to
        check whether it should bail out early.
        """
        return self._stop_event.is_set()

    @property
    def crashed(self) -> bool:
        """True if setup() or do_work() raised an exception during the current/last run."""
        # Same lock as the exception property below, and for the reason given where
        # _exception_lock is created: _exception is written from the worker thread and
        # read from the caller's, so the read is guarded rather than left to the GIL.
        with self._exception_lock:
            return self._exception is not None

    @property
    def exception(self) -> Exception | None:
        """The exception raised inside run() during the current/last run, if any."""
        with self._exception_lock:
            return self._exception

    # --- override these --------------------------------------------------

    def setup(self) -> None:
        """
        Called once before the work loop starts, on every run (i.e. again
        on each safe_start() after a safe_stop()). Override for one-time
        per-run initialization (opening connections, allocating resources,
        etc.). Default implementation does nothing.
        """
        # Pass is intentional, see doc string
        pass    # pylint: disable=unnecessary-pass

    def do_work(self) -> None:
        """
        Called repeatedly until stop() is called. Override this
        with the actual unit of work the thread should perform.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.
        """
        raise NotImplementedError

    def teardown(self) -> None:
        """
        Called after the loop exits, whether it exited cleanly or due to an
        exception. Override for cleanup: closing connections, releasing
        resources, putting hardware back in a known state.

        Scoped to one run of the loop, not to the object. Anything here must be
        safe to do more than once over the lifetime of the instance, and must
        undo exactly what setup() did -- a restart runs setup() again, and
        whatever teardown() failed to release is then held twice.

        For "this worker has ended and that is worth reporting", override
        on_stopped() instead. Default implementation does nothing.
        """
        # Pass is intentional, see doc string
        pass    # pylint: disable=unnecessary-pass

    def stop_reason(self) -> str:
        """
        Why this worker ended, for the details of a *_STOPPED incident.

        Returns:
            A short description: the last exception and how often it recurred,
            or a note that the stop was asked for.

        Worth passing explicitly rather than letting IncidentMessage capture a
        stack. That capture happens where the incident is raised -- in
        on_stopped(), on the way out -- so it describes the tear-down and not
        the failure that caused it. The exception below is the one that ended
        the last attempt, which is what someone reading the incident wants.

        The count matters too: with restart_on_crash the worker has already
        tried and failed several times, so "4 times" says this was persistent
        rather than a one-off.
        """
        exc = self.exception
        if exc is None:
            return "stopped on request"

        attempts = len(self._crash_times)
        return f"{type(exc).__name__}: {exc} (after {attempts} crash(es))"

    def on_stopped(self) -> None:
        """
        Called once when the worker has ended and is not coming back.

        For reporting, not for cleanup: publishing an incident because a
        subsystem the Oradio never stops on purpose has stopped anyway. Runs
        after teardown().

        Separate from teardown() because teardown() belongs to a run of the
        loop and this belongs to the object. A worker that restarts itself
        tears down between attempts; an incident published there would announce
        a stop that the next attempt is about to undo. Default implementation
        does nothing.
        """
        # Pass is intentional, see doc string
        pass    # pylint: disable=unnecessary-pass

class DeferredStarter:  # pylint: disable=too-many-instance-attributes
    """
    Start a subsystem whose system dependency may not be up yet.

    oradio.service is ordered After=basic.target and nothing else, so
    oradio_control routinely reaches a subsystem's start() before MPD,
    NetworkManager or the USB mount exist. Starting anyway makes the start fail
    on a dependency that was merely late, and that failure is permanent:
    nothing retries it for the rest of the boot.

    This generalises the pattern WifiService.start() already implements, so
    every other subsystem can use it too:

        available now -> run the start on the caller's thread, return True
        not up yet    -> hand it to a background thread that polls until the
                         dependency appears or the timeout expires
        never         -> log once and call on_timeout, so the fault is
                         reported exactly once instead of per attempt

    The point is that nothing on the user-facing path ever waits. The start-up
    tune, the buttons and the volume knob do not care whether the music library
    has been scanned yet, so the scan happens beside them rather than in front
    of them.

    Idempotent: a start already in progress, or already completed, is a no-op.
    Construct one instance per subsystem; it is not reusable across
    dependencies.
    """
    # Six arguments against a max-args of 5. Three are the subsystem itself
    # (name, predicate, action) and three are policy with sensible defaults;
    # bundling any of them into a config object would hide the one thing a
    # reader needs to see at the call site, which is what gets started when.
    def __init__(     # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        name: str,
        is_available: Callable[[], bool],
        do_start: Callable[[], None],
        on_timeout: Callable[[], None] | None = None,
        poll_interval: float = DEFERRED_POLL_INTERVAL,
        inline: bool = True,
    ) -> None:
        """
        Args:
            name:          Human-readable subsystem name, used in log lines only.
            is_available:  Cheap, non-blocking predicate answering "is the
                           dependency there yet". Called once per poll interval,
                           so it must not block or the poll loop inherits that.
            do_start:      The actual start action. Runs at most once.
            on_timeout:    Called if the dependency never appears. Publish the
                           incident here, not in do_start, so an outage is
                           reported once rather than once per poll.
            poll_interval: Seconds between is_available() checks.
            inline:        True (default) lets start() check is_available() on
                           the calling thread and, if it says yes, run do_start()
                           there too -- the cheapest path when the dependency is
                           already up.

                           False sends both to the background thread, always.
                           Pass it when either can block: a predicate that opens
                           a network connection costs seconds on a cold boot,
                           and the caller pays them before start() has even
                           decided whether to defer -- which is the wait the
                           deferral exists to remove.

                           Set here and not on start(), because whether these
                           two can block is a property of this pair and not of
                           an individual call. On start() two call sites for the
                           same starter could disagree, and the one that forgot
                           would block a thread that must not.
        """
        self._name = name
        self._is_available = is_available
        self._do_start = do_start
        self._on_timeout = on_timeout
        self._poll_interval = poll_interval
        self._inline = inline

        # Guards _starting and _done only. Never held across _do_start(), which
        # may block: holding it there would make a concurrent start() wait the
        # work out instead of returning at once on the _starting check.
        self._lock = Lock()
        self._starting = False
        self._done = False

        # Set by abort(). Also doubles as the poll loop's sleep, so an abort
        # takes effect immediately rather than after a full poll interval.
        self._aborting = Event()

    def is_done(self) -> bool:
        """Return True once do_start has run to completion."""
        with self._lock:
            return self._done

    def is_starting(self) -> bool:
        """
        Return True while a start is claimed but not finished.

        Covers both shapes of "in progress": running on the caller's thread,
        and waiting in the background for the dependency to appear.
        """
        with self._lock:
            return self._starting

    def reset(self) -> None:
        """
        Forget that a start ever completed, so a later start() runs again.

        For subsystems with a stop/start lifecycle: after a stop, the work
        do_start did is undone and has to happen again on the way back up.
        Without this, _done would make every later start() a no-op.

        Does not cancel a start already in flight -- call abort() for that,
        and call it first, so the deferred thread is on its way out before its
        claim is cleared underneath it.
        """
        with self._lock:
            self._done = False

    def start(self, wait: float = DEFERRED_START_TIMEOUT) -> bool:
        """
        Start now if the dependency is up, otherwise wait for it in the background.

        Args:
            wait:   Seconds to keep waiting in the background. Pass 0 to skip
                    starting entirely when the dependency is absent, which suits
                    tests, stand-alone runs and "try again now" call sites.

        Returns:
            True if do_start ran on this thread, which an inline=False starter
            never does. False if the start was deferred, skipped, already in
            progress, or already done -- so a caller can fall back to its own
            handling without racing this one.
        """
        with self._lock:
            if self._done:
                oradio_log.debug("%s already started", self._name)
                return False

            if self._starting:
                oradio_log.debug("%s start already in progress", self._name)
                return False

            # A previous abort() may have set this; clear it so a restart works.
            self._aborting.clear()

            # Claimed here, released by _clear_starting() once this start has
            # run its course -- here, or on the deferred thread.
            self._starting = True

        # False until the deferred thread has taken the claim over; it releases
        # it in that case, and the finally below releases it in every other.
        handed_over = False

        try:
            if self._inline:
                if self._is_available():
                    self._run()
                    return True

                if wait <= 0:
                    oradio_log.info("%s: dependency not available; not started", self._name)
                    return False

                oradio_log.info("%s: dependency not up yet; deferring start", self._name)
            else:
                if wait <= 0:
                    oradio_log.info("%s: not started; waiting is disabled", self._name)
                    return False

                oradio_log.info("%s: deferring start without checking here", self._name)

            # Daemon thread: exits automatically when the process does.
            Thread(
                target=self._wait_and_start, args=(wait,),
                daemon=True, name=f"defer-{self._name}",
            ).start()
            handed_over = True
            return False

        finally:
            if not handed_over:
                self._clear_starting()

    def abort(self) -> None:
        """
        Cancel a deferred start still waiting in the background.

        Call this from the owning subsystem's stop(), so a shutdown does not
        leave a thread that brings the subsystem up again a minute later.
        """
        self._aborting.set()

    def _clear_starting(self) -> None:
        """
        Release the start claim taken by start().

        Called from whichever context finished the start: start() itself, or
        the deferred thread it handed over to. Leaving it set would make
        start() a permanent no-op for the rest of the process.
        """
        with self._lock:
            self._starting = False

    def _run(self) -> None:
        """
        Run the start action once and record that it succeeded.

        Exceptions are caught rather than propagated: on the caller's thread
        this is module initialisation, where an escape takes the process down
        and hands it to oradio-crash.service, and on the deferred thread
        nothing would observe it at all. A failed start leaves _done False, so
        a later start() can retry it.
        """
        try:
            self._do_start()
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            oradio_log.error("%s: start failed: %s", self._name, ex_err)
            return

        with self._lock:
            self._done = True

        oradio_log.info("%s started", self._name)

    def _wait_and_start(self, timeout: float) -> None:
        """
        Wait for the dependency to appear, then start.

        Runs on a background thread. Polls rather than subscribing to anything,
        because the dependencies this covers announce themselves in three
        different ways (a listening socket, a D-Bus name, a mount point) and a
        poll is the only check that works for all of them.

        Args:
            timeout: Maximum seconds to wait before giving up and reporting.
        """
        started = monotonic()
        deadline = started + timeout

        # try/finally so every way out of this thread -- started, aborted or
        # timed out -- releases the claim start() handed over.
        try:
            while monotonic() < deadline:
                # Checked before waiting and after waking, so abort() takes
                # effect within one poll interval at worst.
                if self._aborting.is_set():
                    oradio_log.debug("Deferred start of %s aborted", self._name)
                    return

                if self._is_available():
                    oradio_log.info(
                        "%s: dependency available after %.1fs",
                        self._name, monotonic() - started,
                    )
                    self._run()
                    return

                # Waiting on the Event rather than sleep() makes abort()
                # immediate instead of costing a full poll interval.
                self._aborting.wait(self._poll_interval)

            oradio_log.warning(
                "%s: dependency did not appear within %.0fs; giving up",
                self._name, timeout,
            )

            if self._on_timeout is not None:
                try:
                    self._on_timeout()
                except Exception as ex_err:     # pylint: disable=broad-exception-caught
                    oradio_log.error("%s: timeout handler failed: %s", self._name, ex_err)

        finally:
            self._clear_starting()

def get_serial() -> str:
    """
    Extract serial from Raspberry Pi.

    Returns:
        The serial number, or "Unknown" when it could not be read.

    Every failure is logged as an error. The serial is how RMS tells one
    Oradio from another: a device reporting as "Unknown" is not a missing
    detail but a device whose incidents cannot be attributed to it, and that
    has to be visible on the device rather than guessed at from the far end.
    """
    cmd = "vcgencmd otp_dump"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get serial number, error: %s", cmd, response)
        return "Unknown"

    # Parse the output in Python
    for line in response.splitlines():
        if line.startswith(SERIAL_OTP_ROW):
            serial = line[len(SERIAL_OTP_ROW):].strip()
            if serial:
                return serial
            oradio_log.error("Serial row '%s' in <%s> output is empty", SERIAL_OTP_ROW, cmd)
            return "Unknown"

    oradio_log.error("No serial row '%s' in <%s> output", SERIAL_OTP_ROW, cmd)
    return "Unknown"

def mask_secrets(data: Any) -> Any:
    """
    Return a copy of data that is safe to log: secret values replaced by SECRET_MASK.

    Args:
        data: Typically a message or argument dict. Anything that is not a dict
              is returned unchanged.

    Returns:
        A shallow copy of the dict with each non-empty value under a key in
        SECRET_KEYS replaced by SECRET_MASK; the original is left untouched.

    The log file is uploaded to RMS with every incident, so a password written
    to it leaves the device. Empty values (None, "") are kept as they are: that
    a network was joined without a password is worth seeing, and says nothing
    about any password.
    """
    if not isinstance(data, dict):
        return data
    return {
        key: SECRET_MASK if key in SECRET_KEYS and value else value
        for key, value in data.items()
    }


def fatal_exit(message: str, stacklevel: int = 6, *, exc: BaseException | None = None, code: int = 1) -> NoReturn:
    """
    Log a fatal error, flush all buffers, and terminate the process.

    Intended for unrecoverable infrastructure failures such as queue
    corruption, invalid internal state, or IPC failure.

    Uses os._exit instead of sys.exit to terminate immediately from any thread.
    This includes daemon threads, where sys.exit() would only terminate the calling thread.

    Args:
        message:    Human-readable description of the fatal error.
        stacklevel: Logging stacklevel passed to oradio_log.critical().
                    The default reports the caller of whatever wrapper invoked
                    this; a direct caller wants 4. Checked by making the log
                    line name the caller rather than log_service or this
                    module, and worth checking again if the call chain changes.
        exc:        Optional exception associated with the failure; when provided,
                    the full traceback is included in the log entry.
        code:       Process exit status code (default: 1).

    Exiting non-zero is also how Python reaches tier three of the recovery:
    systemd restarts oradio.service once, and if that start fails too the crash
    handler reboots, reports to RMS, and on a further failure plays the service
    message and blinks the STOP LED. So a caller reaching for this is not giving
    up -- it is asking for the strongest repair available, which is more than
    any code in this process can do for a peripheral that stopped answering.

    """
    # exc_info=True causes the logging framework to capture the current
    # exception context; passing the exception object directly also works
    # in Python 3.5+ but the bool form is more conventional.
    oradio_log.critical(message, stacklevel=stacklevel, exc_info=exc is not None)

    # Flush the logging framework before exiting so no records are lost.
    oradio_log.shutdown()

    # Flush console buffers before terminating.
    sys.stderr.flush()
    sys.stdout.flush()

    # Bypass Python's normal shutdown sequence so the exit is immediate
    # from any thread, including daemon threads.
    os._exit(code)


def is_service_active(service_name) -> bool:
    """
    Check if systemd service is running
    Args:
        service_name (str): Name of the service
    Returns:
        bool: True if service is active, False otherwise
    """
    try:
        # Run systemctl is-active command
        result = subprocess.run(
            ["sudo", "systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Error checking %s service, error-status: %s", service_name, ex_err)
        return False

def is_service_activating(service_name: str) -> bool:
    """
    Check if a systemd service is still starting up.

    Args:
        service_name (str): Name of the service
    Returns:
        bool: True if systemd reports the unit as 'activating', False otherwise
            (including when it could not be asked).

    For a unit with Type=notify, such as mpd.service, 'activating' lasts until
    the daemon tells systemd it is ready -- which is exactly the window in which
    it accepts a connection but does not answer yet. A server in that state is
    not broken, and restarting it only starts the wait over.
    """
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        return result.stdout.strip() == "activating"
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Error checking %s service, error-status: %s", service_name, ex_err)
        return False

def restart_service(service_name: str, timeout: float = SERVICE_RESTART_TIMEOUT) -> bool:
    """
    Restart a systemd service and wait for it to report active again.

    Args:
        service_name: Unit to restart, e.g. "mpd.service".
        timeout:      Seconds to wait for the unit to become active afterwards.

    Returns:
        True when the unit is active again.

    The restart itself is quick to ask for and slow to take effect, so this
    waits: a caller that returned immediately would tell the rest of the Oradio
    that the service is back while it is still starting, and the first command
    after that would fail for a reason that is no longer true.

    Needs root, which the Oradio user has through the passwordless sudo the
    installer configures -- the same route is_service_active() above already
    takes.
    """
    oradio_log.info("Restarting %s", service_name)

    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", service_name],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Could not restart %s: %s", service_name, ex_err)
        return False

    if result.returncode != 0:
        oradio_log.error(
            "Restarting %s failed: %s", service_name, result.stderr.strip() or "no output"
        )
        return False

    # 'systemctl restart' returns once the job is queued, not once the service
    # is serving, so ask separately until it says so.
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if is_service_active(service_name):
            oradio_log.info("%s is active again", service_name)
            return True
        sleep(SERVICE_POLL_INTERVAL)

    oradio_log.error("%s did not become active within %.0fs", service_name, timeout)
    return False


def has_internet():
    """
    Try whether the wifi-connection has internet by using a DNS service to resolve a domain name.
    As domain name is used google.com, which is one of the most reliable and globally available domains.
    This will resolve into a IPv4 address,to test DNS and networking connectivity using UDP Port 53.
    DNS lookups are high-priority traffic and typically wake the Wi-Fi radio from power-saving mode.

    Note:
        socket.gethostbyname() always uses the process-wide default socket
        timeout (set via socket.setdefaulttimeout()); it is not a
        socket-object method, so a per-call timeout cannot be passed
        directly. The previous default timeout is saved and restored
        around the call so this function does not permanently change
        timeout behaviour for other sockets created elsewhere in the
        process.

    Returns:
        bool: True if internet is reachable, False otherwise.
    """
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(DNS_TIMEOUT)
    try:
        _ = socket.gethostbyname(DNS_HOST)
        oradio_log.info("Internet available")
        return True
    except (socket.gaierror, TimeoutError) as ex_err:
        oradio_log.debug("Internet not available: %s", ex_err)
        return False
    finally:
        socket.setdefaulttimeout(previous_timeout)

def run_shell_script(script):
    """
    Simplified shell command execution
    Args:
        script (str) - shell command to execute
    Returns:
        (success, output) tuple
             success=True -> output = stdout (stripped)
             success=False -> output = stderr (stripped)
    """
    oradio_log.info("Running shell script: %s", script, stacklevel=4)
    try:
        process = subprocess.run(
            script,
            shell = True,           # Avoid exception, inspect returncode and stdout/stderr
            capture_output = True,
            text = True,
            check = False           # Avoid exception, inspect returncode and stdout/stderr
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Error running shell script <%s>, error: %s", script, ex_err)
        return False, str(ex_err)

    if process.returncode != 0:
        return False, process.stderr.strip()
    return True, process.stdout.strip()

def _normalize_listname(raw_value) -> str:
    """
    Normalize a raw preset value into a clean listname string.

    Args:
        raw_value: Value to normalize, expected to be a str but tolerates
            other/missing types.

    Returns:
        str: The stripped string if raw_value is a non-blank string,
            otherwise an empty string.
    """
    return raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else ""

def load_presets() -> dict[str, str]:
    """
    Retrieve the playlist names associated with the presets from a JSON file.
    Returns:
        dict[str, str]: A dictionary mapping lowercase preset_key -> listname.
                        If a preset value is missing or invalid, listname will be an empty string "".
                        Keys are normalized to lowercase for case-insensitive lookup.
    """
    try:
        with open(PRESETS_FILE, encoding='utf-8') as file:
            presets = json.load(file)
            if not isinstance(presets, dict):
                oradio_log.error("Invalid JSON format in %s: expected dict", PRESETS_FILE)
                return {}
    except FileNotFoundError:
        oradio_log.error("File not found at %s", PRESETS_FILE)
        return {}
    except json.JSONDecodeError:
        oradio_log.error("Failed to JSON decode %s", PRESETS_FILE)
        return {}

    # Ensure all expected keys exist and are normalized
    presets_dict = {}
    for key in ["preset1", "preset2", "preset3"]:
        # Fetch raw value from JSON, default to empty string if missing
        raw_value = presets.get(key, "")
        listname = _normalize_listname(raw_value)
        if not listname:
            oradio_log.warning("Preset '%s' is missing or has an empty listname in %s", key, PRESETS_FILE)

        # Store in dictionary using lowercase key for case-insensitive lookups
        presets_dict[key.lower()] = listname

    oradio_log.debug("Presets loaded (case-insensitive): %s", presets_dict)
    return presets_dict

def store_presets(presets: dict[str, str]) -> None:
    """
    Save the provided presets dictionary to the presets.json file in the USB_SYSTEM folder.

    Args:
        presets (dict): Dictionary containing keys 'preset1', 'preset2', 'preset3' with playlist values.
    """
    # Ensure the USB_SYSTEM directory exists
    try:
        Path(USB_SYSTEM).mkdir(parents=True, exist_ok=True)
    except OSError as ex_err:
        oradio_log.error("Presets cannot be saved. Error: %s", ex_err)
        return

    # Prepare the data to save, ensuring all expected keys exist.
    # Keys are already lowercase literals here, so no case normalization
    # of the key itself is needed (unlike load_presets' lookup from
    # arbitrary JSON input).
    data_to_save = {}
    for key in ["preset1", "preset2", "preset3"]:
        # Fetch raw value from JSON, default to empty string if missing
        raw_value = presets.get(key, "")
        data_to_save[key] = _normalize_listname(raw_value)

    # Write the JSON file
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as file:
            json.dump(data_to_save, file, indent=4)
        oradio_log.debug("Presets '%s' successfully saved to %s", data_to_save, PRESETS_FILE)
    except OSError as ex_err:
        oradio_log.error("Failed to write presets to '%s'. Error: %s", PRESETS_FILE, ex_err)

def input_prompt(prompt: str, cast: Callable[[str], T], default: T) -> T:
    """
    Prompt the user for input and cast it to the requested type.

    Args:
        prompt: Prompt shown to the user.
        cast: Cast function (e.g. int, float).
        default: Value returned if cast fails.

    Returns:
        Cast value or the default.
    """
    try:
        return cast(input(prompt))
    except (ValueError, EOFError):
        return default

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu():
        """Show menu with test options"""

        # Show menu with test options
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Show internet connection status\n"
            " 2-Run shell script('ls')\n"
            " 3-Run shell script('xxx')  [intentionally invalid command, exercises the failure path]\n"
            "Select: "
        )

        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    break
                case 1:
                    print(f"\nConnected to internet: {has_internet()}\n")
                case 2:
                    result, response = run_shell_script("ls")
                    if result:
                        print(f"\nresult={result}, response={response}")
                    else:
                        print(f"\n{YELLOW}Unexpected result: result={result}, response={response}{NC}")
                case 3:
                    result, response = run_shell_script("xxx")
                    if not result:
                        print(f"\nresult={result}, response={response}")
                    else:
                        print(f"\n{YELLOW}Unexpected result: result={result}, response={response}{NC}")
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
