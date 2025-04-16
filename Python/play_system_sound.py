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
import time
import random

##### oradio modules ####################
from oradio_logging import oradio_log
from volume_control import VolumeControl  # (Not used in this updated approach)

##### GLOBAL constants ####################
from oradio_const import *
# SOUND_FILES_DIR is defined in global constants

##### LOCAL VOLUME CONSTANTS ####################
DEFAULT_MPD_VOLUME = 100          # Default MPD volume (is reference volume)
DEFAULT_SPOTIFY_VOLUME = 90      # Default Spotify volume (tuned to MPD mp3 files at 90 dB)
VOLUME_MPD_SYS_SOUND = 70         # MPD volume level for system sound playback
VOLUME_SPOTIFY_SYS_SOUND = 70     # Spotify volume level for system sound playback
DEFAULT_SYS_SOUND_VOLUME = 80    # Volume of the System Sound, to tune it, is constant set

SOUND_FILES = {
    # Sounds
    "StartUp": f"{SOUND_FILES_DIR}/StartUp.wav",
    "Stop":    f"{SOUND_FILES_DIR}/UIT.wav",
    "Play":    f"{SOUND_FILES_DIR}/AAN.wav",
    "Preset1": f"{SOUND_FILES_DIR}/PL1.wav",
    "Preset2": f"{SOUND_FILES_DIR}/PL2.wav",
    "Preset3": f"{SOUND_FILES_DIR}/PL3.wav",
    "Click":   f"{SOUND_FILES_DIR}/click.wav",
    "Next":    f"{SOUND_FILES_DIR}/volgende_nummer.wav",
    # Announcements
    "Spotify":      f"{SOUND_FILES_DIR}/Spotify_melding.wav",
    "NoInternet":   f"{SOUND_FILES_DIR}/NoInternet_melding.wav",
    "NoUSB":        f"{SOUND_FILES_DIR}/NoUSB_melding.wav",
    "WebInterface": f"{SOUND_FILES_DIR}/WebInterface_melding.wav",
    "OradioAP":     f"{SOUND_FILES_DIR}/OradioAP_melding.wav",
    "WifiConnected": f"{SOUND_FILES_DIR}/Wifi_verbonden_melding.wav",
    "WifiNotConnected": f"{SOUND_FILES_DIR}/Niet_Wifi_verbonden_melding.wav",
    "NewPlaylistPreset": f"{SOUND_FILES_DIR}/Nieuwe_afspeellijst_preset.wav",
    "USBPresent": f"{SOUND_FILES_DIR}/USB_aangesloten.wav",
}

class PlaySystemSound:
    """
    Class to play system sounds asynchronously in a separate thread.
    When a sound is played, if it is the first in a batch, the MPD and Spotify volumes
    are set to the reduced system sound levels. Each play() increments a shared counter.
    When an individual playback thread finishes, it decrements that counter.
    When the counter reaches zero, a timer is started to restore the default volumes after
    a short delay. If a new sound is played during that delay, the timer is canceled.
    """

    def __init__(self, audio_device="SysSound_in"):
        """
        Initializes the PlaySystemSound class with an optional audio device.
        :param audio_device: The ALSA device to use for playback (default: 'SysSound_in').
        """
        self.audio_device = audio_device
        self.batch_lock = threading.Lock()  # Protects the counter and volume adjustments.
        self.active_count = 0
        self.restore_timer = None  # Timer to delay volume restoration.
        self._set_sys_volume(DEFAULT_SYS_SOUND_VOLUME) # set sys sound ones at the default value

    def play(self, sound_key):
        """
        Plays a system sound asynchronously.
        For non-"Click" sounds, if no other sounds are active, the volumes are set to the reduced levels.
        If a restore timer is pending, it is canceled.
        :param sound_key: The key representing the sound in SOUND_FILES.
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

        threading.Thread(target=self._play_sound_and_restore, args=(sound_key,), daemon=True).start()
        
    def _set_sys_volume(self, volume):
        """
        Sets the system sound volume using the amixer command.
        :param volume: The volume level to set (integer).
        """
        oradio_log.debug("Setting Sys Sound volume to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSysSound", f"{volume}%"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )        


    def _set_mpd_volume(self, volume):
        """
        Sets the MPD volume using the 'amixer command.
        :param volume: The volume level to set (integer).
        """
        oradio_log.debug("Setting MPD volume to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeMPD", f"{volume}%"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def _set_spotify_volume(self, volume):
        """
        Sets the Spotify volume using the 'amixer' command.
        :param volume: The volume level to set (integer).
        """
        oradio_log.debug("Setting Spotify volume to %s%%", volume)
        subprocess.run(
            ["amixer", "-c", "DigiAMP", "sset", "VolumeSpotCon2", f"{volume}%"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def _play_sound(self, sound_key):
        """
        Internal method to play a system sound using `aplay` in a non-blocking way.
        """
        try:
            sound_file = SOUND_FILES.get(sound_key)
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

    def _restore_volumes(self):
        """
        Restores the default MPD and Spotify volumes.
        """
        with self.batch_lock:
            if self.active_count == 0:
                try:
                    self._set_mpd_volume(DEFAULT_MPD_VOLUME)
                    self._set_spotify_volume(DEFAULT_SPOTIFY_VOLUME)
                except Exception as e:
                    oradio_log.error("Error restoring default volumes: %s", e)
                self.restore_timer = None

    def _play_sound_and_restore(self, sound_key):
        """
        Wrapper method that plays the sound and then, in the finally block,
        decrements the active counter. When the counter reaches zero, a timer is
        started to restore the default volumes after a short delay.
        """
        try:
            self._play_sound(sound_key)
        finally:
            with self.batch_lock:
                self.active_count -= 1
                if self.active_count == 0:
                    # Delay volume restoration by 1 seconds to allow new sounds to join the batch.
                    self.restore_timer = threading.Timer(1, self._restore_volumes)
                    self.restore_timer.start()

# ------------------ TEST SECTION ------------------
if __name__ == "__main__":
    print("\nStarting System Sound Player Standalone Test...\n")
    # Instantiate sound player
    sound_player = PlaySystemSound()
    # Dynamically create menu options based on available sounds
    sound_keys = list(SOUND_FILES.keys())
    # Build the dynamic input menu with an additional option for setting system volume
    input_selection = "\nSelect a function, input the number:\n"
    input_selection += " 0 - Quit\n"
    input_selection += " 1 - Set system volume\n"
    # Enumerate sound play options starting at 2
    for index, sound_key in enumerate(sound_keys, start=2):
        input_selection += f" {index} - Play {sound_key}\n"
    input_selection += " 99 - Stress Test (random sounds)\n"
    input_selection += "Select: "

    # User command loop
    while True:
        try:
            function_nr = int(input(input_selection))
        except ValueError:
            function_nr = -1  # Invalid input
        
        if function_nr == 0:
            print("\nExiting test program...\n")
            break
        elif function_nr == 1:
            # Option to set system volume
            try:
                new_volume = int(input("Enter new system volume (in %): "))
                sound_player._set_sys_volume(new_volume)
                print(f"System volume set to {new_volume}%.")
            except ValueError:
                print("Invalid volume value; please enter an integer.")
            except subprocess.CalledProcessError as e:
                print(f"Error setting system volume: {e}")
        elif 2 <= function_nr <= (len(sound_keys) + 1):
            sound_key = sound_keys[function_nr - 2]
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
                        time.sleep(random.uniform(0.1, 0.5))
                thread_list = [threading.Thread(target=random_sound) for _ in range(5)]
                for thread in thread_list:
                    thread.start()
                for thread in thread_list:
                    thread.join()
                print("\nStress test completed.\n")
            stress_test(sound_player)
        else:
            print("\nInvalid selection. Please enter a valid number.\n")