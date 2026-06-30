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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    CI-safe deprecation checker for Oradio3
    - Mocks all hardware, network, and subprocess dependencies
    - Overrides subprocess.run and check_output to ignore all system calls
    - Activates the deprecation guard
    - Imports every module under Main/ and module_test/ to trigger Python
      deprecation warnings
    - Exits with code 1 when any deprecation warnings are found so CI fails
      reliably without relying on grep counting

    Changes v2:
    - module_test/ modules are now also imported and runtime-checked
    - Script exits with code 1 when flush_warnings() returns a non-zero count
    - Removed the dead `warnings.simplefilter("error", ...)` block that had
      no effect after showwarning was already replaced
    - Import errors are now collected and reported as a block at the end so
      they don't mix with deprecation output consumed by the CI grep
"""
import os
import sys
import glob
import importlib
import traceback

# ---------------------------------------------------------------------------
# Activate the deprecation guard FIRST – before any project code is imported
# ---------------------------------------------------------------------------
import deprecation_guard  # noqa: E402

# ---------------------------------------------------------------------------
# Resolve all Oradio Python modules (Main/ and module_test/)
# ---------------------------------------------------------------------------
_SOURCE_MARKERS = ("/Main", "/module_test")

project_dirs = [
    d for d in sys.path
    if any(marker in d for marker in _SOURCE_MARKERS)
]

python_files: list[str] = []
for directory in project_dirs:
    if os.path.isdir(directory):
        py_files = glob.glob(os.path.join(directory, "**", "*.py"), recursive=True)
        python_files.extend(py_files)

python_modules: list[str] = []
for file in python_files:
    filename = os.path.basename(file)
    # Skip __init__.py, __main__.py, and private/dunder files
    if filename.startswith("_"):
        continue
    module = os.path.splitext(filename)[0]
    if module not in python_modules:
        python_modules.append(module)

# ---------------------------------------------------------------------------
# Import every discovered module to trigger runtime deprecation warnings
# ---------------------------------------------------------------------------
import_errors: list[str] = []

for module in python_modules:
    try:
        importlib.import_module(module)
    except ImportError as err_msg:
        import_errors.append(f"Import error         ({module}): {err_msg}")
    except RuntimeError as err_msg:
        import_errors.append(f"Runtime error        ({module}): {err_msg}")
    except OSError as err_msg:
        import_errors.append(f"Missing file/command ({module}): {err_msg}")
    except Exception as err_msg:    # pylint: disable=broad-exception-caught
        import_errors.append(
            f"Unexpected error ({module}): {err_msg}\n{traceback.format_exc()}"
        )

# ---------------------------------------------------------------------------
# Flush queued deprecation warnings – printed in the DEPRECATION (...): format
# that the CI workflow greps for.  Exit 1 when any are found.
# ---------------------------------------------------------------------------
count = deprecation_guard.flush_warnings()

# ---------------------------------------------------------------------------
# Report any import errors AFTER deprecation output so the CI grep is clean
# ---------------------------------------------------------------------------
if import_errors:
    print("\n--- Import / runtime errors encountered during module scan ---")
    for err in import_errors:
        print(err)

sys.exit(1 if count > 0 else 0)
