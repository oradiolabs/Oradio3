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
        * Deferred start of a subsystem whose dependency is late (DeferredStarter)
        * systemd service restart, fatal process exit, log secret masking, USB write flush
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
DNS_TIMEOUT = 0.5   # seconds has_internet() waits for the DNS lookup; short on
                    # purpose - callers should fail fast rather than block on a
                    # flaky or just-woken WiFi radio. Enforced by waiting on a
                    # worker thread, since the resolver itself takes no timeout.

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

# Upper bound on the 'systemctl restart' call itself, which SERVICE_RESTART_TIMEOUT
# does not cover. systemctl waits for systemd's stop-and-start job, and the stop
# half waits for the old process to exit. A process stuck in uninterruptible I/O
# (mpd reading from a USB stick that stopped answering) cannot exit, not even on
# SIGKILL, so without a bound the call -- and the incident handler thread making
# it -- waits for minutes or for ever. Generous: a healthy mpd restart, database
# read included, takes a few seconds.
SERVICE_RESTART_CALL_TIMEOUT = 30   # seconds; an int, as it is passed to timeout(1)
# Grace period before timeout(1) escalates from SIGTERM to SIGKILL on systemctl.
SERVICE_RESTART_KILL_AFTER   = 5    # seconds

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

        # Set once setup() finishes (or crashes). Replaced by a fresh Event on
        # every safe_start() rather than cleared, and each run is handed its
        # own: a safe_stop() + safe_start() from another thread must not reset
        # the event an earlier safe_start() is still waiting on, or that call
        # reports a timeout for a thread that did start.
        self._started_event = Event()

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
        # nothing below holds this lock while blocking on the started event
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
            started_event = Event()
            self._started_event = started_event
            with self._exception_lock:
                self._exception = None

            # A fresh Thread object every time -- Thread.start() itself refuses
            # to run twice, so restartability requires a new one per run. The
            # run gets this call's started event, so it signals the caller
            # that started it even if a later safe_start() has replaced
            # self._started_event by then.
            self._thread = Thread(
                target=self.run, args=(started_event,), name=self._name, daemon=True
            )

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
        # the thread to stop. Waits on the local reference, not on
        # self._started_event, which a concurrent restart may have replaced.
        started_ok = started_event.wait(timeout)
        if not started_ok:
            oradio_log.error("%s failed to start within %ss", self._name, timeout)
        return started_ok

    def run(self, started_event: Event | None = None) -> None:
        """
        Internal thread entry point. Do not call directly -- use safe_start().

        Args:
            started_event: The Event the safe_start() that spawned this run
                           waits on. Defaults to self._started_event.

        Runs setup() once, then calls do_work() immediately, then repeatedly
        again every interval seconds until safe_stop() is called, then runs teardown().
        (do_work() fires right after setup() rather than waiting out the first
        interval, so a poller doesn't sit idle before its first check.) Any
        exception raised by setup() or do_work() is caught, logged, and stored
        rather than propagated, since exceptions raised inside a thread's run()
        are never seen by the caller of start().
        """
        if started_event is None:
            started_event = self._started_event

        # One pass per attempt. Without restart_on_crash this runs exactly once
        # and behaves as it always did.
        while True:
            try:
                self.setup()

                # Signal readiness only after setup() completes successfully.
                started_event.set()

                while not self._stop_event.is_set():
                    self.do_work()
                    # Doubles as the sleep interval AND the interruptible
                    # wait -- safe_stop() setting the event wakes this up
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
                started_event.set()
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

        # Waiting on the stop event, not sleeping: a safe_stop() during the backoff
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
        Called repeatedly until safe_stop() is called. Override this
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

    This generalises the pattern WifiService.start() first implemented, and
    now uses through this class, so every other subsystem can use it too:

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
    The exception is a start that abort() has cancelled but whose deferred
    thread has not exited yet: a start() in that window supersedes it, so a
    stop() immediately followed by start() still brings the subsystem up.
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

        # Guards _starting, _done, _generation, _in_do_start and _aborting. Never held across
        # _do_start(), which may block: holding it there would make a
        # concurrent start() wait the work out instead of returning at once on
        # the _starting check.
        self._lock = Lock()
        self._starting = False
        self._done = False

        # Incremented by every start() that claims a run. A run only releases
        # the claim, or records _done, while it is still the current
        # generation, so a run that start() superseded exits quietly instead
        # of clearing the claim of the run that replaced it.
        self._generation = 0

        # True while a run is inside _do_start(). Such a run is past its abort
        # check and will finish the start regardless, so it is not superseded:
        # two _do_start() calls would then run side by side.
        self._in_do_start = False

        # Set by abort(). A fresh Event per run: the one a run was started
        # with stays set after abort(), so that run still sees it and exits,
        # while the run that superseded it starts with a clear one. Also
        # doubles as the poll loop's sleep, so an abort takes effect
        # immediately rather than after a full poll interval.
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

            # A run that abort() has cancelled before it reached _do_start() is
            # on its way out, not in progress: it only has not reached its
            # finally yet. Turning this
            # start away would lose it, because the old run is about to exit
            # without starting anything -- which is what a stop() followed at
            # once by start() runs into. So it is superseded instead.
            if self._starting and (not self._aborting.is_set() or self._in_do_start):
                oradio_log.debug("%s start already in progress", self._name)
                return False

            # A fresh Event rather than clear(): the superseded run still holds
            # the old one, which must stay set so that run exits.
            self._aborting = Event()
            aborting = self._aborting

            # Claimed here, released by _clear_starting() once this start has
            # run its course -- here, or on the deferred thread.
            self._generation += 1
            generation = self._generation
            self._starting = True

        # False until the deferred thread has taken the claim over; it releases
        # it in that case, and the finally below releases it in every other.
        handed_over = False

        try:
            if self._inline:
                if self._is_available():
                    return self._run(generation, aborting)

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
                target=self._wait_and_start, args=(wait, generation, aborting),
                daemon=True, name=f"defer-{self._name}",
            ).start()
            handed_over = True
            return False

        finally:
            if not handed_over:
                self._clear_starting(generation)

    def abort(self) -> None:
        """
        Cancel a deferred start still waiting in the background.

        Call this from the owning subsystem's stop(), so a shutdown does not
        leave a thread that brings the subsystem up again a minute later.
        Also cancels an inline start whose is_available() check is still
        running, as long as _do_start has not begun yet.
        """
        with self._lock:
            self._aborting.set()

    def _clear_starting(self, generation: int) -> None:
        """
        Release the start claim taken by start().

        Args:
            generation: The run releasing its claim. A run that a later
                        start() superseded no longer owns the claim, so it
                        leaves it alone.

        Called from whichever context finished the start: start() itself, or
        the deferred thread it handed over to. Leaving it set would make
        start() a permanent no-op for the rest of the process.
        """
        with self._lock:
            if generation == self._generation:
                self._starting = False

    def _run(self, generation: int, aborting: Event) -> bool:
        """
        Run the start action once and record that it succeeded.

        Args:
            generation: The run this start belongs to.
            aborting:   That run's abort Event.

        Returns:
            True if do_start ran, False if the run was aborted first.

        The abort is checked again here, under the lock, because the
        is_available() check before this can take a while (a D-Bus call): an
        abort() landing during it must not still bring the subsystem up after
        its stop(). An abort() after this check, while _do_start runs, is too
        late to prevent it; the owner's stop() has to undo it as usual.

        Exceptions are caught rather than propagated: on the caller's thread
        this is module initialisation, where an escape takes the process down
        and hands it to oradio-crash.service, and on the deferred thread
        nothing would observe it at all. A failed start leaves _done False, so
        a later start() can retry it.
        """
        with self._lock:
            if aborting.is_set():
                oradio_log.debug("Start of %s aborted before it ran", self._name)
                return False
            self._in_do_start = True

        try:
            self._do_start()
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            oradio_log.error("%s: start failed: %s", self._name, ex_err)
            with self._lock:
                self._in_do_start = False
            return True

        with self._lock:
            self._in_do_start = False
            # Only the current run can get here (a run inside _do_start() is
            # never superseded), but checked anyway: a superseded run does not
            # speak for the current one. Not recorded either when abort() came
            # in during _do_start(): the owner's stop() has called reset() to
            # say this start is to be undone, and _done would make the next
            # start() a no-op.
            if generation == self._generation and not aborting.is_set():
                self._done = True

        oradio_log.info("%s started", self._name)
        return True

    def _wait_and_start(self, timeout: float, generation: int, aborting: Event) -> None:
        """
        Wait for the dependency to appear, then start.

        Runs on a background thread. Polls rather than subscribing to anything,
        because the dependencies this covers announce themselves in three
        different ways (a listening socket, a D-Bus name, a mount point) and a
        poll is the only check that works for all of them.

        Args:
            timeout:    Maximum seconds to wait before giving up and reporting.
            generation: The run this thread belongs to, see _clear_starting().
            aborting:   That run's own abort Event. Held here rather than read
                        from self, because a superseding start() replaces it.
        """
        started = monotonic()
        deadline = started + timeout

        # try/finally so every way out of this thread -- started, aborted or
        # timed out -- releases the claim start() handed over.
        try:
            while monotonic() < deadline:
                # Checked before waiting and after waking, so abort() takes
                # effect within one poll interval at worst.
                if aborting.is_set():
                    oradio_log.debug("Deferred start of %s aborted", self._name)
                    return

                if self._is_available():
                    oradio_log.info(
                        "%s: dependency available after %.1fs",
                        self._name, monotonic() - started,
                    )
                    self._run(generation, aborting)
                    return

                # Waiting on the Event rather than sleep() makes abort()
                # immediate instead of costing a full poll interval.
                aborting.wait(self._poll_interval)

            # An abort() during the last wait: a stop, not a missing dependency.
            if aborting.is_set():
                oradio_log.debug("Deferred start of %s aborted", self._name)
                return

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
            self._clear_starting(generation)

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


# Total time fatal_exit() gives its exit hooks before it exits anyway. The one
# registered today is RMS: posting the incident that led here, which carries the
# log files. Long enough for one upload on a normal link; on a bad one the crash
# handler (oradio-crash.service) still uploads the logs after the restart.
FATAL_EXIT_HOOK_BUDGET = 20.0   # seconds

_fatal_exit_hooks: list[Callable[[float], object]] = []
_fatal_exit_hooks_lock = Lock()

def register_fatal_exit_hook(hook: Callable[[float], object]) -> None:
    """
    Run hook(timeout) from fatal_exit(), before the process ends.

    Args:
        hook: Called with the seconds it may take at most. Must return within
              that time; its return value is ignored and exceptions are logged.

    For work that must not be lost to os._exit(): above all, getting the
    incident that caused the exit to RMS. Without this the RMS post was queued
    and the process was gone microseconds later, so a GPIO or I2C bus failure
    never reached RMS as an incident at all.

    Registering the same hook twice is a no-op. Not undone by anything: hooks
    live as long as the process, which is all fatal_exit() needs.
    """
    with _fatal_exit_hooks_lock:
        if hook not in _fatal_exit_hooks:
            _fatal_exit_hooks.append(hook)

def _run_fatal_exit_hooks(budget: float) -> None:
    """Run the registered exit hooks within one shared time budget."""
    with _fatal_exit_hooks_lock:
        hooks = list(_fatal_exit_hooks)

    deadline = monotonic() + budget
    for hook in hooks:
        remaining = deadline - monotonic()
        if remaining <= 0:
            oradio_log.error("Fatal exit: no time left for exit hook %s", getattr(hook, "__name__", hook))
            break
        try:
            hook(remaining)
        # Broad catch: a hook failing must not stop the process from exiting.
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            oradio_log.error("Fatal exit hook %s failed: %s", getattr(hook, "__name__", hook), ex_err)

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
                    its traceback is included in the log entry. Works outside an
                    except block too, e.g. for an exception a crashed thread
                    stored earlier.
        code:       Process exit status code (default: 1).

    Exiting non-zero is also how Python reaches tier three of the recovery:
    systemd restarts oradio.service once, and if that start fails too the crash
    handler reboots, reports to RMS, and on a further failure plays the service
    message and blinks the STOP LED. So a caller reaching for this is not giving
    up -- it is asking for the strongest repair available, which is more than
    any code in this process can do for a peripheral that stopped answering.

    Before exiting, runs the hooks registered with register_fatal_exit_hook(),
    for at most FATAL_EXIT_HOOK_BUDGET seconds together -- in practice: waits
    for the RMS sender to post what is queued, including the incident that
    led here. Bounded, so a hung network cannot keep a broken process alive.
    """
    # Pass the exception object itself, not True. exc_info=True logs whatever
    # sys.exc_info() holds at this moment, which is nothing outside an except
    # block (the log then shows "NoneType: None") and the wrong exception when
    # called while handling another one. The object carries its own traceback;
    # None logs no traceback at all.
    oradio_log.critical(message, stacklevel=stacklevel, exc_info=exc)

    # After the critical line (so it is in the log files an RMS upload
    # attaches), before the log shutdown (so the hooks can still log).
    _run_fatal_exit_hooks(FATAL_EXIT_HOOK_BUDGET)

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

    The systemctl call is bounded by timeout(1), see SERVICE_RESTART_CALL_TIMEOUT.
    Bounded there, as root under sudo, and not with subprocess.run(timeout=...):
    on expiry subprocess.run() kills its child, and its child is sudo, which runs
    as root and cannot be signalled by the Oradio user. The kill then raises
    PermissionError and run() waits for the child after all -- the hang this is
    meant to remove. timeout(1) runs as root and can stop systemctl.

    Stopping systemctl does not cancel the restart: systemd's job carries on, and
    the service may still come back later. This only stops the caller waiting
    for it, so the incident handler can deal with the next incident.
    """
    oradio_log.info("Restarting %s", service_name)

    try:
        result = subprocess.run(
            [
                "sudo", "timeout", f"--kill-after={SERVICE_RESTART_KILL_AFTER}",
                str(SERVICE_RESTART_CALL_TIMEOUT),
                "systemctl", "restart", service_name,
            ],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, OSError) as ex_err:
        oradio_log.error("Could not restart %s: %s", service_name, ex_err)
        return False

    # timeout(1) exits 124 when it stopped systemctl with SIGTERM, and 128+9
    # when systemctl ignored that and had to be killed.
    if result.returncode in (124, 137):
        oradio_log.error(
            "Restarting %s did not finish within %ds; not waiting any longer "
            "(systemd may still complete the restart)",
            service_name, SERVICE_RESTART_CALL_TIMEOUT,
        )
        return False

    if result.returncode != 0:
        oradio_log.error(
            "Restarting %s failed: %s", service_name, result.stderr.strip() or "no output"
        )
        return False

    # 'systemctl restart' returns once systemd's job has finished, which is not
    # necessarily once the service is serving, so ask separately until it says so.
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
        The resolver (getaddrinfo, behind socket.gethostbyname()) takes no
        timeout and ignores the default socket timeout; a failing lookup
        blocks for as long as /etc/resolv.conf says, often 10-30 s. So the
        lookup runs on a daemon worker thread and this waits at most
        DNS_TIMEOUT for it. A lookup that takes longer counts as no internet;
        its thread finishes on its own in the background and is then dropped.
        The process-wide socket.setdefaulttimeout() is left alone: changing
        it would affect every other thread's sockets too.

    Returns:
        bool: True if internet is reachable, False otherwise.
    """
    # Written by the worker, read here after join(). A one-item list rather
    # than a nonlocal so the worker needs no closure over a rebinding name.
    outcome: list[BaseException | None] = []

    def _lookup() -> None:
        """Resolve DNS_HOST and record None on success or the exception."""
        try:
            socket.gethostbyname(DNS_HOST)
            outcome.append(None)
        except (OSError, UnicodeError) as ex_err:
            outcome.append(ex_err)

    worker = Thread(target=_lookup, name="has-internet-dns", daemon=True)
    worker.start()
    worker.join(DNS_TIMEOUT)

    if not outcome:
        oradio_log.debug("Internet not available: DNS lookup took longer than %.1fs", DNS_TIMEOUT)
        return False

    if outcome[0] is not None:
        oradio_log.debug("Internet not available: %s", outcome[0])
        return False

    oradio_log.info("Internet available")
    return True

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
            shell = True,           # Run the string through /bin/sh: script is a shell command line
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

def sync_usb(reason: str) -> None:
    """
    Force all buffered writes to disk and wait until they are done.

    Uses os.sync(), which covers the SD card as well; there is nothing to gain
    from limiting it to the stick and the SD card has little pending anyway.
    Blocks until the writes are complete, so call it from a thread nobody is
    waiting on.

    Args:
        reason: Short description for the log, e.g. "web interface stopped".
    """
    start = monotonic()
    os.sync()
    oradio_log.debug("Buffered writes flushed to disk (%s) in %.0f ms",
                     reason, (monotonic() - start) * 1000)

def load_presets() -> dict[str, str]:
    """
    Retrieve the playlist names associated with the presets from a JSON file.
    Returns:
        dict[str, str]: A dictionary mapping lowercase preset_key -> listname,
                        always with all three keys preset1..preset3.
                        If a preset value is missing or invalid, listname will be an empty string "".
                        A missing, unreadable or corrupt file gives "" for all three.
                        Keys are normalized to lowercase for case-insensitive lookup.

    Every key is always present, also when the file cannot be read: callers that
    walk the presets (mpd_control.validate_presets()) must see three unusable
    presets then, not an empty dict that reads as "nothing configured, nothing
    wrong".
    """
    preset_keys = ["preset1", "preset2", "preset3"]
    try:
        with open(PRESETS_FILE, encoding='utf-8') as file:
            presets = json.load(file)
            if not isinstance(presets, dict):
                oradio_log.error("Invalid JSON format in %s: expected dict", PRESETS_FILE)
                presets = {}
    except FileNotFoundError:
        oradio_log.error("File not found at %s", PRESETS_FILE)
        presets = {}
    # UnicodeDecodeError: a corrupt file need not be valid UTF-8, and is a
    # ValueError rather than a JSONDecodeError. OSError: an I/O error reading the stick.
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as ex_err:
        oradio_log.error("Failed to read %s: %s", PRESETS_FILE, ex_err)
        presets = {}

    # Ensure all expected keys exist and are normalized
    presets_dict = {}
    for key in preset_keys:
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
        # Fetch raw value from the given presets, default to empty string if missing
        raw_value = presets.get(key, "")
        data_to_save[key] = _normalize_listname(raw_value)

    # Write the JSON file via a temporary file and a rename.
    # Writing PRESETS_FILE directly first truncates it, and without a sync
    # mount the kernel may write that truncation out before the new contents.
    # A stick pulled in between would then hold an empty presets.json.
    # With the rename the stick holds either the old or the new file.
    # No fsync here: sync_usb() runs when the web interface stops.
    tmp_file = PRESETS_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as file:
            json.dump(data_to_save, file, indent=4)
        os.replace(tmp_file, PRESETS_FILE)
        oradio_log.debug("Presets '%s' successfully saved to %s", data_to_save, PRESETS_FILE)
    except OSError as ex_err:
        oradio_log.error("Failed to write presets to '%s'. Error: %s", PRESETS_FILE, ex_err)
        try:
            os.remove(tmp_file)
        except OSError:
            pass

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
