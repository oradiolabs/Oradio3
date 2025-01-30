#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Test volume control
"""
import time
from volume_control import start_monitoring

class StateMachine:
    def __init__(self):
        self.state = "StateStop"

    def transition(self, new_state):
        print(f"Transitioning from {self.state} to {new_state}")
        self.state = new_state

if __name__ == "__main__":
    try:
        # Initialize state machine
        state_machine = StateMachine()
        # Start the volume monitoring thread
        start_monitoring(state_machine)
        print("Volume monitoring started.")
        
        # Keep the main program running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting program.")
