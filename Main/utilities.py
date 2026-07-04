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
"""
import json
import socket
import subprocess
from pathlib import Path
from typing import TypeVar
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

T = TypeVar("T")

class ThreadTemplate(Thread):
    """
    Template for a background thread with start/stop/crash detection.

    Subclass and override:
        setup(): One-time init, runs at the start of the thread.
        do_work(): The repeated unit of work.
        teardown(): One-time cleanup, runs when the thread is stopping.
    """

    def __init__(self, *, interval: float = 1.0, name: str | None = None) -> None:
        """
        Initializes the thread.

        Args:
            interval: Seconds to wait between do_work() calls. Defaults to 1.0.
            name: Thread name. Defaults to the subclass's class name if not given.
        """
        super().__init__(name=name or self.__class__.__name__, daemon=True)
        self._interval = interval

        self._stop_event = Event()      # set by stop(), checked by run()
        self._started_event = Event()   # set once setup() finishes (or crashes)

        # _exception is written from run() (the worker thread) and read from
        # the crashed/exception properties, typically from the main thread.
        # Guarded by _exception_lock rather than relying on the GIL, so this
        # stays correct on interpreters without one.
        self._exception_lock = Lock()
        self._exception: Exception | None = None    # holds exception raised in run(), if any

    # --- lifecycle -----------------------------------------------------

    def safe_start(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """
        Starts the thread and waits until setup() has completed.

        Args:
            timeout: Max seconds to wait for setup() to finish. Defaults to 5.0.

        Returns:
            True if the thread reported ready within timeout,
            False if it failed to start or setup() did not complete in time.
            Note this does NOT mean setup() succeeded -- check
            the crashed property afterward to distinguish "timed out"
            from "started and immediately crashed".
        """
        try:
            # Spawns the OS thread and schedules run(). Can raise
            # RuntimeError if called twice or if the OS can't allocate
            # a new thread.
            super().start()
        except RuntimeError:
            oradio_log.exception("%s failed to start thread", self.name)
            return False

        # Block here until run() signals that setup() has completed,
        # rather than assuming the thread is ready as soon as it's spawned.
        started_ok = self._started_event.wait(timeout)
        if not started_ok:
            oradio_log.error("%s failed to start within %ss", self.name, timeout)
        return started_ok

    def run(self) -> None:
        """
        Thread entry point. Do not call directly -- use start().
 
        Runs setup() once, then calls do_work() immediately, then
        repeatedly again every interval seconds until stop() is
        called, then runs teardown(). (do_work() fires right after
        setup() rather than waiting out the first interval, so a
        poller doesn't sit idle before its first check.) Any
        exception raised by setup() or do_work() is caught, logged,
        and stored rather than propagated, since exceptions raised
        inside a thread's run() are never seen by the caller of
        start().
        """
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
            oradio_log.exception("%s crashed", self.name)
            # Unblock start() even if setup() itself crashed, so callers
            # waiting on start() don't hang for the full timeout.
            self._started_event.set()

        finally:
            # Always run teardown, even if setup()/do_work() raised,
            # so resources acquired in setup() still get released.
            self.teardown()

    def safe_stop(self, timeout: float = JOIN_TIMEOUT) -> bool:
        """Signals the thread to stop and waits for it to finish.

        Args:
            timeout: Max seconds to wait for the thread to exit.

        Returns:
            True if the thread finished within timeout, False if it's still
            alive afterward (e.g. do_work() is blocked on something that
            ignores _stop_event). Note that a stuck thread cannot be forcibly
            killed -- False just tells you it happened.
        """
        self._stop_event.set()  # tells run()'s loop condition to exit
        self.join(timeout)      # blocks until the thread actually exits

        if self.is_alive():
            oradio_log.error("%s did not stop within %ss", self.name, timeout)
            return False

        if self.crashed:
            oradio_log.error("%s crashed with exception: %s", self.name, self.exception)
            return False
        return True

    @property
    def stopping(self) -> bool:
        """
        True once stop() has been called, even before the
        thread has actually exited. Useful inside a long-running
        do_work() to check whether it should bail out early.
        """
        return self._stop_event.is_set()

    @property
    def crashed(self) -> bool:
        """True if setup() or do_work() raised an exception."""
        return self._exception is not None

    @property
    def exception(self) -> Exception | None:
        """The exception raised inside run(), if any."""
        with self._exception_lock:
            return self._exception

    # --- override these --------------------------------------------------

    def setup(self) -> None:
        """
        Called once before the work loop starts. Override for
        one-time initialization (opening connections, allocating
        resources, etc.). Default implementation does nothing.
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
        Called once after the loop exits, whether it exited
        cleanly or due to an exception. Override for cleanup
        (closing connections, releasing resources, etc.). Default
        implementation does nothing.
        """
        # Pass is intentional, see doc string
        pass    # pylint: disable=unnecessary-pass

def get_serial() -> str:
    """Extract serial from Raspberry Pi."""
    cmd = "vcgencmd otp_dump"
    result, response = run_shell_script(cmd)

    if not result:
        oradio_log.error("Error during <%s> to get serial number, error: %s", cmd, response)
        return "Unknown"

    # Parse the output in Python
    for line in response.splitlines():
        if line.startswith(SERIAL_OTP_ROW):
            serial = line[len(SERIAL_OTP_ROW):].strip()
            return serial or "Unknown"

    return "Unknown"

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
    oradio_log.debug("Running shell script: %s", script)
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
