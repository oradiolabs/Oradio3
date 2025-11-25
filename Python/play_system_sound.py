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
This module handles playback of system sounds and temporary "ducking" of
other audio channels (MPD and Spotify). It restores their volumes smoothly
after all system sounds have finished.
Features:
- Asynchronous sound playback
- Batch-ducking: volumes are only lowered once during bursts of sounds
- Cancelable and smooth volume-restore ramp
- Thread-safe operations
- Stand-alone test menu
"""
import os
#REVIEW Onno: Consider replacing subprocess with oradio_utils.run_shell_script()
import subprocess
from threading import Thread, Lock, Timer, Event
import time
import random

##### oradio modules ####################
from oradio_logging import oradio_log
from singleton import singleton

##### GLOBAL constants ####################
from oradio_const import SOUND_FILES_DIR

##### LOCAL VOLUME CONSTANTS ####################
DEFAULT_MPD_VOLUME       = 100
DEFAULT_SPOTIFY_VOLUME   = 100
VOLUME_MPD_SYS_SOUND     = 80
VOLUME_SPOTIFY_SYS_SOUND = 80
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

@singleton
class PlaySystemSound:
    """
    Plays system sounds asynchronously while automatically lowering (ducking)
    MPD and Spotify volumes. When all system sounds are finished, the volumes
    are restored smoothly over time.
    Functional overview:
    - Multiple sounds may be triggered rapidly; ducking happens only once.
    - After the final sound finishes, the system waits briefly (batching) and
      then begins a cancelable restore ramp.
    - Any new sound immediately cancels an ongoing restore.
    """
    def __init__(self, audio_device="SysSound_in"):
        """Initialize state, locks, volume defaults, and restore flags."""

        # ALSA device name used for system sound output
        self.audio_device  = audio_device

        # Protects access to active_count and restore scheduling
        self.batch_lock    = Lock()

        # Number of system sounds currently active or queued
        self.active_count  = 0

        # Delayed timer used to trigger restore after final sound
        self.restore_timer = None

        # Event used to cancel ongoing volume‑restore ramps
        self._restore_cancel = Event()

        # Thread object for a running restore ramp
        self._restore_thread = None

        # Ensure the system-sound channel is at its intended default volume
        self._set_sys_volume(DEFAULT_SYS_SOUND_VOLUME)

# ----- Utility helpers -----

    @staticmethod
    def _clamp(val, lower_bound, upper_bound) -> int:
        """Clamp a value within the given bounds."""
        return max(lower_bound, min(upper_bound, val))

    def _sleep_with_cancel(self, duration: float) -> bool:
        """
        Sleep for up to *duration* seconds, but wake early if a new system
        sound arrives (indicated by `_restore_cancel`). Returns ``True`` when
        canceled.
        """
        end_time = time.time() + duration

        while time.time() < end_time:
            if self._restore_cancel.is_set():
                oradio_log.debug("[Ramp] canceled during sleep")
                return True

            # Sleep in small increments to remain responsive
            remaining = end_time - time.time()
            time.sleep(0.02 if remaining > 0.02 else remaining)

        return False

# ----- Public API -----

    def play(self, sound_key) -> None:
        """
        Play a system sound asynchronously.
        - If this is the first sound in a batch, MPD and Spotify volumes are immediately ducked.
        - If a restore operation is scheduled or running, it is canceled.
        - The sound plays in a background thread.
        """
        with self.batch_lock:
            # Cancel pending delayed restore
            if self.restore_timer is not None:
                self.restore_timer.cancel()
                self.restore_timer = None

            # Cancel a running restore ramp (if any)
            if self._restore_thread is not None and self._restore_thread.is_alive():
                self._restore_cancel.set()  # ask ramp to stop ASAP

            # If this is the first active system sound, apply ducking
            if self.active_count == 0:
                try:
                    self._set_mpd_volume(VOLUME_MPD_SYS_SOUND)
                    self._set_spotify_volume(VOLUME_SPOTIFY_SYS_SOUND)
                except (subprocess.CalledProcessError, OSError) as err:
                    oradio_log.error("Error setting system sound volumes: %s", err)

            # Increment active sound counter
            self.active_count += 1

        # Play sound in detached thread
        Thread(target=self._play_sound_and_restore, args=(sound_key,), daemon=True).start()

# ----- Volume setters (AMixer interface) -----

    def _set_sys_volume(self, volume) -> None:
        """Set volume of system‑sound ALSA channel."""
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSysSound", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_mpd_volume(self, volume) -> None:
        """Set MPD volume controller (ducked or restored)."""
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeMPD", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _set_spotify_volume(self, volume) -> None:
        """Set Spotify volume controller (ducked or restored)."""
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSpotCon2", f"{volume}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

# ----- Internal helpers: playback and restore -----

    def _play_sound(self, sound_key) -> None:
        """Execute the system command that plays the given sound file."""

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

    def _restore_volumes(self) -> None:
        """
        Initiate a smooth volume‑restore ramp *if* no more system sounds are active.
        Called via delayed Timer.
        """
        with self.batch_lock:
            # If new sounds arrived during delay: don't restore
            if self.active_count != 0:
                self.restore_timer = None
                return

            # Only one concurrent ramp allowed
            if self._restore_thread is not None and self._restore_thread.is_alive():
                self.restore_timer = None
                return

            # Prepare cancellation flag for new ramp
            self._restore_cancel.clear()

            # Start restore thread
            self._restore_thread = Thread(target=self._ramp_restore_volumes, daemon=True)
            self._restore_thread.start()

            self.restore_timer = None

    def _ramp_restore_volumes(self) -> None:
        """
        Smoothly restore MPD and Spotify volumes from ducked levels to their
        normal defaults. The ramp is cancelable and logs each step.
        """

        # Initial and target levels
        start_mpd = VOLUME_MPD_SYS_SOUND
        start_spo = VOLUME_SPOTIFY_SYS_SOUND
        target_mpd = DEFAULT_MPD_VOLUME
        target_spo = DEFAULT_SPOTIFY_VOLUME

        # # How far must we travel?
        dist = max(target_mpd - start_mpd, target_spo - start_spo)
        if dist <= 0:
            return

        # Compute number of steps (ceil division)
        steps = max(1, (dist + RESTORE_VOL_STEP - 1) // RESTORE_VOL_STEP)
        sleep_per_step = RESTORE_VOL_TIME / steps

        current_mpd = start_mpd
        current_spo = start_spo
        oradio_log.debug("[Ramp start] MPD=%s Spotify=%s", start_mpd, start_spo)

        for step_idx in range(steps):
            # Abort if a new system sound is requested
            if self._restore_cancel.is_set():
                oradio_log.debug("[Ramp] canceled")
                return

            # Increment volumes
            current_mpd = self._clamp(current_mpd + RESTORE_VOL_STEP, 0, target_mpd)
            current_spo = self._clamp(current_spo + RESTORE_VOL_STEP, 0, target_spo)

#            oradio_log.debug("[Ramp step %d/%d] MPD=%s Spotify=%s", step_idx+1, steps, current_mpd, current_spo)

            try:
                self._set_mpd_volume(current_mpd)
                self._set_spotify_volume(current_spo)
            except (subprocess.CalledProcessError, OSError) as err:
                oradio_log.error("Error during volume ramp: %s", err)
                # Continue; transient amixer issues can happen

            # Sleep, but remain cancelable
            if self._sleep_with_cancel(sleep_per_step):
                return

        # Final snap to exact defaults (avoid small rounding errors)
        if not self._restore_cancel.is_set():
            oradio_log.debug("[Ramp final] MPD=%s Spotify=%s", target_mpd, target_spo)
            try:
                self._set_mpd_volume(target_mpd)
                self._set_spotify_volume(target_spo)
            except (subprocess.CalledProcessError, OSError) as err:
                oradio_log.error("Error finalizing volume restore: %s", err)

    def _play_sound_and_restore(self, sound_key) -> None:
        """
        Wrapper that plays a sound and then decrements the active counter.
        When the last sound finishes, schedules a delayed restore.
        """
        try:
            self._play_sound(sound_key)
        finally:
            with self.batch_lock:
                self.active_count -= 1

                # If that was the last sound, start 0.2s batching delay
                if self.active_count == 0:
                    self.restore_timer = Timer(0.2, self._restore_volumes)
                    self.restore_timer.start()

# ----- Stand‑alone test menu -----

if __name__ == "__main__":

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    print("\nStarting System Sound Player Standalone Test...\n")

    sound_player = PlaySystemSound()
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

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
