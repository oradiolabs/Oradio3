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
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Checks internet avalaiblity

Test with:
Internet acces: 
NOT:  sudo iptables -I OUTPUT -d 8.8.8.8 -j DROP
YES:  sudo iptables -D OUTPUT -d 8.8.8.8 -j DROP

Use case:
from internet_checker import is_internet_available
if is_internet_available():
    print("Internet is up!")
else:
    print("No internet connection!")
"""

import socket
import threading
import time
import oradio_utils  # Import the logging utility

# Constants (you can change these values as needed)
CHECK_INTERVAL = 10       # seconds between regular checks
POLL_INTERVAL = 1         # seconds between confirmation polls
CONFIRMATION_COUNT = 3    # number of confirmations needed to change status

class InternetAvailabilityChecker:
    """
    This class checks for internet connectivity in a background thread.
    It performs a check every CHECK_INTERVAL seconds.
    If a change in connectivity is detected, it performs additional checks (polls)
    at POLL_INTERVAL seconds apart and confirms the change only if it occurs in
    CONFIRMATION_COUNT consecutive checks.
    """
    def __init__(self, check_interval=CHECK_INTERVAL,
                 poll_interval=POLL_INTERVAL,
                 confirmation_count=CONFIRMATION_COUNT):
        self.check_interval = check_interval
        self.poll_interval = poll_interval
        self.confirmation_count = confirmation_count
        self._internet_available = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        """Start the background checking thread."""
        self._thread.start()

    def stop(self):
        """Stop the background checking thread."""
        self._stop_event.set()
        self._thread.join()

    @property
    def internet_available(self):
        """Return the current internet connectivity status (True/False)."""
        with self._lock:
            return self._internet_available

    def _set_internet_available(self, status):
        with self._lock:
            self._internet_available = status

    def _check_internet(self):
        """
        Try to connect to a well-known host (Google DNS at 8.8.8.8:53).
        Returns True if the connection succeeds (i.e. internet is available),
        or False otherwise.
        
        If a timeout occurs (which is expected when there is no internet),
        no error is logged.
        """
        try:
            socket.setdefaulttimeout(1)  # 3-second timeout
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except socket.timeout:
            # Timeout is expected when there is no internet, so don't log it.
            return False
        except Exception as e:
            # Log only unexpected exceptions.
            oradio_utils.logging("error", f"Error checking internet connectivity: {e}")
            return False

    def _run(self):
        # Check the initial status
        current_status = self._check_internet()
        self._set_internet_available(current_status)
        # Log the initial status in a human-readable format.
        if current_status:
            oradio_utils.logging("info", "Internet is available")
        else:
            oradio_utils.logging("info", "Internet NOT available")

        while not self._stop_event.is_set():
            time.sleep(self.check_interval)
            new_status = self._check_internet()
            # If a change is detected, confirm it by polling
            if new_status != current_status:
                # Log the potential change in a human-readable format.
                if new_status:
                    oradio_utils.logging("warning", "Potential internet status change detected: Internet is available")
                else:
                    oradio_utils.logging("warning", "Potential internet status change detected: Internet NOT available")

                confirmed = True
                for _ in range(self.confirmation_count):
                    if self._stop_event.is_set():
                        break
                    time.sleep(self.poll_interval)
                    poll_status = self._check_internet()
                    if poll_status != new_status:
                        confirmed = False
                        break
                if confirmed:
                    # Log the confirmed change in a human-readable format.
                    if new_status:
                        oradio_utils.logging("warning", "Internet connectivity status confirmed changed to: Internet is available")
                    else:
                        oradio_utils.logging("warning", "Internet connectivity status confirmed changed to: Internet NOT available")
                    current_status = new_status
                    self._set_internet_available(new_status)

# Create a global instance and start it as soon as the module is imported.
_checker_instance = InternetAvailabilityChecker()
_checker_instance.start()

def is_internet_available():
    """
    Returns True if the internet is available, False otherwise.
    This value is updated in the background.
    """
    return _checker_instance.internet_available

# A simple test routine; run this module directly to see it in action.
if __name__ == '__main__':
    try:
        while True:
            # The script can simply wait while the background thread logs the status.
            time.sleep(5)
    except KeyboardInterrupt:
        print("Stopping internet checker.")
        _checker_instance.stop()