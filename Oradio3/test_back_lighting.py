import time
import threading
from back_lighting import backlighting  # Assuming your code is saved in `backlighting.py`
import RPi.GPIO as GPIO


# Switch all leds to output high

LEDS = {
    "LEDPlay": 15,
    "LEDPreset1": 24,
    "LEDPreset2": 25,
    "LEDPreset3": 7,
    "LEDStop": 23
}

# Initialize GPIO
GPIO.setmode(GPIO.BCM)

# Configure LEDs as outputs
for name, pin in LEDS.items():
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)



def test_backlighting():
    lighting = backlighting()
    auto_adjust_thread = None

    def start_auto_adjust():
        """Start the auto_adjust function in a separate thread."""
        nonlocal auto_adjust_thread
        if auto_adjust_thread is None or not auto_adjust_thread.is_alive():
            print("Starting Auto Adjust...")
            auto_adjust_thread = threading.Thread(target=lighting.auto_adjust)
            auto_adjust_thread.daemon = True  # Allows the thread to exit when the main program exits
            auto_adjust_thread.start()
        else:
            print("Auto Adjust is already running.")

    def stop_auto_adjust():
        """Stop the auto_adjust function."""
        if lighting.running:
            print("Stopping Auto Adjust...")
            lighting.running = False
            if auto_adjust_thread:
                auto_adjust_thread.join()
        else:
            print("Auto Adjust is not running.")

    def test_mode():
        """Print raw_visible_light, calculated lux, and DAC value every 2 seconds."""
        print("Entering Test Mode... Press Ctrl+C to return to the main menu.")
        lighting.initialize_sensor()
        try:
            while True:
                raw_visible_light = lighting.read_visible_light()
                lux = lighting.calculate_lux(raw_visible_light)
                target_dac_value = lighting.interpolate_backlight(lux)
                print(f"Raw Visible Light: {raw_visible_light}, Lux: {lux:.2f}, DAC Value: {target_dac_value}")
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nExiting Test Mode...")

    while True:
        print("\nBacklight Control Menu:")
        print("1. Activate Auto Adjust")
        print("2. Stop Auto Adjust")
        print("3. Turn Off Backlight")
        print("4. Set Backlight to Maximum")
        print("5. Test Mode")
        print("6. Exit")
        choice = input("Enter your choice (1-6): ")

        if choice == "1":
            start_auto_adjust()
        elif choice == "2":
            stop_auto_adjust()
        elif choice == "3":
            print("Turning Off Backlight...")
            lighting.off()
        elif choice == "4":
            print("Setting Backlight to Maximum...")
            lighting.maximum()
        elif choice == "5":
            test_mode()
        elif choice == "6":
            print("Exiting program...")
            stop_auto_adjust()  # Ensure auto_adjust is stopped before exiting
            break
        else:
            print("Invalid choice. Please enter a number between 1 and 6.")
        time.sleep(1)

if __name__ == "__main__":
    test_backlighting()