#!/usr/bin/env python3

"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 31`, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Oradio hw serial number generator

Usage:
sudo chmod +x /home/pi/Oradio3/Python/hw_serial_number_gen.py

/etc/systemd/system/hw_serial_number_gen.service :
------------------------
[Unit]
Description=Generate HW Serial Number for Oradio
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/home/pi/.venv/bin/python3 /home/pi/Oradio3/Python/hw_serial_number_gen.py
WorkingDirectory=/home/pi/Oradio3/Python
RemainAfterExit=true

[Install]
WantedBy=multi-user.target

-------------------------

THe script will: 

Check if /var/log/oradio_hw_version.log exists.
If the file doesn’t exist, it will check for the presence of I2C device 0x4D (MCP3021).
If the device is detected, it will create the log file with a timestamp
timestamp formatted as YYYY-MM-DD-HH-MM-SS.
"""



import os
import json
import sys
import smbus2
from datetime import datetime

# Local Constants
I2C_BUS = 1  # Typically 1 on modern Raspberry Pis
MCP3021_ADDRESS = 0x4D
LOG_FILE = "/var/log/oradio_hw_version.log"

def get_europe_time_serial():
    """
    Get the current timestamp formatted as:
    YYYY-MM-DD-HH-MM-SS

    Note: This function uses the system's local time.
    Ensure your Raspberry Pi's timezone is set to Europe/Amsterdam.
    """
    now = datetime.now()  # Assumes the local timezone is Europe/Amsterdam
    return now.strftime("%Y-%m-%d-%H-%M-%S")

def i2c_device_present(address):
    """
    Check if an I2C device is present at the given address.
    Returns True if detected, False otherwise.
    """
    try:
        bus = smbus2.SMBus(I2C_BUS)
        bus.read_byte(address)  # Attempt to read a byte to verify device presence
        bus.close()
        return True
    except Exception:
        return False

def create_hw_serial_file():
    """
    Create the HW serial number file if the I2C device is detected.
    If the file already exists, notify the user.
    """
    if os.path.exists(LOG_FILE):
        print(f"HW serial file already exists at {LOG_FILE}.")
        return

    if i2c_device_present(MCP3021_ADDRESS):
        hw_info = {
            "serial": get_europe_time_serial(),  # Timestamp as a serial number
            "hw_detected": "MCP3021 found at 0x4D"
        }
        # Ensure the directory exists
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "w") as f:
            json.dump(hw_info, f, indent=4)
        print("HW serial file created.")
    else:
        print("I2C device not detected. HW serial file not created.")

def read_hw_serial_file():
    """
    Read and display the contents of the HW serial number file.
    """
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
        print("Contents of HW serial file:")
        print(json.dumps(data, indent=4))
    else:
        print(f"HW serial file does not exist at {LOG_FILE}.")

def delete_hw_serial_file():
    """
    Delete the HW serial number file if it exists.
    """
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
        print("HW serial file deleted.")
    else:
        print(f"HW serial file does not exist at {LOG_FILE}.")

def interactive_menu():
    """
    Display a selection menu for testing.
    """
    while True:
        print("\nSelect an option:")
        print(" 0: Quit")
        print(" 1: Make HW serial number file")
        print(" 2: Read HW serial file")
        print(" 3: Delete HW serial number file")
        try:
            choice = input("Enter your choice: ").strip()
        except EOFError:
            break

        if choice == "0":
            print("Exiting.")
            break
        elif choice == "1":
            create_hw_serial_file()
        elif choice == "2":
            read_hw_serial_file()
        elif choice == "3":
            delete_hw_serial_file()
        else:
            print("Invalid selection. Please try again.")

def systemd_mode():
    """
    Non-interactive mode for use with systemd.
    If the HW serial file does not exist and the I2C device is detected,
    the file is created.
    """
    if os.path.exists(LOG_FILE):
        return  # File exists; nothing to do.
    if i2c_device_present(MCP3021_ADDRESS):
        hw_info = {
            "serial": get_europe_time_serial(),
            "hw_detected": "MCP3021 found at 0x4D"
        }
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "w") as f:
            json.dump(hw_info, f, indent=4)

if __name__ == "__main__":
    # If running interactively (with a terminal attached), show the menu.
    if sys.stdin.isatty():
        interactive_menu()
    else:
        systemd_mode()