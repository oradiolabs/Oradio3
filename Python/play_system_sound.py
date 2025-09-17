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
from threading import Thread, Lock, Timer, Event
import time
import random

##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import SOUND_FILES_DIR

##### LOCAL VOLUME CONSTANTS ####################
DEFAULT_MPD_VOLUME       = 100
DEFAULT_SPOTIFY_VOLUME   = 100
VOLUME_MPD_SYS_SOUND     = 70
VOLUME_SPOTIFY_SYS_SOUND = 70
DEFAULT_SYS_SOUND_VOLUME = 70

# Smooth restore configuration
RESTORE_VOL_TIME = 2.0   # seconds (total duration of the unduck ramp)
RESTORE_VOL_STEP = 2     # percentage points per step during restore

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
    with batch-ducking of MPD/Spotify volumes and delayed, smooth restoration.
    """
# In below code using same construct in multiple modules for singletons
# pylint: disable=duplicate-code

    _lock = Lock()       # Class-level lock to make singleton thread-safe
    _instance = None     # Holds the single instance of this class
    _initialized = False # Tracks whether __init__ has been run

    # Underscores marks audio_device 'intentionally unused'
    def __new__(cls, _audio_device="SysSound_in"):
        """Ensure only one instance of PlaySystemSound is created (singleton pattern)"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, audio_device="SysSound_in"):
        """Initialize audio device"""
        # Prevent re-initialization if the singleton is created again
        if self._initialized:
            return  # Avoid re-initialization if already done
        self._initialized = True

# In above code using same construct in multiple modules for singletons
# pylint: enable=duplicate-code

        self.audio_device  = audio_device
        self.batch_lock    = Lock()
        self.active_count  = 0
        self.restore_timer = None

        # Cancelable smooth-restore state
        self._restore_cancel = Event()
        self._restore_thread = None

        # Debug printing flag (enabled only in standalone test)
        self.debug_print = False

        # Ensure system‐sound channel is at its default level
        self._set_sys_volume(DEFAULT_SYS_SOUND_VOLUME)

    @staticmethod
    def _clamp(val, lower_bound, upper_bound):
        """Clamp integer volume to [lower_bound, upper_bound]."""
        return max(lower_bound, min(upper_bound, val))

    def _dprint(self, msg: str) -> None:
        """Print only in standalone test mode."""
        if self.debug_print:
            print(msg)

    def _sleep_with_cancel(self, duration: float) -> bool:
        """
        Sleep up to 'duration' seconds but exit early if restore is canceled.
        Returns True if canceled during the sleep.
        """
        end_time = time.time() + duration
        while time.time() < end_time:
            if self._restore_cancel.is_set():
                self._dprint("[Ramp] canceled during sleep")
                return True
            # Keep checks responsive without busy-waiting
            remaining = end_time - time.time()
            time.sleep(0.02 if remaining > 0.02 else remaining)
        return False

    def play(self, sound_key):
        """
        Plays a system sound asynchronously.
        Ducks volumes on first sound of a batch, and schedules restore when count goes to zero.
        Cancels any pending or in-progress restore-ramp to avoid fighting with ducking.
        """
        with self.batch_lock:
            # Cancel pending delayed restore timer
            if self.restore_timer is not None:
                self.restore_timer.cancel()
                self.restore_timer = None

            # Cancel an in-progress ramp restore (if any)
            if self._restore_thread is not None and self._restore_thread.is_alive():
                self._restore_cancel.set()  # ask ramp to stop ASAP

            if self.active_count == 0:
                try:
                    self._set_mpd_volume(VOLUME_MPD_SYS_SOUND)
                    self._set_spotify_volume(VOLUME_SPOTIFY_SYS_SOUND)
                except (subprocess.CalledProcessError, OSError) as err:
                    oradio_log.error("Error setting system sound volumes: %s", err)
            self.active_count += 1

        Thread(
            target=self._play_sound_and_restore,
            args=(sound_key,),
            daemon=True
        ).start()

    def _set_sys_volume(self, volume):
#        oradio_log.debug("Setting Sys Sound volume to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSysSound", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_mpd_volume(self, volume):
#        oradio_log.debug("Setting MPD volume controller to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeMPD", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_spotify_volume(self, volume):
#        oradio_log.debug("Setting Spotify volume controller to %s%%", volume)
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
        """
        Start a smooth, cancelable ramp to restore volumes to defaults.
        Called after the batch delay when active_count reached zero.
        """
        with self.batch_lock:
            if self.active_count != 0:
                self.restore_timer = None
                return

            # If there’s already a ramp, don’t start another
            if self._restore_thread is not None and self._restore_thread.is_alive():
                self.restore_timer = None
                return

            # Prepare a fresh cancel flag and start ramp thread
            self._restore_cancel.clear()
            self._restore_thread = Thread(
                target=self._ramp_restore_volumes,
                daemon=True
            )
            self._restore_thread.start()
            self.restore_timer = None

    def _ramp_restore_volumes(self):
        """
        Ramps MPD and Spotify volumes from their ducked levels back to defaults.
        Cancels immediately if a new system sound starts.
        Prints each step via _dprint() when in test mode.
        """
        start_mpd = VOLUME_MPD_SYS_SOUND
        start_spo = VOLUME_SPOTIFY_SYS_SOUND
        target_mpd = DEFAULT_MPD_VOLUME
        target_spo = DEFAULT_SPOTIFY_VOLUME

        # Determine the number of steps based on the larger distance
        dist = max(target_mpd - start_mpd, target_spo - start_spo)
        if dist <= 0:
            return

        # Ceil division: how many RESTORE_VOL_STEP increments to reach target
        steps = max(1, (dist + RESTORE_VOL_STEP - 1) // RESTORE_VOL_STEP)
        sleep_per_step = RESTORE_VOL_TIME / steps

        current_mpd = start_mpd
        current_spo = start_spo

        for step_idx in range(steps):
            if self._restore_cancel.is_set():
                self._dprint("[Ramp] canceled")
                return

            current_mpd = self._clamp(current_mpd + RESTORE_VOL_STEP, 0, target_mpd)
            current_spo = self._clamp(current_spo + RESTORE_VOL_STEP, 0, target_spo)

            self._dprint(f"[Ramp step {step_idx+1}/{steps}] MPD={current_mpd} Spotify={current_spo}")

            try:
                self._set_mpd_volume(current_mpd)
                self._set_spotify_volume(current_spo)
            except (subprocess.CalledProcessError, OSError) as err:
                oradio_log.error("Error during volume ramp: %s", err)
                # Continue; transient amixer issues can happen

            if self._sleep_with_cancel(sleep_per_step):
                return

        # Final snap to exact defaults to avoid off-by-one rounding
        if not self._restore_cancel.is_set():
            self._dprint(f"[Ramp final] MPD={target_mpd} Spotify={target_spo}")
            try:
                self._set_mpd_volume(target_mpd)
                self._set_spotify_volume(target_spo)
            except (subprocess.CalledProcessError, OSError) as err:
                oradio_log.error("Error finalizing volume restore: %s", err)

    def _play_sound_and_restore(self, sound_key):
        try:
            self._play_sound(sound_key)
        finally:
            with self.batch_lock:
                self.active_count -= 1
                if self.active_count == 0:
                    # Delay restore by 0.5s to batch rapid-fire calls
                    self.restore_timer = Timer(0.2, self._restore_volumes)
                    self.restore_timer.start()

# ------------------ TEST SECTION ------------------
if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print("\nStarting System Sound Player Standalone Test...\n")

    sound_player = PlaySystemSound()
    sound_player.debug_print = True   # enable ramp step printing in test mode
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
                print(f"\nEnqueue: Play {key}\n")
                sound_player.play(key)

            elif choice == 99:
                print("\nExecuting: Stress Test\n")
                def stress_test(player, duration=10):
                    """ Run stress test playing sound files randomly """
                    start = time.time()
                    def rnd():
                        while time.time() - start < duration:
                            player.play(random.choice(sound_keys))
                            time.sleep(random.uniform(0.1, 0.5))
                    threads = [Thread(target=rnd) for _ in range(5)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
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

    # Present menu with tests
    interactive_menu()
