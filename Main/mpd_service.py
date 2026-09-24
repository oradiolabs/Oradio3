#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 10, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@references:
    https://www.musicpd.org/
    https://python-mpd2.readthedocs.io/

@summary:
    Oradio MPD service module
    - Automatic reconnect if MPD is down or connection drops
    - Retries commands logging error on failure
    Terminology:
    - directory/directories: read-only collection(s) of music files
    - playlist/playlists: collection(s) which can be created, saved, edited and deleted
    - mpdlist/mpdlists: the combination of directories and playlists
    - current: the directory/playlist in the playback queue
"""
from typing import Any
import socket
from time import monotonic, sleep
from threading import RLock  # Safeguard against concurrent access; callers using one thread or process per instance do not require it.
# Use MPDConnectionError because mpd2 raises a different ConnectionError than Python's built-in one
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### Oradio modules ######################################
from log_service import oradio_log
from messaging import (
    Incidents,
    IncidentMessage,
    MPD_SOURCE,
    MPD_CONNECT_FAILED,
    MPD_EXECUTE_FAILED,
)

##### LOCAL constants #####################################
MPD_HOST     = "localhost"
MPD_PORT     = 6600
MPD_RETRIES  = 3
MPD_BACKOFF  = 1    # seconds between retry attempts, to avoid hammering the MPD server

# Wall-clock cap on one connect burst. MPD_RETRIES x MPD_BACKOFF is the nominal
# cost, but connect() can block as well, so the deadline is what actually
# bounds the total. Kept short: on the start-up path a caller waiting here is
# holding up the state machine, and MPD arriving a second later is handled by
# the breaker closing again rather than by waiting longer here.
MPD_CONNECT_DEADLINE = 4    # seconds

# How long commands fail fast after a connect burst failed. Every command
# issued in this window returns None immediately instead of paying the burst
# again. Without it, one command against a down MPD costs MPD_RETRIES bursts --
# roughly twelve seconds -- and MPDControl issues several in a row.
MPD_COOLDOWN = 5    # seconds

# Connect and read timeout for mpd_is_ready(). Short: it asks a local server to
# say hello, and a local answer that takes longer than this is a no.
MPD_PROBE_TIMEOUT = 0.5     # seconds

# Enough for "OK MPD <version>\n" and nothing more. The probe reads once and
# closes; it is not a client and must not consume anything a client would want.
MPD_GREETING_BYTES = 64
LOCK_TIMEOUT = 5    # seconds

# Socket timeout for every command on the MPDClient (issue #544).
#
# python-mpd2's default is None: block forever. MPD answers from one event loop,
# and some commands (stop, clear, load) wait there for its player and decoder
# threads. A decoder stuck in a read from a USB stick that was pulled - or from
# a mount left over from the stick's previous insertion - holds that loop, and
# with no timeout the calling thread waits with it. The caller is the command
# handler, holding the state-machine lock, so every button press after that
# queues behind it and the Oradio is dead until power is pulled.
#
# Generous, because it must never fire on a healthy MPD: loading or adding a
# large directory on a Pi 3A+ is the slowest thing asked of it.
#
# Does NOT apply to "idle", which mpd_monitor issues through this same client
# and which legitimately blocks until something happens: python-mpd2 switches
# the socket to idletimeout (None) for the idle reply and back afterwards.
MPD_COMMAND_TIMEOUT = 15    # seconds

def mpd_is_ready(timeout: float = MPD_PROBE_TIMEOUT) -> bool:
    """
    Whether MPD is answering, without touching any MPDClient.

    Connects, reads MPD's greeting and closes. The greeting is the point: a
    successful TCP connect only proves the socket is bound, which happens well
    before mpd is serving commands. A probe that stops there reports ready too
    early, and the work it releases then holds MPDService's lock while every
    command inside it waits on a server that is not answering yet -- so a
    button press queued behind it waits out LOCK_TIMEOUT and comes back
    empty-handed. Reading "OK MPD <version>" proves the other side is past
    that point.

    Deliberately NOT a method on MPDService: the question is about the server,
    and answering it through a shared MPDService means two callers opening a
    connection on one MPDClient at the same time, which corrupts the client.

    Its own short-lived socket, so it shares nothing and has no side effects --
    what DeferredStarter asks of a predicate.

    Args:
        timeout: Seconds to wait for the connection and for the greeting.

    Returns:
        True when MPD answered with its greeting.
    """
    try:
        with socket.create_connection((MPD_HOST, MPD_PORT), timeout=timeout) as probe:
            probe.settimeout(timeout)
            greeting = probe.recv(MPD_GREETING_BYTES)
    except OSError:
        return False

    # b"OK MPD 0.23.15\n". Matched on the prefix only: the version varies and
    # nothing here cares which one it is.
    return greeting.startswith(b"OK MPD")

class MPDService:
    """
    Thread-safe class for interacting with an MPD (Music Player Daemon) server.

    Uses a Lock to prevent concurrent access from multiple threads. Callers
    that use only one thread or process per instance do not require this guard,
    but it is retained as a safeguard.

    Features:
        - Automatic connection and reconnection to the MPD server.
        - Retry logic with backoff for commands and connections.
        - Circuit breaker: after a failed connect burst, commands fail fast
          for a cooldown instead of each paying the burst again.
        - Safe execution of MPD commands with optional auto-reconnect.
        - Locking to prevent concurrent access from multiple threads.
        - Logging of commands, connection attempts, and errors.
    """
    def __init__(self, crossfade: int | None = None) -> None:
        """
        Initialise the MPDService and connect to the MPD server.

        Args:
            crossfade (int | None): Optional crossfade duration in seconds.
                                    If None, crossfade will not be configured.
        """
        # RLock, not Lock: _execute() holds this while handling a connection
        # error, and the _connect_client() it calls from there sets the
        # crossfade through _execute() again. With a plain Lock that inner call
        # waits LOCK_TIMEOUT seconds on a lock its own thread already holds,
        # once per command -- which is what a start-up against an MPD that is
        # not up yet turns into minutes of "Timeout waiting for MPD lock".
        #
        # Re-entrancy applies to the holding thread only; other threads still
        # queue, so the protection this lock exists for is unchanged.
        self._lock = RLock()
        self._crossfade = crossfade
        self._client = MPDClient()
        # Applies to connect() and to every command read. See MPD_COMMAND_TIMEOUT.
        self._client.timeout = MPD_COMMAND_TIMEOUT

        # Circuit breaker state. _unavailable_until is a monotonic timestamp:
        # while now() is below it the breaker is open and commands fail fast.
        # _outage_reported keeps one outage to one incident, however many
        # commands run into it.
        self._unavailable_until = 0.0
        self._outage_reported = False

        # No connect here.
        #
        # _execute() connects on its first command and reconnects whenever the
        # link drops, so connecting in the constructor buys nothing except the
        # wait: on a cold boot oradio_control reaches this before mpd.service
        # is accepting connections, and every caller then pays MPD_RETRIES
        # attempts with MPD_BACKOFF between them -- measured at 4.1 seconds,
        # sitting on the path to the start-up tune.
        #
        # Constructing an MPDService is now free, and the cost of an absent MPD
        # is paid by whoever issues the first command, where the circuit
        # breaker bounds it.

##### Helpers #############################################

    def _is_connected(self) -> bool:
        """Return True if the client is currently connected to MPD, False otherwise."""
        try:
            self._client.ping()     # pylint: disable=no-member
            return True
        except (MPDConnectionError, BrokenPipeError, OSError):
            return False

    def _drop_connection(self) -> None:
        """
        Close the connection without asking MPD anything.

        For a connection that can no longer be trusted: after a timed-out
        command its reply may still be on the way. disconnect() only closes the
        local socket, so it cannot block on the server that just failed to
        answer.
        """
        try:
            self._client.disconnect()
        except Exception:   # pylint: disable=broad-exception-caught
            pass            # Already closed or half-open; either way it is gone

    def _open_circuit(self) -> None:
        """
        Trip the breaker after a failed connect burst.

        The incident is published once per outage, not once per burst: MPD
        being down for a minute is one fault, and re-publishing it every
        MPD_COOLDOWN seconds would turn the remote monitoring service into a
        repeater for it.
        """
        self._unavailable_until = monotonic() + MPD_COOLDOWN
        oradio_log.error(
            "Failed to connect to MPD; commands fail fast for the next %ds", MPD_COOLDOWN
        )

        if not self._outage_reported:
            self._outage_reported = True
            Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_CONNECT_FAILED))

    def _close_circuit(self) -> None:
        """Reset the breaker after a successful connect, and log the recovery."""
        self._unavailable_until = 0.0

        if self._outage_reported:
            oradio_log.info("MPD reachable again")
            self._outage_reported = False

    def _connect_client(self) -> None:
        """
        Establish a connection to MPD with retries and backoff.

        Cleans up any stale connection before each attempt, then connects
        and optionally sets the crossfade value.

        Bounded two ways: MPD_RETRIES attempts, and MPD_CONNECT_DEADLINE of
        wall clock. If the burst fails the breaker is opened, so the next
        caller returns at once rather than repeating it.
        """
        # Half-open: while the breaker is open, do not pay for another burst.
        # _is_available() closing again on time is what makes this a delay
        # rather than a permanent give-up.
        if not self._is_available():
            oradio_log.debug("MPD marked unavailable; skipping connect attempt")
            return

        deadline = monotonic() + MPD_CONNECT_DEADLINE

        for attempt in range(1, MPD_RETRIES + 1):
            try:
                if self._is_connected():
                    oradio_log.debug("MPD client already connected, skipping connect")
                else:
                    # Ensure any stale connection is cleanly closed before reconnecting.
                    try:
                        self._client.disconnect()
                    except MPDConnectionError:
                        pass    # Already disconnected; safe to ignore

                    self._client.connect(MPD_HOST, MPD_PORT)
                    oradio_log.info("Connected to MPD on %s:%s", MPD_HOST, MPD_PORT)

                # Reset the breaker before the crossfade command below, which
                # goes through _execute() and would otherwise fail fast on a
                # breaker this very call has just proved stale.
                self._close_circuit()

                # Set crossfade if specified
                if self._crossfade is not None:
                    _ = self._execute("crossfade", self._crossfade, allow_reconnect=False)
                    oradio_log.info("MPD crossfade set to %d", self._crossfade)

                return  # Connection successful

            except (MPDConnectionError, BrokenPipeError, OSError) as ex_err:
                oradio_log.warning(
                    "Connection attempt %d/%d failed (%s). Retrying...",
                    attempt, MPD_RETRIES, ex_err,
                )

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                oradio_log.error(
                    "Unexpected error during MPD connection attempt %d/%d: %s",
                    attempt, MPD_RETRIES, ex_unexpected,
                )

            # Stop early rather than sleep past the deadline: the caller is
            # often the state machine, and every second here is a second of
            # unresponsive Oradio.
            if monotonic() + MPD_BACKOFF >= deadline:
                oradio_log.warning("MPD connect deadline of %ds reached", MPD_CONNECT_DEADLINE)
                break

            sleep(MPD_BACKOFF)

        # Burst exhausted: open the breaker so the next caller fails fast.
        self._open_circuit()

    # The extra exits are the circuit breaker's: fail fast on entry, and give up
    # as soon as a reconnect has opened it. Both replace time spent waiting.
    def _execute(self, command: str, *args, allow_reconnect: bool = True, **kwargs) -> Any | None:  # pylint: disable=too-many-return-statements,too-many-branches
        """
        Execute an MPD command safely with retry logic and lock protection.

        Retries only on connection-related errors. Some expected CommandErrors
        (e.g. "Not playing") are silently ignored; others are logged as errors.
        Acquires the instance lock with a timeout on each attempt to prevent
        deadlocks.

        Args:
            command (str):          MPD command to execute.
            *args:                  Positional arguments passed to the command.
            allow_reconnect (bool): If True, a lost connection triggers
                                    _connect_client() before retrying. Pass False
                                    when calling from _connect_client() itself
                                    to prevent infinite recursion. Default is True.
            **kwargs:               Keyword arguments passed to the command.

        Returns:
            The command result on success, or None if:
            - the command name is not a valid MPD command,
            - the command fails with a CommandError (expected or not), or
            - all retry attempts are exhausted.
        """
        # Fail fast while the breaker is open. Without this, one command
        # issued during an MPD outage pays MPD_RETRIES reconnect bursts before
        # returning None -- and MPDControl issues several in a row, which on a
        # cold boot is the difference between the start-up tune playing at 8s
        # and at 40s.
        if not self._is_available():
            oradio_log.debug("MPD unavailable; skipping command '%s'", command)
            return None

        function = getattr(self._client, command, None)
        if not callable(function):
            oradio_log.error("Invalid MPD command: '%s'", command)
            return None

        for attempt in range(1, MPD_RETRIES + 1):
            acquired = False
            try:
                # Acquire lock with timeout (therefore not using 'with').
                acquired = self._lock.acquire(timeout=LOCK_TIMEOUT)     # pylint: disable=consider-using-with
                if not acquired:
                    oradio_log.warning(
                        "Timeout waiting for MPD lock (attempt %d/%d, command '%s')",
                        attempt, MPD_RETRIES, command,
                    )
                else:
                    return function(*args, **kwargs)

            except CommandError as ex_cmd:
                # Some CommandErrors are expected (e.g. "Not playing"); ignore those and log the rest.
                ignored_errors = ["Not playing"]
                msg = str(ex_cmd)
                if any(err in msg for err in ignored_errors):
                    oradio_log.warning(
                        "Ignoring expected CommandError: '%s' for command '%s'",
                        msg, command,
                    )
                else:
                    oradio_log.error("MPD command '%s' failed: %s", command, ex_cmd)
                return None

            except TimeoutError:
                # MPD accepted the command and never answered. Not retried:
                # each retry would hold the caller - usually the state machine
                # - for another MPD_COMMAND_TIMEOUT against a server that is
                # wedged, not flaky.
                #
                # The connection is dropped, not reused: the reply may still
                # arrive, and the next command would read it as its own.
                # Opening the breaker makes the commands right behind this one
                # fail fast instead of each paying the timeout again.
                oradio_log.error(
                    "MPD did not answer '%s' within %ds; dropping connection",
                    command, MPD_COMMAND_TIMEOUT,
                )
                self._drop_connection()
                self._open_circuit()
                Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_EXECUTE_FAILED))
                return None

            except (MPDConnectionError, ProtocolError, BrokenPipeError, ConnectionResetError) as ex_err:
                oradio_log.warning(
                    "MPD connection error '%s' for command '%s'. Retry %d/%d...",
                    ex_err, command, attempt, MPD_RETRIES,
                )
                if allow_reconnect:
                    self._connect_client()

                    # The reconnect gave up and opened the breaker. Burning the
                    # remaining attempts against a server that is not there
                    # only costs the caller time.
                    if not self._is_available():
                        return None

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                oradio_log.error("Unexpected error executing MPD command '%s': %s", command, ex_unexpected)
                Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_EXECUTE_FAILED))
                return None

            finally:
                if acquired:
                    self._lock.release()

            sleep(MPD_BACKOFF)

        # All retries exhausted
        oradio_log.error("Failed to execute MPD command '%s' after %d retries", command, MPD_RETRIES)
        Incidents.publish(IncidentMessage(MPD_SOURCE, MPD_EXECUTE_FAILED))
        return None

    def _is_available(self) -> bool:
        """
        Return False while the circuit breaker is open, i.e. while a recent
        connect burst failed and the cooldown has not expired.

        Private: it answers a question about the breaker, not about MPD. It is
        True for a server that has never been contacted, because nothing has
        failed yet -- so a caller asking "is MPD up" and reaching for this gets
        a yes before a single connection was attempted.

        The module-level mpd_is_ready() answers "is MPD up" without a
        client at all. This one exists to keep the fail-fast path inside this
        class cheap.
        """
        return monotonic() >= self._unavailable_until

##### Public API ##########################################

    def get_stats(self) -> dict[str, Any]:
        """
        Retrieve and combine MPD playback statistics and current status.

        Merges the results of the MPD 'stats' and 'status' commands into a
        single dictionary. If either command fails after all retries, its
        contribution is an empty dict; the failure is logged and published
        to the Incidents topic by _execute().

        Returns:
            dict: Merged dictionary of statistics and status data, or a
                  partial/empty dict if one or both commands failed.
        """
        stats  = self._execute("stats")  or {}
        status = self._execute("status") or {}
        return stats | status

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    from messaging import DebugMessageHandler       # pylint: disable=ungrouped-imports

    print("\nStarting test program...\n")

    # Subscribe to error topic so published messages are printed to console
    incident_handler = DebugMessageHandler(Incidents.subscribe())

    # Instantiate MPD service and print current stats
    mpd_service = MPDService()
    info = mpd_service.get_stats()
    print("\nRunning MPDService standalone - MPD info:")
    for key, value in info.items():
        print(f"{key:>20} : {value}")

    # Stop receiving messages
    Incidents.unsubscribe(incident_handler.get_queue())

    # Signal the thread to exit and confirm it has exited
    incident_handler.stop()

    print("\nExiting test program...\n")
