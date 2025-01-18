'''

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Januari 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for wifi connectivity services
    :Note
    :Install
    :Documentation
        https://pypi.org/project/usb-monitor/
'''
import os, subprocess
from time import time, sleep
from usbmonitor import USBMonitor
from usbmonitor.attributes import ID_MODEL, ID_MODEL_ID, ID_VENDOR_ID

##### oradio modules ####################
import oradio_utils
from oradio_const import *

def check_usb_present(label):
    '''
    Check if a USB drive is present and drive name matches label
    :param label ==> USB drive name
    :return status ==> True | False
    '''
    # Poll result with timeout
    timeout = time() + USB_POLL_TIMEOUT
    while time() < timeout:

        # Check if USB drive is available
        if os.path.isdir(USB_MOUNT):

            # Get USB label
            cmd = f"eval $(/sbin/blkid -o udev {USB_DEVICE}) && echo ${{ID_FS_LABEL}}"
            result = subprocess.run(cmd, shell = True, capture_output = True, encoding = 'utf-8')
            if result.returncode != 0:
                oradio_utils.logging("error", f"shell script error: {result.stderr}")
            else:
                # True if USB drive label, minus leadingan trailing white spaces, including \n, matches
                return result.stdout.strip() == label

        # Wait before next poll
        sleep(USB_POLL_INTERVAL)

    # Timeout
    return False

def usb_monitor_start(monitor, inserted, removed):
    '''
    Start the USB monitoring with callbacks on insert and remove actions
    :param monitor ==> identfies the monitor daemon
    :param inserted ==> function to call when drive is inserted
    :param removed ==> function to call when drive is removed
    :return monitor ==> mobitor | None
    '''
    # Ignore if monitor is active
    if monitor:
        oradio_utils.logging("warning", "USB monitor already active")
        return monitor

    # Create the USBMonitor instance
    monitor = USBMonitor()

    # Start the daemon
    monitor.start_monitoring(on_connect=inserted, on_disconnect=removed)

    oradio_utils.logging("info", "USB monitor active")

    return monitor

def usb_monitor_stop(monitor):
    '''
    Stop the monitor daemon
    :param monitor ==> identfies the monitor daemon
    :return status ==> None
    '''
    # Only stop if active
    if monitor:
        monitor.stop_monitoring()
        oradio_utils.logging("info", "USB monitor stopped")

    return None

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Check if monitoring is available, i.e. running in Oradio context
    import importlib.util
    system_monitoring = importlib.util.find_spec("system_monitoring")

    '''
    TODO: Move to oradio_utils to determine what to do: logging only, monitoring, ...
    # If monitoring is available then use it
    if system_monitoring:
        import logging.config
        from system_monitoring import system_monitor
        from oradio_data_collector import oradio_data_collector
        from settings import get_config

        # Initialize logging and monitoring
        logging.config.fileConfig(ORADIO_LOGGING_CONFIG)
        status, oradio_config = get_config()
        data_collector = oradio_data_collector()
        sys_monitor = system_monitor(oradio_config, data_collector)

        # No system checks
        sys_monitor.timer_off()
    '''

    # Initialize
    usb_monitor = None

    def usb_inserted(device_id, device_info):
        '''
        Placeholder for module testing
        Handle functionality for when a USB drive is inserted
        '''
        oradio_utils.logging("info", "USB drive inserted")
        #print("device_id=", device_id)
        #print("device_info=", device_info)

    def usb_removed(device_id, device_info):
        '''
        Placeholder for module testing
        Handle functionality for when a USB drive is removed
        '''
        oradio_utils.logging("info", "USB drive removed")
        #print("device_id=", device_id)
        #print("device_info=", device_info)

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-check USB drive present\n"
                       " 2-monitor USB drive inserted | removed\n"
                       " 3-stop USB monitoring\n"
                       "select: "
                       )

    # User command loop
    while True:

        # Get user input
        try:
            function_nr = int(input(input_selection))
        except:
            function_nr = -1

        # Execute selected function
        match function_nr:
            case 0:
                # Be a good citizen: stop if active
                usb_monitor_stop(usb_monitor)
                break
            case 1:
                name = input("Enter USB drive name to check if present: ")
                oradio_utils.logging("info", f"USB drive '{name}' present: {check_usb_present(name)}")
            case 2:
                usb_monitor = usb_monitor_start(usb_monitor, usb_inserted, usb_removed)
                oradio_utils.logging("info", "==> insert or remove a USB drive to test detection")
            case 3:
                usb_monitor = usb_monitor_stop(usb_monitor)
            case _:
                print("\nPlease input a valid number\n")

    '''
    TODO: put check in oradio_utils
    # If monitoring: Stop monitoring
    if system_monitoring:
        sys_monitor.stop()
    '''
