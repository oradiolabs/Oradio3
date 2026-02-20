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
    This code sets up a custom global warning handler that intercepts Python warnings. Specifically:
    - Logs all deprecation warnings (DeprecationWarning and PendingDeprecationWarning) using a dedicated logger instead of printing them to the console.
    - Delegates non-deprecation warnings to the original warning handler.
    - Ensures deprecation warnings are always shown, even if Python would normally suppress them.
    In short: it’s a centralized, reliable way to track and enforce deprecation warnings in your Python project.
"""
import os
import logging
import inspect
import warnings

# Logging setup, disabling output
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.NullHandler()]
)
logger = logging.getLogger("deprecation")

# Safe queue-based warning handler
_deprecation_queue = []

# Store original warnings.showwarning function
_original_showwarning = warnings.showwarning

def handle_deprecation(message, _category, filename, lineno, *_):
    """
    Capture deprecation warnings and build a hierarchical file stack/tree.

    This handler avoids logging immediately (safe for SWIG/C extensions)
    and queues all warnings with their full call stack.

    Args:
        message (Warning): Warning message object
        _category (type[Warning]): Unused, but required by the warnings.showwarning signature
        filename (str): Filename where warning was raised
        lineno (int): Line number of the warning
        *_: Unused extra arguments required by the warnings.showwarning signature
    """
    msg = str(message)

    # Skip known SWIG/C extension warnings
    if "SwigPy" in msg or "_lgpio" in filename:
        return

    # Initialize the module name
    module = 'Undefined'

    # Get the Oradio module containing the deprecation
    for frame_info in inspect.stack():
        abs_path = os.path.abspath(frame_info.filename)
        # Only include files relevant to Oradio3 functionality
        if os.path.isfile(abs_path) and '/Main/' in abs_path:
            module = os.path.basename(abs_path)

    # Append the warning to the queue
    _deprecation_queue.append((module, message, filename, lineno))

# Replace the default warning handler with the custom handler
warnings.showwarning = handle_deprecation

# Ensure all deprecation warnings are always emitted
warnings.simplefilter("always", DeprecationWarning)
warnings.simplefilter("always", PendingDeprecationWarning)

def flush_warnings():
    """Print queued warnings."""
    for module, message, filename, lineno in _deprecation_queue:
        print(f"DEPRECATION ({module}): {message} ({filename}:{lineno})")
