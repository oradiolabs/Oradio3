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

Logging update

"""
import os
import subprocess
import threading
import time
import random

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl

##### GLOBAL constants ####################
from oradio_const import *
#SOUND_FILES_DIR in global constants

##### LOCAL constants ####################

SOUND_FILES = {
    # Sounds
    "StartUp": f"{SOUND_FILES_DIR}/StartUp.wav",
    "Stop":    f"{SOUND_FILES_DIR}/UIT.wav",
    "Play":    f"{SOUND_FILES_DIR}/AAN.wav",
    "Preset1": f"{SOUND_FILES_DIR}/PL1.wav",
    "Preset2": f"{SOUND_FILES_DIR}/PL2.wav",
    "Preset3": f"{SOUND_FILES_DIR}/PL3.wav",
    "Click":   f"{SOUND_FILES_DIR}/click.wav",
    # Announcements
    "Spotify":      f"{SOUND_FILES_DIR}/Spotify_melding.wav",
    "NoInternet":   f"{SOUND_FILES_DIR}/NoInternet_melding.wav",
    "NoUSB":        f"{SOUND_FILES_DIR}/NoUSB_melding.wav",
    "WebInterface": f"{SOUND_FILES_DIR}/WebInterface_melding.wav",
    "OradioAP":     f"{SOUND_FILES_DIR}/OradioAP_melding.wav",
    "WifiConnected": f"{SOUND_FILES_DIR}/Wifi_verbonden_melding.wav",
    "WifiNotConnected":     f"{SOUND_FILES_DIR}/Niet_Wifi_verbonden_melding.wav",
}

class PlaySystemSound:
    """
    Class to play system sounds asynchronously in a separate thread.
    """

    def __init__(self, audio_device="SysSound_in"):
        """
        Initializes the PlaySystemSound class with an optional audio device.
        :param audio_device: The ALSA device to use for playback (default: 'SysSound_in').
        """
        self.audio_device = audio_device

    def play(self, sound_key):
        """
        Plays a system sound asynchronously.
        :param sound_key: The key representing the sound in SOUND_FILES.
        """
        # Start sound playback in a new thread
        threading.Thread(target=self._play_sound, args=(sound_key,), daemon=True).start()

    def _play_sound(self, sound_key):
        """
        Internal method to play a system sound using `aplay` in a non-blocking way.
        """
        try:
            sound_file = SOUND_FILES.get(sound_key)
#             print(f"ORADIO_DIR is set to: {ORADIO_DIR}")
#             print(f"Expected path: {SOUND_FILES['StartUp']}")
            if not sound_file:
                oradio_log.error("Invalid sound key: %s", sound_key)
                return

            if not os.path.exists(sound_file):
                oradio_log.debug("Sound file does not exist: %s", sound_file)
                return

            command = ["aplay", "-D", self.audio_device, sound_file]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            oradio_log.debug("System sound played successfully: %s", sound_file)

        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("Error playing sound: %s", ex_err)

# ------------------ TEST SECTION ------------------
if __name__ == "__main__":
    print("\nStarting System Sound Player Standalone Test...\n")

    # Initialize the volume_control, works stand alone
    volume_control = VolumeControl()

    # Instantiate sound player
    sound_player = PlaySystemSound()

    # Dynamically create menu options based on available sounds
    sound_keys = list(SOUND_FILES.keys())

    # Generate dynamic input menu
    input_selection = "\nSelect a function, input the number:\n"
    input_selection += " 0 - Quit\n"

    for index, sound_key in enumerate(sound_keys, start=1):
        input_selection += f" {index} - Play {sound_key}\n"

    input_selection += " 99 - Stress Test (random sounds)\n"
    input_selection += "Select: "

    # User command loop
    while True:
        # Get user input
        try:
            function_nr = int(input(input_selection))
        except ValueError:
            function_nr = -1  # Invalid input

        # Execute selected function
        if function_nr == 0:
            print("\nExiting test program...\n")
            break
        elif 1 <= function_nr <= len(sound_keys):
            sound_key = sound_keys[function_nr - 1]
            print(f"\nExecuting: Play {sound_key}\n")
            sound_player.play(sound_key)
        elif function_nr == 99:
            print("\nExecuting: Stress Test\n")

            def stress_test(player, duration=10):
                """Stress test: Randomly play sounds in parallel for a given duration."""
                start_time = time.time()

                def random_sound():
                    while time.time() - start_time < duration:
                        sound_key = random.choice(sound_keys)
                        player.play(sound_key)
                        time.sleep(random.uniform(0.1, 0.5))  # Random delay between plays

                # Launch multiple threads
                thread_list = [threading.Thread(target=random_sound) for _ in range(5)]

                # Start all threads
                for thread in thread_list:
                    thread.start()

                # Wait for all threads to complete
                for thread in thread_list:
                    thread.join()

                print("\nStress test completed.\n")

            stress_test(sound_player)
        else:
            print("\nInvalid selection. Please enter a valid number.\n")
