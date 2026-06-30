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
import os
import sys
import glob
import warnings
import importlib
import traceback

# Activate deprecation guard FIRST
import deprecation_guard

# ----------------------- #
# Import project code     #
# ----------------------- #

# Filter sys.path to include only the Oradio source files
project_dirs = [d for d in sys.path if "/Main" in d]

# Initialize an empty list to store the paths of all found Python files
python_files = []

# Iterate over each project directory
for directory in project_dirs:
    if os.path.isdir(directory):
        # Use glob to find all .py files in the current directory and its subdirectories
        py_files = glob.glob(os.path.join(directory, '**', '*.py'), recursive=True)
        # Add the found files to the python_files list
        python_files.extend(py_files)

# Initialize an empty list to store all found Python modules
python_modules = []

for file in python_files:
    filename = os.path.basename(file)
    # Skip hidden files and __init__.py
    if filename.startswith('_'):
        continue
    module = os.path.splitext(filename)[0]
    # Avoid duplicates
    if module not in python_modules:
        python_modules.append(module)

# Check for deprecations
for module in python_modules:
    try:
        _ = importlib.import_module(module)
    except ImportError as err_msg:
        print(f"Import error ({module}): {err_msg}")
    except RuntimeError as err_msg:
        print(f"Runtime error ({module}): {err_msg}")
    except OSError as err_msg:
        print(f"Missing external command/file while importing ({module}): {err_msg}")
    # We want to catch all exceptions to check for deprecation
    except Exception as err_msg:    # pylint: disable=broad-exception-caught
        print(f"Unexpected error ({module}): {err_msg}\n{traceback.format_exc()}")

# Fail on Python deprecations
warnings.simplefilter("error", DeprecationWarning)
warnings.simplefilter("error", PendingDeprecationWarning)

# Flush warnings after all C modules loaded
deprecation_guard.flush_warnings()
