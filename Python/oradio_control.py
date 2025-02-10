# Test the available logging levels
from oradio_logging import oradio_log
oradio_log.debug("Debug message in white")
oradio_log.info("Info message in white")
oradio_log.warning("Warning message in yellow")
oradio_log.error("Error message in red")

# Allow time for logging to do its thing
from time import sleep
sleep(3)