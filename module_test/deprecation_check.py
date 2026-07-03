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
@summary:       CI-safe deprecation checker
    - Imports modules based on file paths (NOT module names)
    - Works without relying on PYTHONPATH or package structure
    - Requires explicit CLI arguments: one or more .py files and/or
      directories to scan (no hardcoded default project directories)
    - Collects import errors separately
    - Fails CI when deprecations are found
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
# DEPRECATION GUARD
# ===========================================================================
#
# _deprecation_queue is a thread-safe collections.deque protected by threading.Lock
# Stack walk now takes the FIRST frame belonging to the scanned target paths
# (the actual call site) rather than the last (outermost) frame. The set of
# scanned paths is supplied at runtime via register_scan_roots() instead of
# being hardcoded to specific project directory names.
# flush_warnings() returns the number of queued warnings so callers can exit non-zero
# SWIG/C-extension guard extended to cover _lgpio filename pattern more broadly

# ---------------------------------------------------------------------------
# Thread-safe warning queue
# ---------------------------------------------------------------------------
_deprecation_queue: deque = deque()
_queue_lock = threading.Lock()

# Keep a reference to the default handler so non-deprecation warnings can
# still be shown normally instead of being silently absorbed.
_original_showwarning = warnings.showwarning

# Resolved directories of the files/dirs being scanned, used to attribute a
# captured warning to the actual project file that triggered it. Populated
# at runtime from the CLI targets (see register_scan_roots() / main())
# instead of being hardcoded to specific project directory names.
_scan_roots: set = set()
_scan_roots_lock = threading.Lock()

def register_scan_roots(paths) -> None:
    """
    Record the resolved parent directories of the files/directories being
    scanned, so handle_deprecation() can recognise stack frames that belong
    to the scanned project without any hardcoded directory names.

    Args:
        paths (Iterable[str | Path]): CLI targets (files or directories)
            as passed by the caller.
    """
    with _scan_roots_lock:
        for raw in paths:
            resolved = Path(raw).resolve()
            _scan_roots.add(str(resolved if resolved.is_dir() else resolved.parent))

def _is_scanned_path(abs_path: str) -> bool:
    """Return True if abs_path lives under one of the registered scan roots."""
    with _scan_roots_lock:
        roots = tuple(_scan_roots)
    return any(
        abs_path == root or abs_path.startswith(root.rstrip(os.sep) + os.sep)
        for root in roots
    )

# This checker's own resolved file path. Used to (a) skip its own frame
# when walking the stack to attribute a warning to the real caller, and
# (b) exclude itself from the file scan in main() — since this script may
# itself live inside one of the scanned directories.
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

    # Walk the call stack and take the FIRST frame that belongs to one of
    # the scanned target paths – this is the actual site of the deprecated
    # call.
    module = "Undefined"
    for frame_info in inspect.stack():
        abs_path = os.path.abspath(frame_info.filename)
        if abs_path == str(_SELF_PATH):
            continue
        if os.path.isfile(abs_path) and _is_scanned_path(abs_path):
            module = os.path.basename(abs_path)
            break   # first match = innermost scanned frame

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
# CHECKER
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

    Scans the given target files and/or directories (directories are
    searched recursively for *.py files), imports each .py file by path
    to trigger any DeprecationWarning / PendingDeprecationWarning it
    emits at import time, then reports the results.

    No targets are assumed by default — at least one file or directory
    must be given on the command line, otherwise a usage message is
    printed and the script exits.

    Exit code:
        0 if no deprecation warnings were captured (and prints any
          import/runtime errors encountered, without failing the build
          on those alone).
        1 if one or more deprecation warnings were captured — intended
          to fail CI.
        2 if no targets were given on the command line.
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
        help=(
            "One or more .py files and/or directories to scan. "
            "Directories are searched recursively for *.py files. "
            "Required — there is no default project location."
        )
    )

    args = parser.parse_args()

    if not args.targets:
        parser.print_usage(sys.stderr)
        print(
            f"{parser.prog}: error: no targets given — pass one or more "
            ".py files and/or directories to scan",
            file=sys.stderr,
        )
        sys.exit(2)

    # -----------------------------------------------------------------
    # COLLECT FILES
    # -----------------------------------------------------------------
    python_files = []
    for target in args.targets:
        target_path = Path(target)
        if target_path.is_dir():
            python_files.extend(target_path.rglob("*.py"))
        else:
            python_files.append(target_path)

    # Let handle_deprecation() recognise stack frames belonging to the
    # scanned targets, without any hardcoded directory names.
    register_scan_roots(args.targets)

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
