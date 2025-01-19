import os, json

'''
Simulate usb drive with wifi settings in root
'''
import oradio_utils
import wifi_utils, usb_utils
from oradio_const import *

def check_usb_wifi_settings():
    '''
    Check if wifi settings are available on the USB drive root folder
    If exists, then try to connect using the wifi crendtials from the file
    '''
    oradio_utils.logging("info", "Look for wifi credentials on USB drive")

    # Check if wifi settings file exists in USB drive root
    if not usb_utils.check_usb_present(USB_ORADIO) or not os.path.isfile(USB_WIFI_FILE):
        oradio_utils.logging("info", f"Oradio USB drive not found or {USB_WIFI_FILE} not found on USB drive")
        return

    # Opening JSON file for reading
    with open(USB_WIFI_FILE, "r") as f:
        # returns JSON object as a dictionary
        data = json.load(f)

    # Initialize
    ssid = None
    pswd = None

    # Get credentials
    if 'SSID' in data.keys():
        ssid = data['SSID']
    else:
        oradio_utils.logging("error", f"SSID not found in {USB_WIFI_FILE}")

    if 'PASSWORD' in data.keys():
        pswd = data['PASSWORD']
    else:
        oradio_utils.logging("error", f"PASSWORD not found in {USB_WIFI_FILE}")

    # Test if currently connected to ssid
    if not ssid == wifi_utils.get_wifi_connection():
        # Currently not connected to ssid, try to connected
        if wifi_utils.wifi_autoconnect(ssid, pswd):
            oradio_utils.logging("success", f"Connected to '{ssid}'")
        else:
            oradio_utils.logging("error", f"Failed to connect to '{ssid}'")
    else:
        oradio_utils.logging("info", f"Already connected to '{ssid}'")

def usb_inserted(device_id, device_info):
    '''
    Handle functionality for when a USB drive is inserted
    '''
    oradio_utils.logging("info", f"USB drive inserted. device_id={device_id}, device_info={device_info}")

    # Check for wifi settings USB drive root. Use if exists
    check_usb_wifi_settings()

def usb_removed(device_id, device_info):
    '''
    Handle functionality for when a USB drive is removed
    '''
    oradio_utils.logging("info", f"USB drive removed. device_id={device_id}, device_info={device_info}")

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

    # Show menu with test options
    input_selection = ("Select a function, input the number.\n"
                       " 0-quit\n"
                       " 1-simulate Oradio power on\n"
                       " 2-test USB drive insert | remove\n"
                       " 3-stop USB monitoring\n"
                       "select: "
                       )

    # User command loop
    print("########## Testing USB ##########")
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
                usb_utils.usb_monitor_stop(usb_monitor)
                break
            case 1:
                check_usb_wifi_settings()
            case 2:
                usb_monitor = usb_utils.usb_monitor_start(usb_monitor, usb_inserted, usb_removed)
                oradio_utils.logging("info", "==> insert or remove a USB drive to test detection")
            case 3:
                usb_monitor = usb_utils.usb_monitor_stop(usb_monitor)
            case _:
                print("\nPlease input a valid number\n")

    '''
    TODO: put check in oradio_utils
    # If monitoring: Stop monitoring
    if system_monitoring:
        sys_monitor.stop()
    '''
