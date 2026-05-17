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
from oradio_logging import oradio_log
from oj_utils import get_error_message, put_command_message, CommandMessage

class ErrorService:
    def __init__(self):
        """
        Start the error handler thread
        """
        Thread(target=self._run, daemon=True).start()

    def _run(self):
        """
        Check if a new message is put into the queue
        Recover from error if possible
        Continue normal operation
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
            print(f"[ERROR]: Unknown error!")
