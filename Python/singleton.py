#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on November 4, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
"""
from threading import Lock
from functools import wraps

def singleton(cls) -> object:
    """Make a class a thread-safe singleton by patching its __new__ and __init__.

    This decorator installs:
     - a class-level _instance and _lock
     - a __new__ that ensures only one instance is created (double-checked locking)
     - an __init__ wrapper that runs the original __init__ only once

    Key safety detail: checks and sets the '_initialized' flag via __dict__
    to avoid triggering user-defined __getattr__ during initialization.

    The decorator returns the original class (not a wrapper), so subclassing and
    isinstance() behavior remain normal.
    """
    cls._instance = None  # Store the singleton instance (per class)
    cls._lock = Lock()    # Lock to make instance creation thread-safe

    # Save the original __init__ method
    original_init = getattr(cls, "__init__", lambda self, *a, **k: None)

    @wraps(original_init)
    def init_once(self, *args, **kwargs):
        """Replacement __init__ that runs only once per once."""
        # Use __dict__ to avoid invoking __getattr__
        if self.__dict__.get("_initialized", False):
            return  # Skip re-initialization

        # Call the original __init__
        original_init(self, *args, **kwargs)

        # Mark initialized via __dict__ to avoid __getattr__ recursion
        self.__dict__["_initialized"] = True

    @wraps(getattr(cls, "__new__", object.__new__))
    def new_singleton(subcls, *args, **kwargs):
        # Double-checked locking on class-level _instance
        if subcls._instance is None:
            with subcls._lock:
                if subcls._instance is None:
                    # Use super(subcls, subcls).__new__(subcls) to get raw instance
                    subcls._instance = super(cls, subcls).__new__(subcls)
        return subcls._instance

    # Patch class in-place
    cls.__new__ = new_singleton
    cls.__init__ = init_once
    return cls

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("Running Singleton metaclass stand-alone is meaningless: do nothing")
