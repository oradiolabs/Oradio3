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
from time import sleep
# Lock added as a safeguard; unnecessary if MPDControl is used correctly per thread/process
from threading import Lock
# Use MPDConnectionError because mpd2 raises a different ConnectionError than Python's built-in one
from mpd import MPDClient, CommandError, ProtocolError, ConnectionError as MPDConnectionError

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################

##### Local constants ####################
MPD_HOST     = "localhost"
MPD_PORT     = 6600
MPD_RETRIES  = 3
MPD_BACKOFF  = 1    # seconds
LOCK_TIMEOUT = 5    # seconds

class MPDService:
    """
    Thread-safe class for interacting with an MPD (Music Player Daemon) server.
    - Automatic connection and reconnection to the MPD server.
    - Retry logic with backoff for commands and connections.
    - Safe execution of MPD commands with optional auto-reconnect.
    - Locking to prevent concurrent access from multiple threads.
    - Logging of commands, connection attempts, and errors.
    """
    def __init__(self, crossfade: int | None = None) -> None:
        """
        Initialize the MPDService class and connect to the MPD server.

        Args:
            crossfade (int | None): Optional crossfade value in seconds.
                                    If None, crossfade will not be set.
        """
        self._lock = Lock()
        self._crossfade = crossfade
        self._client = MPDClient()
        self._connect_client()

# -----Helper methods----------------

    def _is_connected(self):
        """Return True if client is connected to MPD, False otherwise."""
        try:
            self._client.ping() # pylint: disable=no-member
            return True
        except (MPDConnectionError, BrokenPipeError, OSError):
            return False

    def _connect_client(self) -> None:
        """
        Establish a connection to MPD with retries and backoff.
        Optionally sets the crossfade value after a successful connection.
        """
        for attempt in range(1, MPD_RETRIES + 1):
            try:
                # Disconnect stale connection if ping fails
                if not self._is_connected():
                    try:
                        self._client.disconnect()
                        oradio_log.debug("Disconnected stale MPD connection before reconnect")
                    except MPDConnectionError:
                        # Already disconnected, safe to ignore
                        pass

                # Connect if not connected
                if not self._is_connected():
                    self._client.connect(MPD_HOST, MPD_PORT)
                    oradio_log.info("Connected to MPD on %s:%s", MPD_HOST, MPD_PORT)
                else:
                    oradio_log.debug("MPD client already connected, skipping connect")

                # Set crossfade if specified
                if self._crossfade is not None:
                    _ = self._execute("crossfade", self._crossfade, allow_reconnect=False)
                    oradio_log.info("MPD crossfade set to %d", self._crossfade)

                return  # Connection successful

            except (MPDConnectionError, BrokenPipeError, OSError) as ex_err:
                # Wait and retry
                oradio_log.warning("Connection attempt %d/%d failed (%s). Retrying...", attempt, MPD_RETRIES, ex_err)

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                # Catch-all for unexpected errors
                oradio_log.exception("Unexpected error during MPD connection attempt %d/%d: %s", attempt, MPD_RETRIES, ex_unexpected)

            # Avoid hammering the MPD server
            sleep(MPD_BACKOFF)

        # All retries exhausted
        oradio_log.error("Failed to connect to MPD after %d attempts", MPD_RETRIES)

    def _execute(self, command: str, *args, allow_reconnect: bool = True, **kwargs) -> object | None:
        """
        Execute an MPD command safely and efficiently.
        - Retries only on actual lost connections.
        - ProtocolErrors are handled intelligently.
        - Supports lock timeout to prevent deadlocks.

        Args:
            command (str): MPD command to execute.
            *args: Positional arguments.
            allow_reconnect (bool): Prevent recursive reconnects.
            **kwargs: Keyword arguments.

        Returns:
            Result of the command, or None if all retries fail.
        """
        function = getattr(self._client, command, None)
        if not callable(function):
            oradio_log.error("Invalid MPD command: '%s'", command)
            return None

        for attempt in range(1, MPD_RETRIES + 1):
            acquired = False
            try:
                # Acquire lock with timeout (therefore not using 'with')
                acquired = self._lock.acquire(timeout=LOCK_TIMEOUT) # pylint: disable=consider-using-with
                if not acquired:
                    oradio_log.warning("Timeout waiting for MPD lock (attempt %d/%d, command '%s')", attempt, MPD_RETRIES, command)
                    # Avoid hammering the MPD server
                    sleep(MPD_BACKOFF)
                    continue

                oradio_log.debug("Executing MPD command '%s' (attempt %d/%d)", command, attempt, MPD_RETRIES)

                # Execute the MPD command
                return function(*args, **kwargs)

            except CommandError as ex_cmd:
                # Handle logical errors that can safely be ignored
                ignored_errors = [
                    "Not playing",
                ]
                msg = str(ex_cmd)
                if any(err in msg for err in ignored_errors):
                    oradio_log.warning("Ignoring logical error: '%s' for command '%s'", msg, command)
                else:
                    # Invalid command
                    oradio_log.error("MPD command '%s' failed: %s", command, ex_cmd)
                return None

            except (MPDConnectionError, ProtocolError, BrokenPipeError, ConnectionResetError) as ex_err:
                oradio_log.warning("MPD connection error '%s' for command '%s'. Retry %d/%d...", ex_err, command, attempt, MPD_RETRIES)
                if allow_reconnect:
                    self._connect_client()
                # Avoid hammering the MPD server
                sleep(MPD_BACKOFF)

            except Exception as ex_unexpected:  # pylint: disable=broad-exception-caught
                # Unexpected runtime error
                oradio_log.exception("Unexpected error executing MPD command '%s': %s", command, ex_unexpected)
                return None

            finally:
                # Release lock if acquired
                if acquired:
                    self._lock.release()

        # All retries exhausted
        oradio_log.error("Failed to execute MPD command '%s' after %d retries", command, MPD_RETRIES)
        return None

# -----Public methods----------------

    def get_stats(self) -> dict:
        """
        Retrieve and combine playback statistics and current status information.

        Returns:
            dict: A merged dictionary containing both statistics and status data.
        """
        stats = self._execute("stats") or {}
        status = self._execute("status") or {}
        # Return combined dicts
        return stats | status

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Initialise MPD service
    mpd_service = MPDService()

    # Print MPD info
    info = mpd_service.get_stats()
    print("\nRunning MPDService standalone - MPD info:")
    for key, value in info.items():
        print(f"{key:>20} : {value}")
