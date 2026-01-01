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
import subprocess

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    RED, YELLOW, NC,
    SOUND_START,
    SOUND_STOP,
    SOUND_PLAY,
    SOUND_CLICK,
    SOUND_NEXT,
    SOUND_PRESET1,
    SOUND_PRESET2,
    SOUND_PRESET3,
    SOUND_SPOTIFY,
    SOUND_USB,
    SOUND_NO_USB,
    SOUND_AP_START,
    SOUND_AP_STOP,
    SOUND_WIFI,
    SOUND_NO_WIFI,
    SOUND_NO_INTERNET,
    SOUND_NEW_PRESET,
    SOUND_NEW_WEBRADIO,
)

##### LOCAL constants ###################
# ALSA device for playing system sounds
SYSTEM_SOUND_SINK = "SysSound_in"
# Directory containing system sound files
SOUND_FILES_DIR = os.path.abspath(os.path.join(sys.path[0], '..', 'system_sounds'))
SOUND_FILES = {
    # Sounds
    SOUND_START: f"{SOUND_FILES_DIR}/StartUp.wav",
    SOUND_STOP:  f"{SOUND_FILES_DIR}/UIT.wav",
    SOUND_PLAY:  f"{SOUND_FILES_DIR}/AAN.wav",
    SOUND_CLICK: f"{SOUND_FILES_DIR}/click.wav",
    # Announcements
    SOUND_NEXT:         f"{SOUND_FILES_DIR}/Next_melding.wav",
    SOUND_PRESET1:      f"{SOUND_FILES_DIR}/Preset1_melding.wav",
    SOUND_PRESET2:      f"{SOUND_FILES_DIR}/Preset2_melding.wav",
    SOUND_PRESET3:      f"{SOUND_FILES_DIR}/Preset3_melding.wav",
    SOUND_SPOTIFY:      f"{SOUND_FILES_DIR}/Spotify_melding.wav",
    SOUND_USB:          f"{SOUND_FILES_DIR}/USBPresent_melding.wav",
    SOUND_NO_USB:       f"{SOUND_FILES_DIR}/NoUSB_melding.wav",
    SOUND_AP_START:     f"{SOUND_FILES_DIR}/OradioAPstarted_melding.wav",
    SOUND_AP_STOP:      f"{SOUND_FILES_DIR}/OradioAPstopped_melding.wav",
    SOUND_WIFI:         f"{SOUND_FILES_DIR}/WifiConnected_melding.wav",
    SOUND_NO_WIFI:      f"{SOUND_FILES_DIR}/WifiNotConnected_melding.wav",
    SOUND_NO_INTERNET:  f"{SOUND_FILES_DIR}/NoInternet_melding.wav",
    SOUND_NEW_PRESET:   f"{SOUND_FILES_DIR}/NewPlaylistPreset_melding.wav",
    SOUND_NEW_WEBRADIO: f"{SOUND_FILES_DIR}/NewPlaylistWebradio_melding.wav",
}

def play_sound(sound_key: str) -> None:
    """
    Fire-and-forget the system command that plays the given sound file.

    Args:
       sound_key: Name of the sound key listed in SOUND_FILES to be played.
    """
    # Get sound file from sound key
    sound_file = SOUND_FILES.get(sound_key)
    if not sound_file:
        oradio_log.error("Invalid sound key: %s", sound_key)
        return

    # Check if sound file exists
    if not os.path.exists(sound_file):
        oradio_log.debug("Sound file does not exist: %s", sound_file)
        return

    # Command to play sound
    cmd = f"aplay -D '{SYSTEM_SOUND_SINK}' {sound_file}"

    # Use 'with' to ensure proper cleanup after starting process in background
    with subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,     # detaches process to prevent zombies
        close_fds=True
    ):
        pass  # We don't need to wait; context ensures cleanup

    oradio_log.debug("System sound played successfully: %s", sound_file)

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    import time
    import random
    import threading

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print("\nStarting System Sound Player Standalone Test...\n")

    sound_keys = list(SOUND_FILES.keys())

    def build_menu():
        """Create stand-alone menu options """
        menu = "\nSelect a function:\n  0-Quit\n"
        for idx, sound_key in enumerate(sound_keys, start=1):
            menu += f"{idx:>3}-Play {sound_key}\n"
        menu += " 99-Stress Test (random sounds)\n"
        menu += "100-Custom Sequence Test (enter 5 sound numbers)\n"
        menu += "Select: "
        return menu

    def interactive_menu():
        """Show menu with test options"""

        # User command loop
        while True:
            try:
                choice = int(input(build_menu()))
            except ValueError:
                choice = -1

            if choice == 0:
                print("\nExiting test program...\n")
                break

            if 1 <= choice <= len(sound_keys):
                key = sound_keys[choice - 1]
                print(f"\nPlay {key}\n")
                play_sound(key)

            elif choice == 99:
                print("\nStress Test\n")
                duration = int(input("Enter duration in seconds: "))
                start = time.time()
                def rnd():
                    while time.time() - start < duration:
                        play_sound(random.choice(sound_keys))
                        time.sleep(random.uniform(0.1, 0.5))
                threads = [threading.Thread(target=rnd) for _ in range(5)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                print("\nStress test completed.\n")

            elif choice == 100:
                print("\nCustom Sequence Test\n")
                seq_input = input(f"Enter 5 numbers (1–{len(sound_keys)}) separated by spaces: ")
                nums = seq_input.strip().split()
                if len(nums) != 5 or not all(n.isdigit() for n in nums):
                    print(f"{RED}Invalid input: need exactly 5 integers.\n{NC}")
                    continue
                indices = [int(n) for n in nums]
                if not all(1 <= i <= len(sound_keys) for i in indices):
                    print(f"{RED}Numbers out of range.\n{NC}")
                    continue
                seq = [sound_keys[i-1] for i in indices]
                print(f"Enqueuing sequence: {seq}\n")
                for k in seq:
                    play_sound(k)

            else:
                print(f"{YELLOW}\nInvalid selection. Please enter a valid number.{NC}")

    # Present menu with tests
    interactive_menu()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
