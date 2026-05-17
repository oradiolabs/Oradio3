#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on May 17, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Placeholder for testing error service
"""
from time import sleep
from threading import Thread

##### Oradio modules ####################
from oj_utils import get_command_message
from oj_error_service import ErrorService
from oj_module import worker, start_thread, start_process

def _cmd_loop():
    """
    Check if a new message is put into the queue
    """
    while True:
        # Wait for command message
        command = get_command_message()

        # Error getting message
        if command is None:
            # Mitigate messaging error
            sleep(1)
            continue

        print(f"[MAIN] command: source={command.source}, message={command.message}")
        # Do something based on the source and command arguments

def main():
    """
    Placeholder for testing error service
    """
    print("[MAIN] Start testing error service...")

    # Start the command queue handler service
    Thread(target=_cmd_loop, daemon=True).start()

    # Start the error queue handler service
    _ = ErrorService()

    # Start worker (direct call example)
    print("[MAIN] Testing error service in main context...")
    worker()

    # Wait for worker to fail
    sleep(0.5)

    # Start thread worker example
    print("[MAIN] Testing error service in thread context...")
    start_thread()

    # Wait for thread to fail
    sleep(0.5)

    # Start process worker example
    print("[MAIN] Testing error service in process context...")
    start_process()

    # Wait for thread to fail
    sleep(0.5)

    print("[MAIN] Use ctrl-c to quit")

    # Wait for keyboard interrupt
    while True:
        sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[MAIN] Shutting down...")
