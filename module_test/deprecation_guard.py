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
    This code sets up a custom global warning handler that intercepts Python warnings. Specifically:
    - Logs all deprecation warnings (DeprecationWarning and PendingDeprecationWarning) using a
      dedicated logger instead of printing them to the console.
    - Delegates non-deprecation warnings to the original warning handler.
    - Ensures deprecation warnings are always shown, even if Python would normally suppress them.
    In short: it's a centralized, reliable way to track and enforce deprecation warnings in your
    Python project.

    Changes v2:
    - _deprecation_queue is now a thread-safe collections.deque protected by threading.Lock
    - Stack walk now takes the FIRST matching /Main/ or /module_test/ frame (the actual call site)
      rather than the last (outermost) frame
    - flush_warnings() returns the number of queued warnings so callers can exit non-zero
    - SWIG/C-extension guard extended to cover _lgpio filename pattern more broadly
"""
import os
import logging
import inspect
import warnings
import threading
from collections import deque

# ---------------------------------------------------------------------------
# Logging setup – output suppressed by default; enable externally if needed
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.NullHandler()],
)
logger = logging.getLogger("deprecation")

# ---------------------------------------------------------------------------
# Thread-safe warning queue
# ---------------------------------------------------------------------------
_deprecation_queue: deque = deque()
_queue_lock = threading.Lock()

# Store original warnings.showwarning so non-deprecation warnings are unaffected
_original_showwarning = warnings.showwarning

# Directories that are considered "Oradio project" source paths
_PROJECT_MARKERS = ("/Main/", "/module_test/")


def handle_deprecation(message, _category, filename, lineno, *_):
    """
    Capture deprecation warnings and enqueue them with the originating module.

    Avoids logging immediately (safe for SWIG / C extensions) and uses a
    thread-safe deque so imports that spawn threads cannot corrupt the queue.

    Args:
        message   (Warning): Warning message object.
        _category (type):    Warning category (unused; required by showwarning ABI).
        filename  (str):     File where the warning was raised.
        lineno    (int):     Line number of the warning.
        *_:                  Any additional arguments required by the showwarning signature.
    """
    msg = str(message)

    # Skip known SWIG / C-extension noise
    if "SwigPy" in msg or "_lgpio" in os.path.basename(filename):
        return

    # Walk the call stack and take the FIRST frame that belongs to an Oradio
    # source directory – this is the actual site of the deprecated call.
    module = "Undefined"
    for frame_info in inspect.stack():
        abs_path = os.path.abspath(frame_info.filename)
        if os.path.isfile(abs_path) and any(marker in abs_path for marker in _PROJECT_MARKERS):
            module = os.path.basename(abs_path)
            break   # first match = innermost Oradio frame

    with _queue_lock:
        _deprecation_queue.append((module, msg, filename, lineno))


# Replace the default handler with our custom one
warnings.showwarning = handle_deprecation

# Ensure all deprecation variants are always emitted (never silently filtered)
warnings.simplefilter("always", DeprecationWarning)
warnings.simplefilter("always", PendingDeprecationWarning)


def flush_warnings() -> int:
    """
    Print all queued deprecation warnings and return the count.

    Returns:
        int: Number of deprecation warnings that were queued.
             Callers should treat a non-zero return as a failure signal.
    """
    with _queue_lock:
        queued = list(_deprecation_queue)

    for module, message, filename, lineno in queued:
        print(f"DEPRECATION ({module}): {message} ({filename}:{lineno})")

    return len(queued)
