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
import threading
from queue import Queue
import alsaaudio
import smbus2


##### oradio modules ####################
from oradio_logging import oradio_log

##### GLOBAL constants ####################
from oradio_const import (
    VOLUME_MINIMUM,
    VOLUME_MAXIMUM,
    MESSAGE_VOLUME_SOURCE,
    MESSAGE_VOLUME_CHANGED,
    MESSAGE_NO_ERROR
)

##### LOCAL constants ####################
MCP3021_ADDRESS = 0x4D      # I2C address of MCP3021 ADC
ADC_UPDATE_TOLERANCE = 5    # Sensitivity for volume change
POLLING_MIN_INTERVAL = 0.05
POLLING_MAX_INTERVAL = 0.3
ALSA_MIXER_DIGITAL = "Digital"

# REVIEW Onno: Is het een optie om checks voor c-extension-no-member in pylintrc uit te schakelen? dan kan het hier weg
# alsaaudio is a C-extension for which pylint cannot check
# pylint: disable=c-extension-no-member

class VolumeControl:
    """ Tracks the volume contral setting and sends message when changed """
    def __init__(self, queue=None):
        """ Start volume control thread """
        self.queue = queue
        self.running = True

        # Initialize ALSA Mixer
        try:
            self.mixer = alsaaudio.Mixer(ALSA_MIXER_DIGITAL)
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error initializing ALSA mixer: %s", ex_err)
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
        except OSError as ex_err:
            oradio_log.error("I2C read error: %s", ex_err)
            return None

    def scale_adc_to_volume(self, adc_value, adc_max=1023, vol_min=VOLUME_MINIMUM, vol_max=VOLUME_MAXIMUM):
        """ Scales the ADC value to the raw volume range. """
        return int(((adc_value / adc_max) * (vol_max - vol_min)) + vol_min)

    def set_volume(self, volume_raw):
        """ Sets the volume using the ALSA mixer. """
        try:
            self.mixer.setvolume(volume_raw, units=alsaaudio.VOLUME_UNITS_RAW)
            oradio_log.debug("Volume set to: %s", volume_raw)
#            print(f"Volume set to: {volume_raw}")  # Print for testing
        except alsaaudio.ALSAAudioError as ex_err:
            oradio_log.error("Error setting volume: %s", ex_err)

    def send_message(self, message_source, state):
        """ Sends a message to the specified queue. """
        if self.queue:
            try:
                message = {"source": message_source, "state": state, "error": MESSAGE_NO_ERROR}
                self.queue.put(message)
                oradio_log.debug("Message sent to queue: %s", message)
            # Queue is unbounded, so Full exception will not be raised
            # message is dict, so TypeError exception will not be raised
            # if queue is not setup properly you can get NameError:
            except NameError as ex_err:
                oradio_log.error("Queue object is not defined: %s", ex_err)
            except AttributeError as ex_err:
                oradio_log.error("Queue object not properly initialized: %s", ex_err)
            # Fallback
# REVIEW Onno: We already catch the possible exceptions. Can we then skip this broad exception?
            except Exception as ex_err: # pylint: disable=broad-exception-caught
                oradio_log.error("Unexpected error sending message to queue: %s", ex_err)

    def volume_adc(self):
        """ Monitors the ADC for changes and adjusts the volume accordingly. """
        previous_adc_value = self.read_adc() or 0
        polling_interval = POLLING_MAX_INTERVAL

        # Initial volume adjustment
        initial_adc_value = self.read_adc()
        if initial_adc_value is not None:
            initial_volume = self.scale_adc_to_volume(initial_adc_value)
            self.set_volume(initial_volume)
            oradio_log.debug("Initial volume set to: %s", initial_volume)

        while self.running:
            adc_value = self.read_adc()
            if adc_value is not None:
                volume = self.scale_adc_to_volume(adc_value)

                if abs(adc_value - previous_adc_value) > ADC_UPDATE_TOLERANCE:
                    previous_adc_value = adc_value
                    self.set_volume(volume)
#                   print(f"ADC Value: {adc_value}, Volume: {volume}")  # Print for testing

                    if polling_interval >= POLLING_MAX_INTERVAL:
                        self.send_message(MESSAGE_VOLUME_SOURCE, MESSAGE_VOLUME_CHANGED)

                    polling_interval = POLLING_MIN_INTERVAL
                else:
                    polling_interval = min(polling_interval + 0.01, POLLING_MAX_INTERVAL)
            else:
                oradio_log.warning("ADC read failed. Retrying...")

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

    message_queue = Queue()
    volume_control = VolumeControl(message_queue)

    try:
        while True:
            time.sleep(1)  # Keep the script running
    except KeyboardInterrupt:
        print("\nStopping VolumeControl...")
        volume_control.stop()
        print("Test finished.")
