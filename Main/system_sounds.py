#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 30, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Oradio System Sound Player
"""
import subprocess
from pathlib import Path

##### Oradio modules ######################################
from log_service import oradio_log

##### GLOBAL constants ####################################
from constants import (
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

##### LOCAL constants #####################################

# ALSA device for playing system sounds
SYSTEM_SOUND_SINK = "SysSound_in"

# Directory containing system sound files
SOUND_FILES_PATH = (Path(__file__).parent.parent / "system_sounds").resolve()
SOUND_FILES = {
    # Sounds
    SOUND_START: f"{SOUND_FILES_PATH}/StartUp.wav",
    SOUND_STOP:  f"{SOUND_FILES_PATH}/UIT.wav",
    SOUND_PLAY:  f"{SOUND_FILES_PATH}/AAN.wav",
    SOUND_CLICK: f"{SOUND_FILES_PATH}/click.wav",
    # Announcements
    SOUND_NEXT:         f"{SOUND_FILES_PATH}/Next_melding.wav",
    SOUND_PRESET1:      f"{SOUND_FILES_PATH}/Preset1_melding.wav",
    SOUND_PRESET2:      f"{SOUND_FILES_PATH}/Preset2_melding.wav",
    SOUND_PRESET3:      f"{SOUND_FILES_PATH}/Preset3_melding.wav",
    SOUND_SPOTIFY:      f"{SOUND_FILES_PATH}/Spotify_melding.wav",
    SOUND_USB:          f"{SOUND_FILES_PATH}/USBPresent_melding.wav",
    SOUND_NO_USB:       f"{SOUND_FILES_PATH}/NoUSB_melding.wav",
    SOUND_AP_START:     f"{SOUND_FILES_PATH}/OradioAPstarted_melding.wav",
    SOUND_AP_STOP:      f"{SOUND_FILES_PATH}/OradioAPstopped_melding.wav",
    SOUND_WIFI:         f"{SOUND_FILES_PATH}/WifiConnected_melding.wav",
    SOUND_NO_WIFI:      f"{SOUND_FILES_PATH}/WifiNotConnected_melding.wav",
    SOUND_NO_INTERNET:  f"{SOUND_FILES_PATH}/NoInternet_melding.wav",
    SOUND_NEW_PRESET:   f"{SOUND_FILES_PATH}/NewPlaylistPreset_melding.wav",
    SOUND_NEW_WEBRADIO: f"{SOUND_FILES_PATH}/NewPlaylistWebradio_melding.wav",
}

# Critical error at import time if the sounds directory is missing, so the problem
# is visible immediately rather than surfacing per-file at play time.
if not SOUND_FILES_PATH.is_dir():
    oradio_log.critical("System sounds directory not found: %s", SOUND_FILES_PATH)

def play_sound(sound_key: str) -> None:
    """
    Launch a fire-and-forget subprocess that plays the given system sound.

    The function returns as soon as the playback process is launched;
    it does not wait for playback to complete and provides no return value
    to indicate success or failure. If the sound key is unknown or its file
    is missing, the error is logged and the function returns silently.

    Args:
        sound_key (str): One of the SOUND_* constants imported from constants
                         (e.g. SOUND_START, SOUND_CLICK). Must be a key in
                         SOUND_FILES; an unknown key is logged as an error.
    """
    # Resolve the sound key to a file path
    sound_file = SOUND_FILES.get(sound_key)
    if not sound_file:
        oradio_log.error("Invalid sound key: %s", sound_key)
        return

    # Verify the file exists before attempting playback
    if not Path(sound_file).is_file():
        oradio_log.debug("Sound file does not exist or is not a file: %s", sound_file)
        return

    # Launch aplay as a detached process. Passing a list with shell=False avoids
    # shell-injection risks from special characters in the file path.
    # start_new_session=True detaches the child from the parent process group,
    # preventing zombie processes and ensuring playback survives a parent exit.
    subprocess.Popen(               # pylint: disable=consider-using-with
        ["aplay", "-D", SYSTEM_SOUND_SINK, sound_file],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,     # detach from parent; prevents zombies and survives parent exit
        close_fds=True
    )

    oradio_log.debug("System sound process launched: %s", sound_file)

##### Stand-alone entry point #############################

if __name__ == '__main__':

    # Imports only relevant when stand-alone
    import time
    import random
    import threading
    from utilities import input_prompt
    from constants import RED, YELLOW, NC           # pylint: disable=ungrouped-imports

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    sound_keys = list(SOUND_FILES.keys())

    def build_menu():
        """
        Build and return the interactive test menu as a string.
        """
        menu = "\nSelect a function:\n  0-Quit\n"
        for idx, sound_key in enumerate(sound_keys, start=1):
            menu += f"{idx:>3}-Play {sound_key}\n"
        menu += " 99-Stress Test (random sounds)\n"
        menu += "100-Custom Sequence Test (enter 5 sound numbers)\n"
        menu += "Select: "
        return menu

    def interactive_menu():
        """
        Run an interactive self-test menu for system sound playback.
        Blocks until the user enters 0 to quit.
        """
        while True:
            choice = input_prompt(build_menu(), int, -1)

            if choice == 0:
                break

            if 1 <= choice <= len(sound_keys):
                key = sound_keys[choice - 1]
                print(f"\nPlay {key}\n")
                play_sound(key)

            elif choice == 99:
                print("\nStress Test\n")
                try:
                    duration = int(input("Enter duration in seconds (max 60): "))
                except ValueError:
                    print(f"{RED}Invalid input: enter a whole number of seconds.{NC}")
                    continue
                duration = max(1, min(duration, 60))
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
                indices = [int(n) for n in nums if n.isdigit()]
                if len(indices) != 5 or not all(1 <= i <= len(sound_keys) for i in indices):
                    print(f"{RED}Invalid input: need exactly 5 integers between 1 and {len(sound_keys)}.{NC}")
                    continue
                seq = [sound_keys[i - 1] for i in indices]
                print(f"Enqueuing sequence: {seq}\n")
                for k in seq:
                    play_sound(k)

            else:
                print(f"{YELLOW}\nInvalid selection. Please enter a valid number.{NC}")
        # pylint: enable=duplicate-code

    print("\nStarting test program...\n")

    # Launch the interactive test menu; blocks until the user quits
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
