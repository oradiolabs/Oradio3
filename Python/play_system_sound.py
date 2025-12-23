#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on Januari 30, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio System Sound Player
"""
import os
import sys
from threading import Thread

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script

##### GLOBAL constants ####################
from oradio_const import (
    YELLOW, NC,
)

##### LOCAL constants ###################
# Directory containing system sound files
SOUND_FILES_DIR = os.path.abspath(os.path.join(sys.path[0], '..', 'system_sounds'))
# Dictionary of sound names mapped to their file paths
SOUND_FILES = {
    # Sounds
    "StartUp": f"{SOUND_FILES_DIR}/StartUp.wav",
    "Stop":    f"{SOUND_FILES_DIR}/UIT.wav",
    "Play":    f"{SOUND_FILES_DIR}/AAN.wav",
    "Click":   f"{SOUND_FILES_DIR}/click.wav",
    # Announcements
    "Preset1":             f"{SOUND_FILES_DIR}/Preset1_melding.wav",
    "Preset2":             f"{SOUND_FILES_DIR}/Preset2_melding.wav",
    "Preset3":             f"{SOUND_FILES_DIR}/Preset3_melding.wav",
    "Next":                f"{SOUND_FILES_DIR}/Next_melding.wav",
    "Spotify":             f"{SOUND_FILES_DIR}/Spotify_melding.wav",
    "NoInternet":          f"{SOUND_FILES_DIR}/NoInternet_melding.wav",
    "NoUSB":               f"{SOUND_FILES_DIR}/NoUSB_melding.wav",
    "OradioAPstarted":     f"{SOUND_FILES_DIR}/OradioAPstarted_melding.wav",
    "OradioAPstopped":     f"{SOUND_FILES_DIR}/OradioAPstopped_melding.wav",
    "WifiConnected":       f"{SOUND_FILES_DIR}/WifiConnected_melding.wav",
    "WifiNotConnected":    f"{SOUND_FILES_DIR}/WifiNotConnected_melding.wav",
    "NewPlaylistPreset":   f"{SOUND_FILES_DIR}/NewPlaylistPreset_melding.wav",
    "NewPlaylistWebradio": f"{SOUND_FILES_DIR}/NewPlaylistWebradio_melding.wav",
    "USBPresent":          f"{SOUND_FILES_DIR}/USBPresent_melding.wav",
}

class PlaySystemSound:
    """Play a system sound asynchronously."""
    def play(self, sound_key: str) -> None:
        """
        Play a system sound asynchronously.
        
        Args:
            sound_key (str): Key of the sound in SOUND_FILES dictionary.
            
        Logs an error if the key is invalid or the file does not exist.
        """
        sound_file = SOUND_FILES.get(sound_key)
        if not sound_file:
            oradio_log.error("Invalid sound key: %s", sound_key)
            return

        if not os.path.exists(sound_file):
            oradio_log.error("Sound file does not exist: %s", sound_file)
            return

        # Command to play sound
        cmd = f"pw-play {sound_file}"

        # Run the command in a thread
        thread = Thread(
            target=run_shell_script,
            args=(cmd,),
            daemon=True  # thread will not block program exit
        )
        thread.start()

        oradio_log.debug("System sound started in background: %s", sound_file)

# ----- Stand-alone test menu -----

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    import time
    import random

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print("\nStarting System Sound Player Standalone Test...\n")

    sound_player = PlaySystemSound()
    sound_keys = list(SOUND_FILES.keys())

    def build_menu():
        """Construct the menu string for user input."""
        menu = "\nSelect a function:\n  0-Quit\n"
        for idx, sound_key in enumerate(sound_keys, start=1):
            menu += f"{idx:>3}-Play {sound_key}\n"
        menu += " 99-Stress Test (random sounds)\n"
        menu += "100-Custom Sequence Test (enter 5 sound numbers)\n"
        menu += "Select: "
        return menu

    def interactive_menu():
        """Interactive menu loop for testing system sounds."""

        # User command loop
        while True:
            try:
                choice = int(input(build_menu()))
            except ValueError:
                choice = -1

            if choice == 0:
                print("\nExiting test program...\n")
                break

            # Play a single sound
            if 1 <= choice <= len(sound_keys):
                key = sound_keys[choice - 1]
                print(f"\nEnqueue: Play {key}\n")
                sound_player.play(key)

            # Stress test: multiple threads playing random sounds for 10 seconds
            elif choice == 99:
                print("\nExecuting: Stress Test\n")
                #Run stress test playing sound files randomly
                start = time.time()
                def rnd():
                    while time.time() - start < 10:
                        sound_player.play(random.choice(sound_keys))
                        time.sleep(random.uniform(0.1, 0.5))
                threads = [Thread(target=rnd) for _ in range(5)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                print("\nStress test completed.\n")

            # Custom sequence test: user enters 5 sound numbers
            elif choice == 100:
                print("\nCustom Sequence Test selected.")
                seq_input = input(f"Enter 5 numbers (1–{len(sound_keys)}) separated by spaces: ")
                nums = seq_input.strip().split()
                if len(nums) != 5 or not all(n.isdigit() for n in nums):
                    print("Invalid input: need exactly 5 integers.\n")
                    continue
                indices = [int(n) for n in nums]
                if not all(1 <= i <= len(sound_keys) for i in indices):
                    print("Numbers out of range.\n")
                    continue
                seq = [sound_keys[i-1] for i in indices]
                print(f"Enqueuing sequence: {seq}\n")
                for k in seq:
                    sound_player.play(k)

            else:
                print(f"\n{YELLOW}Please input a valid number{NC}\n")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
