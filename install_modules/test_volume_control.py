
# main.py
import time
from volume_control import start_monitoring

class StateMachine:
    def __init__(self):
        self.state = "StateStop"

    def transition(self, new_state):
        print(f"Transitioning from {self.state} to {new_state}")
        self.state = new_state

if __name__ == "__main__":
    try:
        # Initialize state machine
        state_machine = StateMachine()
        # Start the volume monitoring thread
        start_monitoring(state_machine)
        print("Volume monitoring started.")
        
        # Keep the main program running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting program.")
