#!/bin/bash
# run the moduletest as: ./run_test.sh python touch_buttons_test.py
# run pylint as: ./run_test.sh pylint touch_buttons_test.py
export PYTHONPATH="/home/pi/Oradio3/Python"
# run the program
exec "$@"
