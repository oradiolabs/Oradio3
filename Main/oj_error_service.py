#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on May 15, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides a top level error resolution service
"""
from time import sleep
from threading import Thread

##### Oradio modules ####################
from oj_utils import get_error_message, put_command_message, CommandMessage

class ErrorService:
    """
    Background service responsible for handling runtime error messages

    The service continuously monitors the shared error queue for incoming
    error messages and applies predefined mitigation or recovery actions
    based on the error source

    Known errors are handled automatically when possible
    Unknown errors are logged as a fail-safe mechanism
    """
    def __init__(self):
        """
        Initialize and start the error handling service

        A daemon thread is started automatically, allowing the service
        to run continuously in the background without blocking the main
        application thread
        """
        Thread(target=self._run, daemon=True).start()

    def _run(self):
        """
        Main error handling loop

        Continuously waits for error messages from the shared queue
        When an error is received, the service attempts to recover
        from known error conditions by issuing appropriate commands

        Behavior:
            - Retries if no message could be retrieved
            - Handles known worker-related errors by sending a reset command
            - Logs unknown errors for debugging and fail-safe purposes
        """
        while True:
            # Wait for error message
            error = get_error_message()

            # Error getting message
            if error is None:
                # Mitigate messaging error
                sleep(1)
                continue

            print(f"[ERROR] received: {error}")

            # Mitigation logic for known errors
            if error.source == "worker":
                put_command_message(CommandMessage("error service", "reset"))
                continue

            # Fail-safe for unknown error
            print("[ERROR]: Unknown error!")
