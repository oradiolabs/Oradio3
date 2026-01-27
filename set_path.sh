#!/usr/bin/bash
ORADIO3_PYTHON="$HOME/Oradio3/Python:"
ORADIO3_PYTHON_MODULE_TEST="$HOME/Oradio3/Python/module_test:"

# Check and append directories if they are not already in PYTHONPATH
if [[ ":$PYTHONPATH:" != *:$ORADIO3_PYTHON* ]]; then
    PYTHONPATH="$ORADIO3_PYTHON$PYTHONPATH"
fi
if [[ ":$PYTHONPATH:" != *:$ORADIO3_PYTHON_MODULE_TEST* ]]; then
    PYTHONPATH="$ORADIO3_PYTHON_MODULE_TEST$PYTHONPATH"

export PYTHONPATH

