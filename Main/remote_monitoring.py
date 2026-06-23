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
    Provides the RMService class for communication with the Remote Monitoring
    Service (RMS). On every WiFi-connected event the service starts a
    repeating heartbeat timer that POSTs a lightweight status message to the
    RMS server once per hour, and immediately sends a one-off SYS_INFO message
    containing hardware/software identification. The heartbeat is stopped
    whenever WiFi is disconnected.

    Helper functions collect Raspberry Pi telemetry (serial number,
    temperature, hardware model, OS version) and the project software version.
    A thin exponential-backoff retry loop guards against transient network
    failures when POSTing to the RMS server.
"""
import os
import re
import json
import subprocess
from time import sleep
from datetime import datetime
from platform import python_version
from threading import Thread, Timer, Lock
from requests import post, RequestException, Timeout

##### oradio modules ################
from singleton import singleton
from oradio_utils import get_serial
from wifi_service import WifiService
from oradio_logging import oradio_log
from messaging import (
    Commands,
    safe_get,
    WIFI_SOURCE,
    WIFI_DISCONNECTED,
    WIFI_CONNECTED,
)

##### GLOBAL constants ##############
from oradio_const import (
    YELLOW, NC,
    STOP_SENTINEL,
    JOIN_TIMEOUT,
)

##### LOCAL constants ###############
# RMS message type identifiers
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'

# Path to the JSON file written by the deployment pipeline with version info
SW_LOG_FILE = "/var/log/oradio_sw_version.log"

# How often the heartbeat is sent (seconds); currently once per hour
HEARTBEAT_REPEAT = 60 * 60

# How often the heartbeat is sent (seconds); currently once per hour
STOP_LISTENER = "Stop the wifi message listener"

# How long (seconds) to wait for the listener thread to acknowledge a stop request
LISTENER_TIMEOUT = 3

# Remote Monitoring Service endpoint and HTTP POST tuning parameters
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
MAX_RETRIES    = 3    # Maximum number of POST attempts before giving up
BACKOFF_FACTOR = 2    # Base for exponential backoff: delay = BACKOFF_FACTOR ** (attempt - 1)
POST_TIMEOUT   = 5    # Per-attempt HTTP timeout in seconds

##### Helpers #######################

def _get_rpi_serial() -> str:
    """
    Return the Raspberry Pi's unique OTP serial number.

    Reads the serial from the OTP (One-Time Programmable) register via
    vcgencmd otp_dump. The result is used as a stable device identifier
    in RMS messages.

    Returns:
        str: 8-character hex serial number, or "Unsupported platform"
            if the command is unavailable (e.g. non-RPi hardware).
    """
    serial = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-').read().strip()
    return serial or "Unsupported platform"

def _get_temperature() -> str:
    """
    Return the Raspberry Pi SoC temperature in degrees Celsius.

    Queries the on-chip thermal sensor via vcgencmd measure_temp and
    strips the surrounding label/units, returning only the numeric value
    (e.g. "52.1").

    Returns:
        str: Temperature as a numeric string (°C), or "Unsupported platform"
            if the command is unavailable.
    """
    temperature = os.popen('vcgencmd measure_temp | cut -c 6-9').read().strip()
    return temperature or "Unsupported platform"

def _get_rpi_version() -> str:
    """
    Return the Raspberry Pi hardware model string.

    Reads the Model field from /proc/cpuinfo, which describes the
    board revision (e.g. "Raspberry Pi 4 Model B Rev 1.4").

    Returns:
        str: Human-readable hardware model, or "Unsupported platform"
            if the field is absent.
    """
    version = os.popen("cat /proc/cpuinfo | grep Model | cut -d':' -f2").read().strip()
    return version or "Unsupported platform"

def _get_os_version() -> str:
    """
    Return the operating system distribution description.

    Calls lsb_release -a and extracts the Description line, which
    contains the full OS name and version (e.g.
    "Raspberry Pi OS Lite (bookworm)")

    Returns:
        str: OS description string, or "Unsupported platform" if
            lsb_release is not available.
    """
    version = os.popen("lsb_release -a | grep 'Description:' | cut -d':' -f2").read().strip()
    return version or "Unsupported platform"

def _get_sw_version() -> str:
    """
    Return the Oradio software version from the deployment log file.

    Reads SW_LOG_FILE (a JSON file written by the deployment
    pipeline) and returns a combined string of the serial and
    gitinfo fields, e.g. "v2.3.1 (abc1234)".

    Returns:
        str: Version string in the form "<serial> (<gitinfo>)", or
            "Invalid SW version" if the file is missing, cannot be
            parsed as JSON, or lacks the expected keys.
    """
    try:
        with open(SW_LOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

# REVIEW Onno: Command injection risk — arbitrary shell commands received from the RMS
#              server are executed directly. Replace with a whitelist or delegate to
#              oradio_control so that commands are validated before execution.
def _handle_response_command(response_text) -> None:
    """
    Extract and execute a shell command embedded in the RMS server response.

    The RMS server may include a 'command' directive in its response body
    (PHP array syntax: 'command' => <cmd>). When found, the command is
    passed to /usr/bin/bash for execution and the output is logged.

    .. warning::
        Executing arbitrary commands from a remote server is a security risk.
        This function must only remain in place until command validation is
        moved to oradio_control.

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
                text=True
            )
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error(
                "shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s",
                command, ex_err.returncode, ex_err.stdout, ex_err.stderr
            )

@singleton
class Heartbeat(Timer):
    """
    Repeating daemon timer that fires a callback at a fixed interval.

    Inherits from threading.Timer and overrides run so that
    the callback executes immediately on start, then repeats every
    interval seconds until cancel is called.

    Decorated with singleton so that only one Heartbeat instance
    exists at any time. Use the class-level helpers start_heartbeat
    and stop_heartbeat instead of instantiating directly.
    """
    # Prevents concurrent start/stop calls from racing on cls.instance
    start_lock = Lock()

    def __init__(self, interval, function, args=None, kwargs=None) -> None:
        """
        Initialise the heartbeat timer.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (list, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        super().__init__(interval, function, args=args, kwargs=kwargs)

    def run(self) -> None:
        """
        Run the callback immediately, then repeat every *interval* seconds.

        Overrides threading.Timer.run. The loop continues until
        cancel sets self.finished, at which point  threading.Event.wait
        returns True and the loop exits.

        Exceptions raised by the callback are caught and logged so that a
        single failing tick does not terminate the timer thread.
        """
        while not self.finished.is_set():
            try:
                self.function(*self.args, **self.kwargs)
            # Catch all exceptions: we must not let an unpredictable callback
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

        Thread-safe: uses start_lock to serialise concurrent calls.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (list, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        with cls.start_lock:
            # Cancel and discard the previous instance before creating a new one
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None

            # Create and start a new timer
            cls.instance = cls(interval, function, args=args, kwargs=kwargs)
            # Daemon thread: exits automatically when the main program exits
            cls.instance.daemon = True
            # start the timer
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
            # Stop existing timer if running
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None
                oradio_log.info("Heartbeat stopped")
            else:
                oradio_log.debug("No heartbeat to stop")

class RMService:
    """
    Manage communication with the Remote Monitoring Service (RMS).

    Responsibilities:
    - Listen for WiFi connect/disconnect events via the messaging layer.
    - Start a repeating Heartbeat and send an initial SYS_INFO message
    when WiFi becomes available.
    - Stop the heartbeat when WiFi is lost.
    - Send HEARTBEAT and SYS_INFO POST requests to the RMS
      server, with exponential-backoff retries on failure.
    """
    def __init__(self) -> None:
        """
        Initialise the service and register for WiFi state change events.

        Caches the device serial number and subscribes _wifi_listener to
        the messaging layer. The subscription starts an internal daemon
        thread, so no additional setup is required by the caller.
        """
        # Cache serial number once; used in every outgoing RMS message
        self._serial = get_serial()

        # Subscribe to wifi messages
        self._queue = Commands.subscribe(sources=(WIFI_SOURCE,))

        # Start queue listener thread
        self._thread = Thread(target=self._wifi_listener, daemon=True,)
        self._thread.start()

    def _wifi_listener(self) -> None:
        """
        React to WiFi state-change messages and manage the heartbeat timer.

        Called by the messaging layer whenever a WiFi event is published.
        Starts the heartbeat (and sends SYS_INFO) on connect; stops it on
        disconnect.
        """
        while True:
            message = safe_get(self._queue)
            oradio_log.debug("message: %s", message)

            # STOP_SENTINEL means exit cleanly.
            if message == STOP_SENTINEL:
                return

            if message == WIFI_DISCONNECTED:
                # Use class method to stop the heartbeat timer
                Heartbeat.stop_heartbeat()

            if message == WIFI_CONNECTED:
                # Use class method to start the heartbeat timer
                Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, self.send_message, args = (HEARTBEAT,))
                # Immediately report hardware/software identity on every new connection
                self.send_message(SYS_INFO)
                oradio_log.debug("WiFi connected. Heartbeat started and system info sent.")

    def stop(self) -> None:
        """
        Stop the listener thread and heartbeat timer cleanly.

        The queue is first removed from the pub-sub registry so no further
        messages can arrive. A sentinel value is then enqueued to wake the
        listener thread, after which join() waits for it to terminate.
        """
        # Remove from registry first — no new messages after this point.
        Commands.unsubscribe(self._queue)

        # Wake the listener thread and request a clean shutdown.
        self._queue.put_nowait(STOP_SENTINEL)

        # Wait for the thread to exit.
        self._thread.join(timeout=JOIN_TIMEOUT)
        if self._thread.is_alive():
            oradio_log.warning("Listener thread did not stop within timeout")

        # Stop the heartbeat timer
        Heartbeat.stop_heartbeat()

    def send_message(self, msg_type) -> None:
        """
        Build and POST a message to the RMS server.

        Constructs a payload dict containing a timestamp, device serial, and
        message type. Appends type-specific data (temperature for
        HEARTBEAT; hardware/software info for SYS_INFO), then
        sends the payload via HTTP POST with up to MAX_RETRIES
        attempts using exponential backoff.

        Any command directive found in the server response is forwarded to
        _handle_response_command.

        Args:
            msg_type (str): One of HEARTBEAT or SYS_INFO.
        """
        # Base fields present in every message type
        payload_info = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : self._serial,
            'type'     : msg_type,
        }

        # Append lightweight runtime telemetry for periodic sign-of-life messages
        if msg_type == HEARTBEAT:
            payload_info['message'] = json.dumps({
                'temperature': _get_temperature(),
            })

        # Append full hardware/software identification for onboarding messages
        elif msg_type == SYS_INFO:
            payload_info['message'] = json.dumps({
                'sw_version': _get_sw_version(),
                'python'    : python_version(),
                'rpi'       : _get_rpi_version(),
                'rpi-os'    : _get_os_version(),
            })

        # Unexpected message type
        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return  # Nothing to POST; exit early

        # Retry loop with exponential backoff: delays are 1s, 2s, 4s, ...
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # files=None keeps the Content-Type as application/x-www-form-urlencoded
                response = post(RMS_SERVER_URL, data=payload_info, files=None, timeout=POST_TIMEOUT)
                # Check for any errors
                response.raise_for_status()
                break  # POST succeeded; exit the retry loop
            except (RequestException, Timeout) as ex_err:
                oradio_log.warning("Attempt %d failed: %s", attempt, ex_err)
                if attempt == MAX_RETRIES:
                    oradio_log.error("Failed to POST log: %s", ex_err)
                    return
                # Wait before retrying; delay grows exponentially with each attempt
                sleep(BACKOFF_FACTOR ** (attempt - 1))

        # A non-2xx status after a successful raise_for_status() shouldn't
        # occur, but log it defensively in case the server returns an
        # unexpected code without raising an HTTPError.
        if response.status_code != 200:
            oradio_log.error(
                "Unexpected status code=%s, response.headers=%s",
                response.status_code, response.headers
            )

        # Act on any command the RMS server included in its response body
        _handle_response_command(response.text)

##### Stand-alone entry point #######

if __name__ == "__main__":

    # Imports only relevant when running stand-alone
    from messaging import Topic, DebugMessageHandler    # pylint: disable=ungrouped-imports,wrong-import-position

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
            " 1-Test sending heartbeat\n"
            " 2-Test sending sys_info\n"
            " 3-Start heartbeat timer\n"
            " 4-Stop heartbeat timer\n"
            " 5-Connect to wifi\n"
            " 6-Disconnect wifi\n"
            "Select: "
        )

        # Create the wifi service interface
        wifi_service = WifiService()

        # Instantiate RMS service
        rms = RMService()

        # User command loop
        while True:

            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1  # Treat non-integer input as an invalid selection

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    rms.stop()
                    break
                case 1:
                    print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                    rms.send_message(HEARTBEAT)
                case 2:
                    print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                    rms.send_message(SYS_INFO)
                case 3:
                    print("\nStarting heartbeat timer...\n")
                    Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, rms.send_message, args = (HEARTBEAT,))
                case 4:
                    print("\nStop heartbeat timer...\n")
                    Heartbeat.stop_heartbeat()
                case 5:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    print("\nDisconnecting wifi...\n")
                    wifi_service.wifi_disconnect()
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
