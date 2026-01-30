#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on January 15, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    CI-safe deprecation checker for Oradio3
    - Mocks all hardware, network, and subprocess dependencies
    - Overrides subprocess.run and check_output to ignore all system calls
    - Activates the deprecation guard
    - Imports `oradio_control` to trigger Python deprecation warnings
"""
#import sys
#import types
#import subprocess
from pathlib import Path
import importlib
import traceback
import warnings

# --------------------- #
# Dummy classes/modules #
# --------------------- #

'''
# ----- GPIO -----
class DummyGPIO:
    BCM = IN = OUT = PUD_UP = PUD_DOWN = HIGH = LOW = BOTH = RISING = FALLING = None
    @staticmethod
    def setmode(mode): pass
    @staticmethod
    def setwarnings(flag): pass
    @staticmethod
    def setup(pin, mode, pull_up_down=None, initial=None): pass
    @staticmethod
    def add_event_detect(pin, edge, callback=None, bouncetime=None): pass
    @staticmethod
    def remove_event_detect(pin): pass
    @staticmethod
    def input(pin): return DummyGPIO.HIGH
    @staticmethod
    def cleanup(): pass

sys.modules['RPi.GPIO'] = DummyGPIO
sys.modules['_lgpio'] = DummyGPIO
'''

'''
# ----- I2C -----
class DummySMBus:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

dummy_smbus_module = types.ModuleType('smbus2')
dummy_smbus_module.SMBus = DummySMBus
sys.modules['smbus2'] = dummy_smbus_module
'''

'''
#REVIEW Onno: replace after GPIO rework
# ----- LED -----
class DummyLEDControl:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

dummy_led_module = types.ModuleType("led_control")
dummy_led_module.LEDControl = DummyLEDControl
sys.modules["led_control"] = dummy_led_module
'''

'''
# ----- MPD -----
class DummyMPDControl:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

class DummyMPDMonitor:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

dummy_mpd_module = types.ModuleType("mpd_control")
dummy_mpd_module.MPDControl = DummyMPDControl
sys.modules["mpd_control"] = dummy_mpd_module

dummy_mpd_monitor_module = types.ModuleType("mpd_monitor")
dummy_mpd_monitor_module.MPDMonitor = DummyMPDMonitor
sys.modules["mpd_monitor"] = dummy_mpd_monitor_module

sys.modules["mpd_service"] = types.ModuleType("mpd_service")
'''

'''
# ----- Spotify -----
class DummySpotifyConnect:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

dummy_spotify_module = types.ModuleType("spotify_connect_direct")
dummy_spotify_module.SpotifyConnect = DummySpotifyConnect
sys.modules["spotify_connect_direct"] = dummy_spotify_module
'''

'''
# ----- Volume -----
class DummyVolumeControl:
    def __init__(self, *args, **kwargs): pass
    def __getattr__(self, name): return lambda *a, **k: None

dummy_volume_module = types.ModuleType("volume_control")
dummy_volume_module.VolumeControl = DummyVolumeControl
sys.modules["volume_control"] = dummy_volume_module
'''

'''
# ----------------------- #
# Subprocess overrides    #
# ----------------------- #

class DummyCompletedProcess:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode

def dummy_run(*args, **kwargs):
    """Ignore all subprocess.run calls."""
    return DummyCompletedProcess(stdout="")

def dummy_check_output(*args, **kwargs):
    """Ignore all subprocess.check_output calls."""
    return ""

subprocess.run = dummy_run
subprocess.check_output = dummy_check_output
'''

# ----------------------- #
# Deprecation guard       #
# ----------------------- #

# Activate deprecation guard FIRST
import deprecation_guard

# ----------------------- #
# Import project code     #
# ----------------------- #

# LEGEND:
#   - #: imported by other model

# Identify modules to check
modules = (
    "backlighting",                 # oradio_logging, i2c_service
#   "fastapi_server",
#   "i2c_service",
    "led_control",                  # oradio_logging
    "mpd_control",                  # oradio_logging, singleton, mpd_service
    "mpd_monitor",                  # oradio_logging, singleton, mpd_service
#   "mpd_service",
#   "oradio_const",
    "oradio_control",               # oradio_logging, backlighting, volume_control, mpd_control, mpd_monitor, led_control, touch_buttons, remote_monitoring, spotify_connect_direct, usb_service, web_service, oradio_utils, power_supply_control, system_sounds, throttled_monitor
#   "oradio_logging",
#   "oradio_utils",
    "power_supply_control",         # oradio_logging, i2c_service
    "remote_monitoring",            # oradio_logging, oradio_utils, singleton, wifi_service
#   "singleton",
    "spotify_connect_direct",       # oradio_logging
    "system_sounds",                # oradio_logging
    "throttled_monitor",            # oradio_logging, singleton
    "touch_buttons",                # oradio_logging, system_sounds
    "usb_service",                  # oradio_logging, oradio_utils, singleton, wifi_service
    "volume_control",               # oradio_logging, oradio_utils, i2c_service
    "web_service",                  # oradio_logging, oradio_utils, fastapi_server, wifi_service
#   "wifi_service",
)

# Check for deprecations
for module in modules:
    try:
        print(f"Checking: {module}")
        module = importlib.import_module(module)
    except Exception as err_msg:
        print(f"### Error checking {module}: {err_msg}\n{traceback.format_exc()}")

# Fail on Python deprecations
warnings.simplefilter("error", DeprecationWarning)
warnings.simplefilter("error", PendingDeprecationWarning)
warnings.simplefilter("error", FutureWarning)

# Flush warnings after all C modules loaded
deprecation_guard.flush_warnings()
