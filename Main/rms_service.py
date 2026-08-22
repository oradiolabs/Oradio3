#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on February 8, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides communication with the Remote Monitoring Service (RMS).

    When WiFi connectivity becomes available, a periodic heartbeat is
    started and a one-time SYS_INFO message containing hardware and
    software information is sent. The heartbeat stops when WiFi is lost.

    Any other service in the application (e.g. incident_service) can also
    use RMService.send_message(INCIDENT, incident) to report an
    IncidentMessage to RMS, attaching the current log files for context.
    Like HEARTBEAT/SYS_INFO, this requires start() to have been called;
    RMS is expected to start early enough in the boot sequence that this
    is not a practical limitation.

    Helper functions collect Raspberry Pi telemetry and software version
    information. Outgoing POST requests are protected by a simple
    exponential backoff retry mechanism. A failing POST marks the server
    unreachable, after which each message makes a single probe attempt and
    logs one line until a POST succeeds or WiFi connects.
"""
import json
import subprocess
from time import sleep
from threading import Timer
from datetime import datetime
from platform import python_version
from contextlib import ExitStack
from multiprocessing import Queue, Lock
from requests import post, RequestException, Response, Timeout

##### Oradio modules ######################################
from singleton import singleton
from utilities import get_serial
from log_service import oradio_log, ORADIO_LOG_PATH
from messaging import (
    Commands,
    Incidents,
    IncidentMessage,
    MessageHandlerTemplate,
    WIFI_SOURCE,
    WIFI_CONNECTED,
    WIFI_DISCONNECTED,
    WIFI_ACCESS_POINT,
    RMS_SOURCE,
    RMS_START_FAILED,
    RMS_POST_FAILED,
)

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    RMS_SERVER_URL,
    RMS_SERVER_KEY,
)

##### LOCAL constants #####################################
# RMS message type identifiers
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
INCIDENT  = 'INCIDENT'

# Path to the JSON file written by the deployment pipeline with version info
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# How often the heartbeat is sent (seconds); currently once per hour
HEARTBEAT_REPEAT = 60 * 60

# Remote Monitoring Service endpoint and HTTP POST tuning parameters
MAX_RETRIES    = 3    # Maximum number of POST attempts before giving up
BACKOFF_FACTOR = 2    # Base for exponential backoff: delay = BACKOFF_FACTOR ** attempt (1s, 2s, 4s)
POST_TIMEOUT   = 30   # Per-attempt HTTP timeout in seconds. Generous because RMS may
                      # run its notification and retention routines inside the POST
                      # before responding: giving up early would treat a stored
                      # record as a failure and post it again on the next attempt.

##### RMS reachability state ##############################

class _RmsReachability:
    """
    Cached view of whether the RMS server is reachable.

    Cleared when a POST exhausts its retries, set again on the first
    successful POST and when WiFi connects. While cleared, a POST makes a
    single probe attempt rather than the full retry/backoff cycle, and
    RMS's own incidents are dropped rather than POSTed.

    Never instantiated; use the classmethods.

    Attributes:
        reachable: Whether the server is believed to be reachable.
        lock: Serialises access to reachable across the heartbeat timer
            thread, the WiFi handler thread, and any caller of
            send_message().
    """
    reachable = True
    lock = Lock()

    @classmethod
    def is_reachable(cls) -> bool:
        """
        Return whether the RMS server is believed to be reachable.

        Returns:
            bool: The current reachability state.
        """
        with cls.lock:
            return cls.reachable

    @classmethod
    def update(cls, reachable: bool) -> bool:
        """
        Set the reachability state and report whether it changed.

        The test and the assignment share one lock, so concurrent callers
        cannot both observe the same transition.

        Args:
            reachable: The new state.

        Returns:
            bool: True if this call changed the state, False if it already
            held that value. Callers log and publish on the transition
            only, keeping a prolonged outage to one line per message.
        """
        with cls.lock:
            changed = cls.reachable != reachable
            cls.reachable = reachable
        return changed

##### Helpers #############################################

def _get_temperature() -> str:
    """
    Return the Raspberry Pi SoC temperature in degrees Celsius.

    Returns:
        str: Temperature in °C, or "Unsupported platform" if unavailable.
    """
    result = subprocess.run(
        ["vcgencmd", "measure_temp"],
        capture_output=True, text=True, check=False,
    )
    # Output format: "temp=42.8'C" — slice characters 5–9 to extract the value
    temperature = result.stdout.strip()[5:9] if result.returncode == 0 else ""
    return temperature or "Unsupported platform"

def _get_rpi_version() -> str:
    """
    Return the Raspberry Pi model string.

    Returns:
        str: Human-readable model description, or "Unsupported platform" if unavailable.
    """
    result = subprocess.run(
        ["cat", "/proc/cpuinfo"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return "Unsupported platform"
    for line in result.stdout.splitlines():
        if line.startswith("Model"):
            return line.split(":", 1)[1].strip()
    return "Unsupported platform"

def _get_os_version() -> str:
    """
    Return the operating system description.

    Returns:
        str: OS name and version, or "Unsupported platform" if unavailable.
    """
    result = subprocess.run(
        ["lsb_release", "-a"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return "Unsupported platform"
    for line in result.stdout.splitlines():
        if line.startswith("Description:"):
            return line.split(":", 1)[1].strip()
    return "Unsupported platform"

def _get_sw_version() -> str:
    """
    Return the installed Oradio software version.

    Returns:
        str: Software version string, or "Invalid SW version" if the
        version file is missing or invalid.
    """
    try:
        with open(SOFTWARE_VERSION_FILE, encoding="utf-8") as file:
            data = json.load(file)
        return data["dtstamp"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SOFTWARE_VERSION_FILE)
        return "Invalid SW version"

def _extract_command(response: Response) -> str | None:
    """
    Read the pending command out of an RMS response body.

    RMS wraps its payload in the standard API envelope, so the command
    arrives as data.command:

        {"success": true, ..., "data": {"stored": true, "command": "..."}}

    The unwrapped top-level shape is accepted as well, so a response that
    is relayed rather than returned directly still works.

    Args:
        response: The successful response returned by _post_with_retry().

    Returns:
        The command to run, or None if the body carried none or could not
        be parsed as JSON.
    """
    try:
        body = response.json()
    except ValueError:
        oradio_log.error("RMS response was not JSON: %s", response.text[:200])
        return None

    if not isinstance(body, dict):
        return None

    data = body.get("data")
    command = body.get("command") or (data.get("command") if isinstance(data, dict) else None)

    if command is None:
        return None

    command = str(command).strip()

    return command or None

def _handle_response_command(response: Response) -> None:
    """
    Execute a command returned by the RMS server, if there was one.

    RMS clears a pending command as soon as it hands it out, so it is
    delivered exactly once: a command that is not run here is not offered
    again on the next heartbeat.

    Warning:
        Executing commands received from a remote system is inherently
        risky and should eventually be replaced by validated commands
        handled elsewhere.

    Args:
        response: The successful response returned by _post_with_retry().
    """
    command = _extract_command(response)

    if command is not None:
        # Pass command to linux shell for execution
        oradio_log.debug("Run command '%s' from RMS server", command)
        try:
            # executable must be set explicitly; without it Python falls
            # back to /bin/sh which may lack bash-specific features.
            # text=True decodes stdout/stderr to str for readable logging.
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                check=True,
                executable="/usr/bin/bash",
                text=True,
            )
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error(
                "shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s",
                command, ex_err.returncode, ex_err.stdout, ex_err.stderr
            )

def _post_with_retry(
    payload_info: dict,
    attach_log_files: bool = False,
    context: str = "message",
) -> Response | None:
    """
    POST payload_info to the RMS server, retrying on failure.

    Shared across the message types handled by
    WifiMessageHandler.send_message(): all POST to RMS_SERVER_URL under the
    same MAX_RETRIES/BACKOFF_FACTOR/POST_TIMEOUT policy. They differ only
    in whether log files are attached and in what happens with a successful
    response (a heartbeat acts on a returned command, the others do not),
    both of which stay with the caller.

    Failures are split the way the crash action script splits them, since
    the two classes call for different responses:

    - 4xx means the server answered and rejected this request. Retrying
      sends the identical request and earns the identical rejection, so
      there is no retry and the reachability state is left alone: 401 (key
      rotated) and 413 (payload too large) are configuration faults, not an
      outage. No incident is published either -- publishing one would POST
      an incident that is rejected in turn, publishing another.
    - 5xx and transport errors (DNS, TLS, timeout) may clear on their own,
      so these retry with backoff and, once exhausted, clear the
      reachability state and publish RMS_POST_FAILED.

    Retries apply while _RmsReachability reports the server as reachable.
    Once it does not, each call makes a single probe attempt, so a dead
    server costs one timeout rather than the full retry and backoff cycle
    while recovery is still picked up on the next message.

    Args:
        payload_info:     Form fields to POST.
        attach_log_files: If True, attach every *.log* file in
                           ORADIO_LOG_PATH on each attempt, rotated logs
                           included. Files are (re)opened fresh per attempt
                           inside the loop, since a file object already
                           consumed by a failed attempt can't be resent
                           as-is.
        context:          Short label used in log messages, e.g.
                           "message" or "incident".

    Returns:
        The successful requests.Response, or None if the request was
        rejected with a 4xx or the retryable attempts were exhausted.
    """
    attempts = MAX_RETRIES if _RmsReachability.is_reachable() else 1

    for attempt in range(1, attempts + 1):
        failure = None
        try:
            with ExitStack() as stack:
                payload_files = None
                if attach_log_files:
                    send_files = ORADIO_LOG_PATH.glob("*.log*")
                    payload_files = {f.name: (f.name, stack.enter_context(f.open("rb"))) for f in send_files}
                response = post(
                    url=RMS_SERVER_URL,
                    headers={"X-Api-Key": RMS_SERVER_KEY},
                    data=payload_info,
                    files=payload_files,
                    timeout=POST_TIMEOUT
                )
        except (RequestException, Timeout) as ex_err:
            failure = ex_err
        else:
            if 400 <= response.status_code < 500:
                # The server answered, so it is reachable; the request
                # itself is what it refused. Recorded with the status code
                # because the fix differs per code, and returned without
                # retrying or publishing an incident.
                oradio_log.error(
                    "POST %s rejected: HTTP %d, body: %s",
                    context, response.status_code, response.text[:200] or "<none>"
                )
                if _RmsReachability.update(True):
                    oradio_log.info("RMS server reachable again")
                return None

            if response.status_code >= 500:
                # Server-side and possibly transient, so treated like a
                # transport failure and retried.
                failure = f"HTTP {response.status_code}"

        if failure is None:
            if _RmsReachability.update(True):
                oradio_log.info("RMS server reachable again")
            return response  # POST succeeded; exit the retry loop

        # Per-attempt detail is informative only while more attempts follow
        if attempts > 1:
            oradio_log.warning("Attempt %d failed to POST %s: %s", attempt, context, failure)

        if attempt < attempts:
            # Wait before retrying; delay grows exponentially with each attempt
            sleep(BACKOFF_FACTOR ** attempt)
            continue

        # Clear the state before publishing, so send_message() recognises
        # the incident published here as undeliverable and drops it
        # instead of starting another POST.
        if _RmsReachability.update(False):
            oradio_log.error("Failed to POST %s: %s", context, failure)
            Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_POST_FAILED))
        else:
            # Outage already reported: one line per message
            oradio_log.error("Failed to POST %s: RMS server still unreachable", context)
        return None

    return None  # Unreachable (loop always returns), keeps type checkers happy

class Heartbeat(Timer):
    """
    Timer that repeatedly invokes a callback.

    Extends threading.Timer, overriding run() so the callback executes
    immediately on start and then repeats every interval seconds until
    cancel() is called.

    Use the classmethods start_heartbeat() and stop_heartbeat() rather
    than instantiating directly; they keep at most one timer active.

    Note:
        Each start_heartbeat() call must be free to construct a fresh
        instance, because a Timer thread is consumable and cannot run
        again once it has finished or been cancelled. Keep this class
        undecorated by @singleton, which would pin one instance for the
        lifetime of the process.

    Attributes:
        instance: The active timer, or None when no heartbeat is running.
        start_lock: Serialises start/stop calls so they cannot race on
            instance.
    """
    instance = None
    start_lock = Lock()

    def __init__(self, interval, function, args=None, kwargs=None) -> None:
        """
        Initialise the heartbeat timer.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (tuple, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        super().__init__(interval, function, args=args, kwargs=kwargs)

    def run(self) -> None:
        """
        Execute the callback immediately and repeat until cancelled.

        Exceptions raised by the callback are caught and logged so that the
        timer thread remains alive.
        """
        while not self.finished.is_set():
            try:
                self.function(*self.args, **self.kwargs)
            # Catch all non-system exceptions: we must not let an unpredictable callback
            # error kill the timer thread.
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Heartbeat execution failed: %s", ex_err)

            # Block for *interval* seconds; returns True early if cancel() is called
            if self.finished.wait(self.interval):
                break

    @classmethod
    def start_heartbeat(cls, interval, function, args=None, kwargs=None) -> None:
        """
        Stop any running heartbeat and start a new one.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (tuple, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        with cls.start_lock:
            # Cancel and discard the previous instance before creating a new one
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None

            cls.instance = cls(interval, function, args=args, kwargs=kwargs)

            # Daemon thread: exits automatically when the main program exits
            cls.instance.daemon = True
            cls.instance.start()
            oradio_log.info("Heartbeat started")

    @classmethod
    def stop_heartbeat(cls) -> None:
        """
        Cancel the running heartbeat timer, if any.

        Thread-safe: uses start_lock to serialise concurrent calls.
        Does nothing if no heartbeat is currently running.
        """
        with cls.start_lock:
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None
                oradio_log.info("Heartbeat stopped")
            else:
                oradio_log.debug("No heartbeat to stop")

class WifiMessageHandler(MessageHandlerTemplate):
    """
    Handle WiFi state change messages and drive heartbeat and RMS reporting.

    Subscribes to the COMMAND topic filtered to WiFi messages. On a
    WIFI_CONNECTED event the heartbeat timer is started and a one-time
    SYS_INFO message is sent to the RMS server. On a WIFI_DISCONNECTED
    event the heartbeat timer is stopped.

    send_message() also handles INCIDENT, used by other services (e.g.
    incident_service) via RMService.send_message() to report an
    IncidentMessage to RMS. All three message types require this handler
    to exist (i.e. RMService.start() to have been called) and, for
    SYS_INFO/INCIDENT, WiFi to currently be connected.
    """
    def __init__(self, queue: Queue) -> None:
        """
        Initialise the WiFi message handler.

        Args:
            queue: Subscription queue filtered to WiFi messages.
        """
        # Cache serial number once; used in every outgoing RMS message
        self._serial = get_serial()

        # Tracks the most recently observed WiFi state; updated in
        # _handle_message() below. Starts False since no WIFI_* message
        # has been processed yet at construction time.
        self._wifi_connected = False

        # Initialise base class and start the worker thread
        super().__init__(queue)

    @property
    def wifi_connected(self) -> bool:
        """Whether WiFi is currently connected, per the last WIFI_* message processed."""
        return self._wifi_connected

    def _handle_message(self, message) -> None:
        """
        Handle an incoming WiFi state change message.

        Args:
            message: The received message from the queue.
        """
        if message.message == WIFI_DISCONNECTED:
            self._wifi_connected = False
            Heartbeat.stop_heartbeat()
            oradio_log.debug("WiFi disconnected. Heartbeat stopped.")

        elif message.message == WIFI_CONNECTED:
            self._wifi_connected = True
            # A new connection may resolve an earlier failure, so allow the
            # next POST a full retry cycle rather than a single probe
            _RmsReachability.update(True)
            Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, self.send_message, args=(HEARTBEAT,))
            # Immediately report hardware/software identity on every new connection
            self.send_message(SYS_INFO)
            oradio_log.debug("WiFi connected. Heartbeat started and system info sent.")

        elif message.message == WIFI_ACCESS_POINT:
            # Heartbeat cannot be active, info message cannot be sent
            self._wifi_connected = False

        else:
            oradio_log.error("Unexpected message: %s", message)

    def send_message(self, msg_type: str, incident: IncidentMessage | None = None) -> None:
        """
        Build and send a message to the RMS server.

        HEARTBEAT and SYS_INFO carry runtime/hardware telemetry. INCIDENT
        reports an IncidentMessage from another service, attaching the
        current log files for context.

        Only a HEARTBEAT response is inspected for a pending command: that
        is the one message type RMS attaches one to, so parsing any other
        response for it would never find anything.

        Only attempted while WiFi is currently known to be connected; if not,
        nothing is sent and a debug line is logged instead, since attempting
        a POST with no network would just burn through the full retry/backoff
        cycle before failing anyway.

        Args:
            msg_type: HEARTBEAT, SYS_INFO, or INCIDENT.
            incident: Required when msg_type is INCIDENT (ignored
                      otherwise) -- the IncidentMessage to report.
        """
        if not self._wifi_connected:
            oradio_log.debug("WiFi not available; not sending %s message", msg_type)
            return

        # Base fields present in every message type
        payload_info = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : self._serial,
            'type'     : msg_type,
        }

        # Append lightweight runtime telemetry for periodic sign-of-life messages
        if msg_type == HEARTBEAT:
            payload_info['temperature'] = _get_temperature()

        # Append full hardware/software identification for onboarding messages
        elif msg_type == SYS_INFO:
            payload_info['sw_version'] = _get_sw_version()
            payload_info['python']     = python_version()
            payload_info['rpi']        = _get_rpi_version()
            payload_info['rpi-os']     = _get_os_version()

        # Report an incident from another service, attaching current logs
        elif msg_type == INCIDENT:
            if incident is None:
                oradio_log.error("send_message(INCIDENT) requires an IncidentMessage")
                return

            # An incident about RMS can only be delivered while RMS answers.
            # Dropping it here keeps a failed POST from publishing an
            # incident that triggers another POST; the failure is already in
            # the local log.
            if incident.source == RMS_SOURCE and not _RmsReachability.is_reachable():
                oradio_log.debug("RMS unreachable; not reporting: %s", incident.message)
                return

            payload_info['source']  = incident.source
            payload_info['message'] = incident.message
            # RMS attaches a command to heartbeats only, so the response
            # here is unused
            _post_with_retry(payload_info, attach_log_files=True, context="incident")
            return

        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return  # Nothing to POST; exit early

        response = _post_with_retry(payload_info, context="message")
        if response is None:
            # Rejected, or all retries failed; _post_with_retry() has
            # already logged it and published an incident where warranted
            return

        # RMS attaches a pending command to a heartbeat response only
        if msg_type == HEARTBEAT:
            _handle_response_command(response)

@singleton
class RMService:
    """
    Manage communication with the Remote Monitoring Service (RMS).

    Subscribes to WiFi connectivity events and delegates all message
    handling -- HEARTBEAT, SYS_INFO, and INCIDENT alike -- to an internal
    WifiMessageHandler. All three require start() to have been called, so
    start RMS early in the application's boot sequence, ahead of any
    service that may raise an incident. See
    WifiMessageHandler.send_message() for the per-type detail.

    Construction only sets up internal state; the WiFi subscription and
    the handler's worker thread begin at the first start() call. Callers
    therefore choose when subscribing and threading start, and may
    stop() and start() again later.
    """
    def __init__(self) -> None:
        """
        Initialise the service.

        No subscription is made and no thread is started here; call
        start() to begin operation.
        """
        self._queue: Queue | None = None
        self._handler: WifiMessageHandler | None = None

    def start(self) -> None:
        """
        Subscribe to WiFi state change events and start the handler thread.

        Idempotent: calling start() when the service is already running is
        a no-op. If handler creation fails, any partial subscription is
        rolled back and an incident is published.
        """
        if self._handler is not None:
            oradio_log.debug("RMS service already running")
            return

        # Subscribe to WiFi messages only
        self._queue = Commands.subscribe(sources=(WIFI_SOURCE,))

        # Start queue listener thread
        try:
            self._handler = WifiMessageHandler(self._queue)
            oradio_log.info("RMS service started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("RMS service failed to start: %s", ex_err)
            # Roll back the subscription so a retry via start() starts clean
            Commands.unsubscribe(self._queue)
            self._queue = None
            Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_START_FAILED))

    def send_message(self, msg_type: str, incident: IncidentMessage | None = None) -> None:
        """
        Send a message to the RMS server.

        Thin delegator to the internal WiFi-driven handler, letting
        callers and the interactive test menu trigger sends on the
        RMService instance without touching internal state. See
        WifiMessageHandler.send_message() for what each type does and
        which require WiFi to be connected.

        Args:
            msg_type: HEARTBEAT, SYS_INFO, or INCIDENT.
            incident: Required when msg_type is INCIDENT (ignored
                      otherwise) -- the IncidentMessage to report.
        """
        if self._handler is None:
            oradio_log.error("RMS service not started; cannot send %s", msg_type)
            return

        self._handler.send_message(msg_type, incident)

    def stop(self) -> None:
        """
        Shut down the RMS service cleanly.

        Stops the heartbeat timer, unsubscribes from the command queue,
        and signals the worker thread to exit. Does nothing if the service
        was never started (or has already been stopped).
        """
        if self._handler is None:
            oradio_log.debug("RMS service not running")
            return

        # Invariant: start() always sets _queue and _handler together, and
        # every reset path (here and the rollback in start()) clears both
        # together, so _handler being set guarantees _queue is too. Asserted
        # so mypy can narrow _queue from Optional[Queue] to Queue below.
        assert self._queue is not None

        Heartbeat.stop_heartbeat()
        Commands.unsubscribe(self._queue)
        self._handler.stop()
        self._handler = None
        self._queue = None
        oradio_log.info("RMS service stopped")

##### Stand-alone entry point #############################

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from wifi_service import WifiService

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """
        Run an interactive command-line menu for manual RMService testing.

        Creates a WifiService and RMService instance, then
        presents a numbered menu that lets a developer exercise each public
        method without running the full Oradio application stack.
        """
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Test sending HEARTBEAT message\n"
            " 2-Test sending SYS_INFO message\n"
            " 3-Test sending INCIDENT message\n"
            " 4-Start heartbeat timer\n"
            " 5-Stop heartbeat timer\n"
            " 6-Connect to wifi\n"
            " 7-Disconnect wifi\n"
            "Select: "
        )

        # Create the wifi service interface
        wifi_service = WifiService()
        wifi_service.start()

        # Instantiate and start RMS service
        rms = RMService()
        rms.start()

        # User command loop
        while True:
            test_choice = input_prompt(input_selection, int, -1)
            match test_choice:
                case 0:
                    rms.stop()
                    break
                case 1:
                    print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                    rms.send_message(HEARTBEAT)
                case 2:
                    print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                    rms.send_message(SYS_INFO)
                case 3:
                    print("\nSend test INCIDENT message to Remote Monitoring Service...\n")
                    rms.send_message(INCIDENT, IncidentMessage("rms_service.py:0", "Test incident from interactive menu"))
                case 4:
                    print("\nStarting heartbeat timer...\n")
                    Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, rms.send_message, args=(HEARTBEAT,))
                case 5:
                    print("\nStop heartbeat timer...\n")
                    Heartbeat.stop_heartbeat()
                case 6:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 7:
                    print("\nDisconnecting wifi...\n")
                    wifi_service.wifi_disconnect()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
