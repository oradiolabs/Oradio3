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
    This module runs a heartbeat timer sending heartbeat messages to a remote
    monitoring service when connected to the internet. It also sends system
    information messages to the remote monitoring service.
"""
import os
import re
import json
import subprocess
from time import sleep
from datetime import datetime
from platform import python_version
from threading import Thread, Timer, Lock
from multiprocessing import Queue
from requests import post, RequestException, Timeout

##### oradio modules ####################
from singleton import singleton
from oradio_logging import oradio_log
from oradio_utils import get_serial, safe_put
from wifi_service import WifiService

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
    STATE_WIFI_IDLE,
    STATE_WIFI_CONNECTED,
)

##### LOCAL constants ####################
# Message types
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
# Software version info file
SW_LOG_FILE = "/var/log/oradio_sw_version.log"
# HEARTBEAT repeat time
HEARTBEAT_REPEAT = 60 * 60     # 1 hour in seconds
# Internal message to stop the message listener thread
STOP_LISTENER = "Stop the wifi message listener"
# Timeout for listener to respond (seconds)
LISTENER_TIMEOUT = 3
# Remote Monitoring Service
RMS_SERVER_URL = 'https://oradiolabs.nl/rms/receive.php'
MAX_RETRIES    = 3
BACKOFF_FACTOR = 2  # Exponential backoff multiplier
POST_TIMEOUT   = 5  # seconds

# ----- Helpers -----

def _get_rpi_serial() -> str:
    """Extract serial from Raspberry Pi."""
    serial = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-').read().strip()
    return serial or "Unsupported platform"

def _get_temperature() -> str:
    """Extract SoC temperature from Raspberry Pi."""
    temperature = os.popen('vcgencmd measure_temp | cut -c 6-9').read().strip()
    return temperature or "Unsupported platform"

def _get_rpi_version() -> str:
    """Get the Raspberry Pi version."""
    version = os.popen("cat /proc/cpuinfo | grep Model | cut -d':' -f2").read().strip()
    return version or "Unsupported platform"

def _get_os_version() -> str:
    """Get the operating system version."""
    version = os.popen("lsb_release -a | grep 'Description:' | cut -d':' -f2").read().strip()
    return version or "Unsupported platform"

def _get_sw_version() -> str:
    """Read software version from log file."""
    try:
        with open(SW_LOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data["serial"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SW_LOG_FILE)
        return "Invalid SW version"

#REVIEW Onno: Dit is gevaarlijk, kwetsbaar voor command injection: Stuur command naar oradio_control voor veilige afhandeling
def _handle_response_command(response_text) -> None:
    """
    Check for a 'command =>' entry in the server response and execute it.
    
    WARNING: This executes shell commands received from RMS server.
    """
    match = re.search(r"'command'\s*=>\s*(.*)", response_text)
    if match:
        # Pass command to linux shell for execution
        command = match.group(1).strip()
        oradio_log.debug("Run command '%s' from RMS server", command)
        try:
            # executable need to be set, othewise python uses sh. Text converts the result into readable string
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
            oradio_log.error("shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s", command, ex_err.returncode, ex_err.stdout, ex_err.stderr)

@singleton
class Heartbeat(Timer):
    """Timer singleton to handle heartbeat sending at regular intervals."""
    # Lock for start/stop operations

    start_lock = Lock()

    def __init__(self, interval, function, args=None, kwargs=None) -> None:
        """Initialize Timer."""
        super().__init__(interval, function, args=args, kwargs=kwargs)

    def run(self) -> None:
        """Call function immediately, then repeat at interval."""
        while not self.finished.is_set():
            try:
                self.function(*self.args, **self.kwargs)
            # We don't know what exception the callback can raise, so we need to catch all exceptions as we don't want to stop
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Heartbeat execution failed: %s", ex_err)

            # Wait for interval before next iteration
            if self.finished.wait(self.interval):
                break

    @classmethod
    def start_heartbeat(cls, interval, function, args=None, kwargs=None) -> None:
        """Stop the current timer if running, then start a new one safely."""
        with cls.start_lock:
            # Stop existing timer if running
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None

            # Create and start a new timer
            cls.instance = cls(interval, function, args=args, kwargs=kwargs)
            # makes it exit with the main program
            cls.instance.daemon = True
            # start the timer
            cls.instance.start()
            oradio_log.info("Heartbeat started")

    @classmethod
    def stop_heartbeat(cls) -> None:
        """Stop the running heartbeat safely."""
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
    Manage communication with Oradio Remote Monitoring Service (ORMS):
    - HEARTBEAT messages as sign of life
    - SYS_INFO to identify the Oradio to ORMS
    """
    def __init__(self) -> None:
        """Initialize RMService and start wifi listener."""
        # Cach Raspberry Pi serial number
        self._serial = get_serial()

        # Queue for receiving messages from wifi service
        self._wifi_queue = Queue()

        # Start wifi listener thread
        self._wifi_listener = Thread(target=self._wifi_listener, daemon=True)
        self._wifi_listener.start()

        # Create the wifi service interface
        self.wifi_service = WifiService(self._wifi_queue)

    def _wifi_listener(self) -> None:
        """Thread that processes messages from WifiService."""
        while True:
            # Wait indefinitely until a message arrives from the server/wifi service
            message = self._wifi_queue.get(block=True, timeout=None)
            oradio_log.debug("Message received: '%s'", message)

            # Get the wifi message
            state = message.get("state")

            # Check if the thread needs to stop
            if state == STOP_LISTENER:
                # Use class method to stop the heartbeat timer
                Heartbeat.stop_heartbeat()
                # Stop the message listener
                break

            if state == STATE_WIFI_IDLE:
                # Use class method to stop the heartbeat timer
                Heartbeat.stop_heartbeat()
                # Continue listening for further messages
                continue

            if state == STATE_WIFI_CONNECTED:
                # Use class method to start the heartbeat timer
                Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, self.send_message, args = (HEARTBEAT,))
                # Send system info
                self.send_message(SYS_INFO)
                oradio_log.debug("WiFi connected. Heartbeat started and system info sent.")
                # Continue listening for further messages
                continue

    def close(self) -> None:
        """Close RMService."""
        # Unsubscribe from wifi service
        self.wifi_service.close()

        # Send message for wifi mmessage listener to stop
        safe_put(self._wifi_queue, {"state": STOP_LISTENER})

        # Avoid hanging forever if the thread is stuck in I/O
        self._wifi_listener.join(timeout=LISTENER_TIMEOUT)

        if self._wifi_listener.is_alive():
            oradio_log.error("Join timed out: wifi listener thread is still running")

    def send_message(self, msg_type) -> None:
        """
        send message to Remote Monitoring Service.
        NOTE: Only called when connected to internet.

        Args:
            msg_type (str): HEARTBEAT or SYS_INFO
        """

        # Initialze message to send
        payload_info = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'serial'   : self._serial,
            'type'     : msg_type,
        }

        # Compile HEARTBEAT message
        if msg_type == HEARTBEAT:
            payload_info['message'] = json.dumps({
                'temperature': _get_temperature(),
            })

        # Compile SYS_INFO message
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

        # Retry loop
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Send POST without files
                response = post(RMS_SERVER_URL, data=payload_info, files=None, timeout=POST_TIMEOUT)
                # Check for any errors
                response.raise_for_status()
                # Success, exit retry loop
                break
            except (RequestException, Timeout) as ex_err:
                oradio_log.warning("Attempt %d failed: %s", attempt, ex_err)
                if attempt == MAX_RETRIES:
                    oradio_log.error("Failed to POST log: %s", ex_err)
                    return
                sleep(BACKOFF_FACTOR ** (attempt - 1))

        # Check for errors
        if response.status_code != 200:
            oradio_log.error("Status code=%s, response.headers=%s", response.status_code, response.headers)

        # Check for command in RMS response
        _handle_response_command(response.text)

if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def interactive_menu() -> None:
        """Show menu with test options"""
        # Instantiate RMS service
        rms = RMService()

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

        # User command loop
        while True:

            # Get user input
            try:
                function_nr = int(input(input_selection))
            except ValueError:
                function_nr = -1

            # Execute selected function
            match function_nr:
                case 0:
                    print("\nExiting test program...\n")
                    rms.close()
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
                        rms.wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 6:
                    print("\nDisconnecting wifi...\n")
                    rms.wifi_service.wifi_disconnect()
                case _:
                    print("\nPlease input a valid number\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
