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

Reducing volume when system sounds are played. The volume control is independent of the master volume.
The MPD and Spotify channel can be controlled with amixer -c DigiAMP sset "VolumeMPD" 100% for the MPD
amixer -c DigiAMP sset "VolumeSpotCon2" 100% for Spotify.
With this update, it is not needed that the statemachine needs to manage the start stop of the Sound
During syssounds
Also the Sound file Next and USBpresent is added
And a volume controller to set the system sound
"""
import os
import subprocess
import threading
import queue
import time
import random

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl

##### GLOBAL constants ####################
from oradio_const import SOUND_FILES_DIR

##### LOCAL VOLUME CONSTANTS ####################
DEFAULT_MPD_VOLUME       = 100
DEFAULT_SPOTIFY_VOLUME   = 100
VOLUME_MPD_SYS_SOUND     = 70
VOLUME_SPOTIFY_SYS_SOUND = 70
DEFAULT_SYS_SOUND_VOLUME = 90

SOUND_FILES = {
    # Sounds
    "StartUp": f"{SOUND_FILES_DIR}/StartUp.wav",
    "Stop":    f"{SOUND_FILES_DIR}/UIT.wav",
    "Play":    f"{SOUND_FILES_DIR}/AAN.wav",
    "Click":   f"{SOUND_FILES_DIR}/click.wav",
    "Attention":   f"{SOUND_FILES_DIR}/Attention.wav",
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
    """
    Singleton class to play system sounds asynchronously in a separate thread,
    with batch‐ducking of MPD/Spotify volumes and delayed restoration.
    """

    # ——— Singleton machinery ———
    _instance = None
    def __new__(cls, audio_device="SysSound_in"):
        # If no instance exists yet, create one; otherwise return the existing one
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, audio_device="SysSound_in"):
        # Only run init once
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
    # ——————————————————————————

        self.audio_device = audio_device
        self.batch_lock     = threading.Lock()
        self.active_count   = 0
        self.restore_timer  = None

        # Ensure system‐sound channel is at its default level
        self._set_sys_volume(DEFAULT_SYS_SOUND_VOLUME)

    def play(self, sound_key):
        """
        Plays a system sound asynchronously.
        Ducks volumes on first sound of a batch, and schedules restore when count goes to zero.
        """
        with self.batch_lock:
            if self.restore_timer is not None:
                self.restore_timer.cancel()
                self.restore_timer = None
            if self.active_count == 0:
                try:
                    self._set_mpd_volume(VOLUME_MPD_SYS_SOUND)
                    self._set_spotify_volume(VOLUME_SPOTIFY_SYS_SOUND)
                except Exception as e:
                    oradio_log.error("Error setting system sound volumes: %s", e)
            self.active_count += 1

        threading.Thread(
            target=self._play_sound_and_restore,
            args=(sound_key,),
            daemon=True
        ).start()

    def _set_sys_volume(self, volume):
        oradio_log.debug("Setting Sys Sound volume to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSysSound", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_mpd_volume(self, volume):
        oradio_log.debug("Setting MPD volume controller to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeMPD", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_spotify_volume(self, volume):
        oradio_log.debug("Setting Spotify volume controller to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSpotCon2", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _play_sound(self, sound_key):
        try:
            sound_file = SOUND_FILES.get(sound_key)
            if not sound_file:
                oradio_log.error("Invalid sound key: %s", sound_key)
                return
            if not os.path.exists(sound_file):
                oradio_log.debug("Sound file does not exist: %s", sound_file)
                return
            subprocess.run(
                ["aplay", "-D", self.audio_device, sound_file],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            oradio_log.debug("System sound played successfully: %s", sound_file)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error("Error playing sound: %s", ex_err)

    def _restore_volumes(self):
        with self.batch_lock:
            if self.active_count == 0:
                try:
                    self._set_mpd_volume(DEFAULT_MPD_VOLUME)
                    self._set_spotify_volume(DEFAULT_SPOTIFY_VOLUME)
                except Exception as e:
                    oradio_log.error("Error restoring default volumes: %s", e)
                self.restore_timer = None

    def _play_sound_and_restore(self, sound_key):
        try:
            self._play_sound(sound_key)
        finally:
            with self.batch_lock:
                self.active_count -= 1
                if self.active_count == 0:
                    # Delay restore by 1s to batch rapid-fire calls
                    self.restore_timer = threading.Timer(0.5, self._restore_volumes)
                    self.restore_timer.start()

# ------------------ TEST SECTION ------------------
if __name__ == "__main__":
    print("\nStarting System Sound Player Standalone Test...\n")

    volume_control = VolumeControl()
    sound_player = PlaySystemSound()
    sound_keys = list(SOUND_FILES.keys())

    def build_menu():
        menu = "\nSelect a function:\n 0  - Quit\n"
        for i, k in enumerate(sound_keys, 1):
            menu += f" {i:<3}- Play {k}\n"
        menu += " 99 - Stress Test (random sounds)\n"
        menu += "100 - Custom Sequence Test (enter 5 sound numbers)\n"
        menu += "Select: "
        return menu

    while True:
        try:
            choice = int(input(build_menu()))
        except ValueError:
            choice = -1

        if choice == 0:
            print("\nExiting test program...\n")
            break

        elif 1 <= choice <= len(sound_keys):
            key = sound_keys[choice - 1]
            print(f"\nEnqueue: Play {key}\n")
            sound_player.play(key)

        elif choice == 99:
            print("\nExecuting: Stress Test\n")
            def stress_test(player, duration=10):
                start = time.time()
                def rnd():
                    while time.time() - start < duration:
                        player.play(random.choice(sound_keys))
                        time.sleep(random.uniform(0.1, 0.5))
                threads = [threading.Thread(target=rnd) for _ in range(5)]
                for t in threads: t.start()
                for t in threads: t.join()
                print("\nStress test completed.\n")
            stress_test(sound_player)

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
            print("\nInvalid selection. Please enter a valid number.\n")
