#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 27, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio Volume control
"""
import time
import alsaaudio
import smbus2
import threading
from queue import Queue

##### oradio modules ####################
import oradio_utils

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
MCP3021_ADDRESS = 0x4D      # I2C address of MCP3021 ADC
ADC_UPDATE_TOLERANCE = 5    # Sensitivity for volume change
POLLING_MIN_INTERVAL = 0.05
POLLING_MAX_INTERVAL = 0.3
ALSA_MIXER_DIGITAL = "Digital"

class VolumeControl:
    def __init__(self, queue=None):
        """ Start volume control thread """
        self.queue = queue
        self.running = True

        # Initialize ALSA Mixer
        try:
            self.mixer = alsaaudio.Mixer(ALSA_MIXER_DIGITAL)
        except alsaaudio.ALSAAudioError as e:
            oradio_utils.logging("error", f"Error initializing ALSA mixer: {e}")
            raise

        # Initialize I2C Bus
        self.bus = smbus2.SMBus(1)

        # Start the monitoring thread
        self.thread = threading.Thread(target=self.volume_adc, daemon=True)
        self.thread.start()

    def read_adc(self):
        """ Reads the ADC value from the MCP3021 sensor via I2C. """
        try:
            data = self.bus.read_i2c_block_data(MCP3021_ADDRESS, 0, 2)
            adc_value = ((data[0] & 0x3F) << 6) | (data[1] >> 2)
            return adc_value
        except OSError as e:
            oradio_utils.logging("error", f"I2C read error: {e}")
            return None

    def scale_adc_to_volume(self, adc_value, adc_max=1023, vol_min=VOLUME_MINIMUM, vol_max=VOLUME_MAXIMUM):
        """ Scales the ADC value to the raw volume range. """
        return int(((adc_value / adc_max) * (vol_max - vol_min)) + vol_min)

    def set_volume(self, volume_raw):
        """ Sets the volume using the ALSA mixer. """
        try:
            self.mixer.setvolume(volume_raw, units=alsaaudio.VOLUME_UNITS_RAW)
            oradio_utils.logging("info", f"Volume set to: {volume_raw}")
#            print(f"Volume set to: {volume_raw}")  # Print for testing
        except alsaaudio.ALSAAudioError as e:
            oradio_utils.logging("error", f"Error setting volume: {e}")

    def send_message(self, message_type, state):
        """ Sends a message to the specified queue. """
        if self.queue:
            try:
                message = {"type": message_type, "state": state}
                self.queue.put(message)
                oradio_utils.logging("info", f"Message sent to queue: {message}")
            except Exception as e:
                oradio_utils.logging("error", f"Error sending message to queue: {e}")

    def volume_adc(self):
        """ Monitors the ADC for changes and adjusts the volume accordingly. """
        previous_adc_value = self.read_adc() or 0
        polling_interval = POLLING_MAX_INTERVAL

        # Initial volume adjustment
        initial_adc_value = self.read_adc()
        if initial_adc_value is not None:
            initial_volume = self.scale_adc_to_volume(initial_adc_value)
            self.set_volume(initial_volume)
            oradio_utils.logging("info", f"Initial volume set to: {initial_volume}")

        while self.running:
            adc_value = self.read_adc()
            if adc_value is not None:
                volume = self.scale_adc_to_volume(adc_value)

                if abs(adc_value - previous_adc_value) > ADC_UPDATE_TOLERANCE:
                    previous_adc_value = adc_value
                    self.set_volume(volume)
#                   print(f"ADC Value: {adc_value}, Volume: {volume}")  # Print for testing

                    if polling_interval >= POLLING_MAX_INTERVAL:
                        self.send_message(MESSAGE_TYPE_VOLUME, MESSAGE_STATE_CHANGED)
                    
                    polling_interval = POLLING_MIN_INTERVAL
                else:
                    polling_interval = min(polling_interval + 0.01, POLLING_MAX_INTERVAL)
            else:
                oradio_utils.logging("warning", "ADC read failed. Retrying...")

            time.sleep(polling_interval)

    def stop(self):
        """ Stops the monitoring thread gracefully. """
        self.running = False
        self.thread.join()

# Test section
if __name__ == "__main__":
    print("\nStarting VolumeControl test...\n")
    print("Turn the volume knob and observe ADC values and volume settings.")
    print("Press Ctrl+C to exit.\n")

    queue = Queue()
    volume_control = VolumeControl(queue)

    try:
        while True:
            time.sleep(1)  # Keep the script running
    except KeyboardInterrupt:
        print("\nStopping VolumeControl...")
        volume_control.stop()
        print("Test finished.")