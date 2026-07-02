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
@summary:       CI-safe deprecation checker for Oradio3 (single-file version)
    - Imports modules based on file paths (NOT module names)
    - Works without relying on PYTHONPATH or package structure
    - Supports CLI arguments (specific files OR full scan)
    - Collects import errors separately
    - Fails CI when deprecations are found

    This file combines the former `deprecation_guard.py` module and the
    checker script into a single standalone file, so no separate import
    of `deprecation_guard` is required.
"""
import os
import sys
import inspect
import warnings
import argparse
import traceback
import threading
import importlib.util
from pathlib import Path
from types import ModuleType
from collections import deque

# ===========================================================================
# DEPRECATION GUARD (formerly deprecation_guard.py)
# ===========================================================================
#
# Changes v2:
# - _deprecation_queue is now a thread-safe collections.deque protected by
#   threading.Lock
# - Stack walk now takes the FIRST matching /Main/ or /module_test/ frame
#   (the actual call site) rather than the last (outermost) frame
# - flush_warnings() returns the number of queued warnings so callers can
#   exit non-zero
# - SWIG/C-extension guard extended to cover _lgpio filename pattern more
#   broadly

# ---------------------------------------------------------------------------
# Thread-safe warning queue
# ---------------------------------------------------------------------------
_deprecation_queue: deque = deque()
_queue_lock = threading.Lock()

# Keep a reference to the default handler so non-deprecation warnings can
# still be shown normally instead of being silently absorbed.
_original_showwarning = warnings.showwarning

# Directories that are considered "Oradio project" source paths
_PROJECT_MARKERS = ("/Main/", "/module_test/")

# This checker's own resolved file path. Used to (a) skip its own frame
# when walking the stack to attribute a warning to the real caller, and
# (b) exclude itself from the file scan in main() — since this script may
# itself live inside one of the scanned directories (e.g. Main/).
_SELF_PATH = Path(__file__).resolve()

# Warning categories this guard is responsible for capturing.
_DEPRECATION_CATEGORIES = (DeprecationWarning, PendingDeprecationWarning)


def handle_deprecation(message, category, filename, lineno, *args):
    """
    Capture deprecation warnings and enqueue them with the originating module.

    Only DeprecationWarning / PendingDeprecationWarning are captured here.
    Any other warning category is passed through to the original
    `warnings.showwarning` handler unchanged, so this guard does not
    suppress or hide unrelated warnings (e.g. RuntimeWarning, UserWarning).

    Avoids logging immediately (safe for SWIG / C extensions) and uses a
    thread-safe deque so imports that spawn threads cannot corrupt the queue.

    Args:
        message  (Warning): Warning message object.
        category (type):    Warning category, used to decide whether to
                             capture the warning or delegate it.
        filename (str):     File where the warning was raised.
        lineno   (int):     Line number of the warning.
        *args:               Any additional arguments required by the
                             showwarning signature (passed through as-is
                             when delegating).
    """
    # Not a deprecation warning: let the default handler deal with it so
    # its normal console output is preserved.
    if not issubclass(category, _DEPRECATION_CATEGORIES):
        _original_showwarning(message, category, filename, lineno, *args)
        return

    msg = str(message)

    # Skip known SWIG / C-extension noise
    if "SwigPy" in msg or "_lgpio" in os.path.basename(filename):
        return

    # Walk the call stack and take the FIRST frame that belongs to an Oradio
    # source directory – this is the actual site of the deprecated call.
    # This checker's own frame (e.g. import_from_file, main) is skipped so
    # it never misattributes a warning to itself when it happens to reside
    # inside one of the scanned directories.
    module = "Undefined"
    for frame_info in inspect.stack():
        abs_path = os.path.abspath(frame_info.filename)
        if abs_path == str(_SELF_PATH):
            continue
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


# ===========================================================================
# CHECKER (formerly deprecation_check.py, the CLI script)
# ===========================================================================

# ---------------------------------------------------------------------------
# FILE-BASED IMPORT HELPER
# ---------------------------------------------------------------------------
def import_from_file(file_path: Path) -> ModuleType:
    """
    Import a Python file directly by its filesystem path, bypassing the
    normal package/module resolution machinery.

    This is used instead of a regular `import` statement because the
    project files being scanned are not guaranteed to sit on PYTHONPATH
    or to form a proper package hierarchy. Loading by path lets this
    checker import any .py file found under the scan directories without
    needing project-specific package setup.

    The generated module name is derived from the resolved absolute path
    so it is both deterministic (same file -> same name) and collision-safe
    (different files -> different names), then registered in `sys.modules`
    before execution so relative imports and reload logic inside the
    scanned file behave the same as a normal import would.

    Args:
        file_path (Path): Path to the .py file to import.

    Returns:
        module: The imported module object.

    Raises:
        ImportError: If no module spec/loader could be created for the file.
        Exception: Any exception raised while executing the module body
            (e.g. ImportError, RuntimeError, OSError) propagates to the
            caller so it can be handled per-file.
    """
    file_path = file_path.resolve()

    # Deterministic + collision-safe module name derived from the full path.
    module_name = "oradio_" + str(file_path).replace("/", "_").replace(".", "_")

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for {file_path}")

    module = importlib.util.module_from_spec(spec)

    # Register in sys.modules before exec so the module behaves like a
    # normally-imported one (e.g. for relative imports it may perform).
    # Note: if exec_module() raises, this partially-initialized module
    # stays registered under module_name. That's acceptable here since
    # this is a one-shot CI script — each file is only scanned once per run.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    return module


def main():
    """
    Entry point for the deprecation checker.

    Scans either the given target files or the default project
    directories, imports each .py file by path to trigger any
    DeprecationWarning / PendingDeprecationWarning it emits at import
    time, then reports the results.

    Exit code:
        0 if no deprecation warnings were captured (and prints any
          import/runtime errors encountered, without failing the build
          on those alone).
        1 if one or more deprecation warnings were captured — intended
          to fail CI.
    """
    # -----------------------------------------------------------------
    # CLI ARGUMENTS
    # -----------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Oradio3 deprecation checker"
    )

    parser.add_argument(
        "targets",
        nargs="*",
        help="Optional list of .py files to scan (default: full project)"
    )

    args = parser.parse_args()

    # -----------------------------------------------------------------
    # COLLECT FILES
    # -----------------------------------------------------------------
    base_dirs = [
        Path.home() / "Oradio3" / "Main",
        Path.home() / "Oradio3" / "module_test",
    ]

    if args.targets:
        python_files = [Path(t) for t in args.targets]
    else:
        python_files = []
        for base in base_dirs:
            if base.exists():
                python_files.extend(base.rglob("*.py"))

    # -----------------------------------------------------------------
    # FILTER FILES
    # -----------------------------------------------------------------
    # Never scan/import this checker script itself: importing it by path
    # would re-run this module's top-level side effects (installing the
    # warnings handler again, etc.) and it isn't part of the codebase
    # being checked.
    python_files = [
        f for f in python_files
        if f.exists()
        and f.is_file()
        and not f.name.startswith("_")
        and f.resolve() != _SELF_PATH
    ]

    # -----------------------------------------------------------------
    # IMPORT MODULES (TRIGGER DEPRECATIONS)
    # -----------------------------------------------------------------
    import_errors = []

    for file in python_files:
        print(f"checking: {file}")
        try:
            import_from_file(file)
        except ImportError as ex_err:
            import_errors.append(f"Import error      ({file}): {ex_err}")
        except RuntimeError as ex_err:
            import_errors.append(f"Runtime error     ({file}): {ex_err}")
        except OSError as ex_err:
            import_errors.append(f"OS error          ({file}): {ex_err}")
        # The broad exception is intentional to catch ANY unknown exception
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            import_errors.append(
                f"Unexpected error  ({file}): {ex_err}\n{traceback.format_exc()}"
            )

    # -----------------------------------------------------------------
    # FLUSH DEPRECATION WARNINGS
    # -----------------------------------------------------------------
    count = flush_warnings()

    # -----------------------------------------------------------------
    # REPORT IMPORT ERRORS (AFTER WARNINGS FOR CLEAN CI OUTPUT)
    # -----------------------------------------------------------------
    if import_errors:
        print("\n--- Import / runtime errors during scan ---")
        for err in import_errors:
            print(err)

    # -----------------------------------------------------------------
    # EXIT CODE (CI FAILURE IF DEPRECATIONS FOUND)
    # -----------------------------------------------------------------
    sys.exit(1 if count > 0 else 0)


if __name__ == "__main__":
    main()
