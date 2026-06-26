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
from multiprocessing import Queue, Lock
from requests import post, RequestException, Timeout

##### oradio modules ################
from utilities import get_serial
from wifi_service import WifiService
from log_service import oradio_log
from messaging import (
    Commands,
    MessageHandlerBase,
    WIFI_SOURCE,
    WIFI_DISCONNECTED,
    WIFI_CONNECTED,
    RMS_SOURCE,
    RMS_ERROR_SERVICE,
)

##### GLOBAL constants ##############
from constants import (
    YELLOW, NC,
)

##### LOCAL constants ###############
# RMS message type identifiers
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'

# Path to the JSON file written by the deployment pipeline with version info
SW_LOG_FILE = "/var/log/oradio_sw_version.log"

# How often the heartbeat is sent (seconds); currently once per hour
HEARTBEAT_REPEAT = 60 * 60

# Remote Monitoring Service endpoint and HTTP POST tuning parameters
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
MAX_RETRIES    = 3    # Maximum number of POST attempts before giving up
BACKOFF_FACTOR = 2    # Base for exponential backoff: delay = BACKOFF_FACTOR ** attempt (1s, 2s, 4s)
POST_TIMEOUT   = 5    # Per-attempt HTTP timeout in seconds

##### Helpers #######################

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
        with open(SW_LOG_FILE, "r", encoding="utf-8") as file:
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

class Heartbeat(Timer):
    """
    Timer that repeatedly invokes a callback.

    The callback is executed immediately when the timer starts and then
    repeated every interval seconds until cancelled.

    Inherits from ``threading.Timer`` and overrides ``run`` so that
    the callback executes immediately on start, then repeats every
    ``interval`` seconds until ``cancel`` is called.

    Note:
        ``@singleton`` is intentionally NOT applied here. The singleton
        decorator enforces a single shared instance for the lifetime of the
        process, but ``Timer`` is a consumable thread — it cannot be restarted
        once it has finished or been cancelled. ``start_heartbeat`` must be
        able to create a fresh instance on every call. The "one active timer
        at a time" guarantee is provided instead by ``cls.instance`` and
        ``cls.start_lock``, which cancel any running timer before creating
        a new one.

    Use the class-level helpers ``start_heartbeat`` and ``stop_heartbeat``
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

        Thread-safe: uses ``start_lock`` to serialise concurrent calls.
        Does nothing if no heartbeat is currently running.
        """
        with cls.start_lock:
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None
                oradio_log.info("Heartbeat stopped")
            else:
                oradio_log.debug("No heartbeat to stop")

class WifiMessageHandler(MessageHandlerBase):
    """
    Handle WiFi state change messages and drive heartbeat and RMS reporting.

    Subscribes to the COMMAND topic filtered to WiFi messages. On a
    WIFI_CONNECTED event the heartbeat timer is started and a one-time
    SYS_INFO message is sent to the RMS server. On a WIFI_DISCONNECTED
    event the heartbeat timer is stopped.
    """
    def __init__(self, queue: Queue) -> None:
        """
        Initialise the WiFi message handler.

        Args:
            queue: Subscription queue filtered to WiFi messages.
        """
        # Cache serial number once; used in every outgoing RMS message
        self._serial = get_serial()

        # Initialise base class and start the worker thread
        super().__init__(queue)

    def _handle_message(self, message) -> None:
        """
        Handle an incoming WiFi state change message.

        Args:
            message: The received message from the queue.
        """
        if message.message == WIFI_DISCONNECTED:
            Heartbeat.stop_heartbeat()
            oradio_log.debug("WiFi disconnected. Heartbeat stopped.")

        elif message.message == WIFI_CONNECTED:
            Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, self.send_message, args=(HEARTBEAT,))
            # Immediately report hardware/software identity on every new connection
            self.send_message(SYS_INFO)
            oradio_log.debug("WiFi connected. Heartbeat started and system info sent.")

        else:
            oradio_log.error("Unexpected message: %s", message)

    def send_message(self, msg_type) -> None:
        """
        Build and send a message to the RMS server.

        Depending on the message type, either runtime telemetry or hardware
        and software information is included. Failed HTTP requests are retried
        with exponential backoff.

        Args:
            msg_type (str): HEARTBEAT or SYS_INFO.
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

        else:
            oradio_log.error("Unsupported message type: %s", msg_type)
            return  # Nothing to POST; exit early

        # Retry loop with exponential backoff: delays are 1s, 2s, 4s, ...
        response = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = post(RMS_SERVER_URL, data=payload_info, timeout=POST_TIMEOUT)
                response.raise_for_status()
                break  # POST succeeded; exit the retry loop
            except (RequestException, Timeout) as ex_err:
                oradio_log.warning("Attempt %d failed: %s", attempt, ex_err)
                if attempt == MAX_RETRIES:
                    oradio_log.error("Failed to POST log: %s", ex_err)
                    return
                # Wait before retrying; delay grows exponentially with each attempt
                sleep(BACKOFF_FACTOR ** attempt)

        if response is None:
            # All retries failed without breaking out of the loop — nothing to process
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

class RMService:
    """
    Manage communication with the Remote Monitoring Service (RMS).

    Subscribes to WiFi connectivity events and delegates all message
    handling — heartbeat scheduling, SYS_INFO reporting, and HTTP
    POST retries — to an internal ``WifiMessageHandler``.
    """
    def __init__(self) -> None:
        """
        Initialise the service and register for WiFi state change events.

        Subscribes to the messaging layer filtered to WiFi events and starts
        an internal daemon thread to process incoming messages. Logs an error
        if the handler thread fails to start.
        """
        # Subscribe to WiFi messages only
        self._queue = Commands.subscribe(sources=(WIFI_SOURCE,))

        # Start queue listener thread
        try:
            self._handler = WifiMessageHandler(self._queue)
            oradio_log.info("RMS service started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("RMS service failed to start: %s", ex_err)
            Errors.publish(ErrorMessage(RMS_SOURCE, RMS_ERROR_SERVICE))

    def send_message(self, msg_type: str) -> None:
        """
        Send a message to the RMS server.

        Delegates to the internal handler. Provided so callers and the
        interactive test menu can trigger sends directly on the RMService
        instance without accessing internal state.

        Args:
            msg_type (str): HEARTBEAT or SYS_INFO.
        """
        self._handler.send_message(msg_type)

    def stop(self) -> None:
        """
        Shut down the RMS service cleanly.

        Stops the heartbeat timer, unsubscribes from the command queue,
        and signals the worker thread to exit.
        """
        Heartbeat.stop_heartbeat()
        Commands.unsubscribe(self._queue)
        self._handler.stop()

##### Stand-alone entry point #######

if __name__ == "__main__":

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
                    Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, rms.send_message, args=(HEARTBEAT,))
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
