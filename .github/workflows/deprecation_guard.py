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
import warnings
import logging

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

def handle_deprecation(message, category, filename, lineno, file=None, line=None):
    """
    Custom global warning handler for logging deprecations.

    Queue all deprecation warnings safely.
    SWIG/C extensions are safe because we don't log immediately.
    
    Args:
        message (Warning): The warning message object.
        category (Type[Warning]): The warning category class.
        filename (str): Name of the file where the warning occurred.
        lineno (int): Line number in the file where the warning occurred.
        file (Optional[object], optional): File object to write the warning to. Defaults to None.
        line (Optional[str], optional): Source code line where the warning was triggered. Defaults to None.
    """
    msg = str(message)
    # Skip known SWIG/C extension warnings
    if "SwigPy" in msg or "_lgpio" in filename:
        return
    _deprecation_queue.append((message, category, filename, lineno))

# Replace the default warning handler with the custom handler
warnings.showwarning = handle_deprecation

# Ensure all deprecation warnings are always emitted
warnings.simplefilter("always", DeprecationWarning)
warnings.simplefilter("always", PendingDeprecationWarning)

# Function to flush queued warnings
def flush_warnings():
    for message, category, filename, lineno in _deprecation_queue:
        print(f"DEPRECATION: {message} ({filename}:{lineno})")
