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
    This replaces the log-service-embedded SafeRemotePostHandler, which
    posted such alerts directly from the logging pipeline. Like
    HEARTBEAT/SYS_INFO, this requires start() to have been called; RMS is
    expected to start early enough in the boot sequence that this is not
    a practical limitation.

    Helper functions collect Raspberry Pi telemetry and software version
    information. Outgoing POST requests are protected by a simple
    exponential backoff retry mechanism.
"""
import re
import json
import subprocess
from time import sleep
from threading import Timer
from datetime import datetime
from platform import python_version
from contextlib import ExitStack
from multiprocessing import Queue, Lock
from requests import post, RequestException, Timeout

##### Oradio modules ######################################
from singleton import singleton
from utilities import get_serial
from wifi_service import WifiService
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
)

##### LOCAL constants #####################################
# RMS message type identifiers
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
INCIDENT  = 'INCIDENT'

# Path to the JSON file written by the deployment pipeline with version info
SW_LOG_FILE = "/var/log/oradio_sw_version.log"

# How often the heartbeat is sent (seconds); currently once per hour
HEARTBEAT_REPEAT = 60 * 60

# Remote Monitoring Service endpoint and HTTP POST tuning parameters
RMS_SERVER_URL = "https://oradiolabs.nl/rms/api/index.php/v1/oradiorms/records"
RMS_SERVER_KEY = "e8590bb5e3d88fa306c214bbb066e3638000f6c45aeb04fc8e043af57233e0d9"
MAX_RETRIES    = 3    # Maximum number of POST attempts before giving up
BACKOFF_FACTOR = 2    # Base for exponential backoff: delay = BACKOFF_FACTOR ** attempt (1s, 2s, 4s)
POST_TIMEOUT   = 5    # Per-attempt HTTP timeout in seconds

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
        with open(SW_LOG_FILE, encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

def _handle_response_command(response_text) -> None:
    """
    Extract and execute a command returned by the RMS server.

    Warning:
        Executing commands received from a remote system is inherently
        risky and should eventually be replaced by validated commands
        handled elsewhere.

    Args:
        response_text (str): Raw text body returned by the RMS server.
    """
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
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

def _post_with_retry(payload_info: dict, attach_log_files: bool = False, context: str = "message"):
    """
    POST payload_info to the RMS server with exponential backoff retries.

    Shared across the message types handled by WifiMessageHandler.
    send_message(): all POST to the same RMS_SERVER_URL under the same
    MAX_RETRIES/BACKOFF_FACTOR/POST_TIMEOUT policy, and all publish
    RMS_POST_FAILED once retries are exhausted. They differ only in
    whether log files are attached and in what happens with a
    successful response (SYS_INFO/HEARTBEAT act on a returned command;
    an incident alert does not) -- both of those stay with the caller.

    Args:
        payload_info:     Form fields to POST.
        attach_log_files: If True, attach every *.log file in
                           ORADIO_LOG_PATH on each attempt. Files are
                           (re)opened fresh per attempt inside the loop,
                           since a file object already consumed by a
                           failed attempt can't be resent as-is.
        context:          Short label used in log messages, e.g.
                           "message" or "incident".

    Returns:
        The successful requests.Response, or None if every retry failed
        (RMS_POST_FAILED has already been published in that case).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with ExitStack() as stack:
                payload_files = None
                if attach_log_files:
                    send_files = ORADIO_LOG_PATH.glob("*.log")
                    payload_files = {f.name: (f.name, stack.enter_context(f.open("rb"))) for f in send_files}
                response = post(
                    url=RMS_SERVER_URL,
                    headers={"X-Api-Key": RMS_SERVER_KEY},
                    data=payload_info,
                    files=payload_files,
                    timeout=POST_TIMEOUT
                )
                response.raise_for_status()
            return response  # POST succeeded; exit the retry loop
        except (RequestException, Timeout) as ex_err:
            oradio_log.warning("Attempt %d failed to POST %s: %s", attempt, context, ex_err)
            if attempt == MAX_RETRIES:
                oradio_log.error("Failed to POST %s: %s", context, ex_err)
                Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_POST_FAILED))
                return None
            # Wait before retrying; delay grows exponentially with each attempt
            sleep(BACKOFF_FACTOR ** attempt)
    return None  # Unreachable (loop always returns or raises), keeps type checkers happy

class Heartbeat(Timer):
    """
    Timer that repeatedly invokes a callback.

    The callback is executed immediately when the timer starts and then
    repeated every interval seconds until cancelled.

    Inherits from threading.Timer and overrides run so that
    the callback executes immediately on start, then repeats every
    interval seconds until cancel is called.

    Note:
        @singleton is intentionally NOT applied here. The singleton
        decorator enforces a single shared instance for the lifetime of the
        process, but Timer is a consumable thread — it cannot be restarted
        once it has finished or been cancelled. start_heartbeat must be
        able to create a fresh instance on every call. The "one active timer
        at a time" guarantee is provided instead by cls.instance and
        cls.start_lock, which cancel any running timer before creating
        a new one.

    Use the class-level helpers start_heartbeat and stop_heartbeat
    instead of instantiating directly.
    """
    # Tracks the active timer so start/stop helpers can cancel it.
    # This is Heartbeat's own concern, separate from the singleton machinery.
    instance = None

    # Prevents concurrent start/stop calls from racing on cls.instance.
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
        current log files for context and skipping the response-command
        handling HEARTBEAT/SYS_INFO get (an incident alert doesn't act on
        anything the server returns).

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
            payload_info['source']  = incident.source
            payload_info['message'] = incident.message
            # Result intentionally unused: unlike HEARTBEAT/SYS_INFO, an
            # incident alert doesn't act on any command in the response.
            _post_with_retry(payload_info, attach_log_files=True, context="incident")
            return

        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return  # Nothing to POST; exit early

        response = _post_with_retry(payload_info, context="message")
        if response is None:
            # All retries failed; _post_with_retry() already published the incident
            return

        # A non-2xx status after a successful raise_for_status() shouldn't
        # occur, but log it defensively in case the server returns an
        # unexpected code without raising an HTTPError.
        if not response.ok:
            oradio_log.error(
                "Unexpected status code=%s, response.headers=%s",
                response.status_code, response.headers
            )

        # Act on any command the RMS server included in its response body
        _handle_response_command(response.text)

@singleton
class RMService:
    """
    Manage communication with the Remote Monitoring Service (RMS).

    Subscribes to WiFi connectivity events and delegates all message
    handling -- HEARTBEAT, SYS_INFO, and INCIDENT alike -- to an internal
    WifiMessageHandler. All three require start() to have been called;
    RMS is expected to start early enough in the application's boot
    sequence that no incident could plausibly be raised before it, so
    this is a deliberate simplification rather than an oversight (see
    WifiMessageHandler.send_message() for the per-type detail).

    Construction only sets up internal state; the WiFi subscription and the
    handler's worker thread are not started until start() is called
    explicitly. This lets callers control exactly when the service begins
    subscribing/threading (and stop()/start() again later) rather than
    having it begin as a side effect of instantiation.
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

        Thin delegator to the internal WiFi-driven handler, which now
        handles HEARTBEAT, SYS_INFO, and INCIDENT uniformly -- see
        WifiMessageHandler.send_message() for what each type does and
        which additionally require WiFi to be currently connected.
        Provided so callers and the interactive test menu can trigger
        sends directly on the RMService instance without accessing
        internal state.

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
