#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on December 23, 2024
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Thread running volume control
"""
import time
import alsaaudio
import smbus2
from threading import Thread

# Constants and settings
MCP3021_ADDRESS = 0x4D   # I2C address of MCP3021
VolMin = 80              # Minimum volume (raw units)
VolMax = 180             # Maximum volume (raw units)
adc_UpdateTolerance = 5  # Sensitivity for volume change
VolumeOnThreshold = 10   # ADC change to transition to play when stopped

# I2C bus for MCP3021 ADC
bus = smbus2.SMBus(1)

# Initialize ALSA mixer
try:
    mixer = alsaaudio.Mixer("Digital")
except alsaaudio.ALSAAudioError as e:
    print(f"Error initializing ALSA mixer: {e}")
    exit(1)

# Read ADC value
def read_adc():
    try:
        data = bus.read_i2c_block_data(MCP3021_ADDRESS, 0, 2)
        adc_value = ((data[0] & 0x3F) << 6) | (data[1] >> 2)
        return adc_value
    except OSError as e:
        print(f"I2C read error: {e}")
        return None

# Scale ADC value to raw volume range (VolMin to VolMax)
def scale_adc_to_volume(adc_value, adc_max=1023, vol_min=VolMin, vol_max=VolMax):
    scaled_value = ((adc_value / adc_max) * (vol_max - vol_min)) + vol_min
    return int(scaled_value)

# Set volume using ALSA
def set_volume(volume_raw):
    try:
        mixer.setvolume(volume_raw, units=alsaaudio.VOLUME_UNITS_RAW)
#        print(f"Volume set to: {volume_raw} (raw units)")
    except alsaaudio.ALSAAudioError as e:
        print(f"Error setting volume: {e}")

# Monitor ADC and adjust volume
def monitor_adc(state_machine):
    previous_adc_value = read_adc() or 0
    polling_interval = 0.2

    # Perform initial ADC volume adjustment
    initial_adc_value = read_adc()
    if initial_adc_value is not None:
        initial_volume = scale_adc_to_volume(initial_adc_value)
        set_volume(initial_volume)
        print(f"Initial volume set to: {initial_volume}")

    while True:
        adc_value = read_adc()
        if adc_value is not None:
            # Scale ADC value to raw volume range
            volume = scale_adc_to_volume(adc_value)
            # Check if the volume change exceeds the threshold and transition state if needed
            if (abs(adc_value - previous_adc_value) > VolumeOnThreshold) and state_machine.state == "StateStop":
                state_machine.transition("StatePlay")
                previous_adc_value = adc_value
            # Update the volume if it changes more than the tolerance
            elif abs(adc_value - previous_adc_value) > adc_UpdateTolerance:
                previous_adc_value = adc_value
                set_volume(volume)
                polling_interval = 0.05
            else:
                polling_interval = min(polling_interval + 0.01, 0.3)
        time.sleep(polling_interval)

# Start monitoring in a separate thread
def start_monitoring(state_machine):
    thread = Thread(target=monitor_adc, args=(state_machine,))
    thread.daemon = True  # Ensure thread exits when the main program does
    thread.start()
