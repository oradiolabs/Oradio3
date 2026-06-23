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
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides the singleton decorator.
 
    Applying @singleton to a class makes it thread-safe such that only one
    instance is ever created, regardless of how many times the class is
    instantiated. The decorator patches __new__ and __init__ in place, so
    subclassing and isinstance() checks continue to work normally.
"""
from threading import Lock
from functools import wraps

def singleton(cls) -> object:
    """
    Decorator that turns a class into a thread-safe singleton.
 
    Installs a class-level instance and lock, then patches __new__ and
    __init__ so that only one instance is ever created and initialised.
 
    The '_initialized' flag is read and set via __dict__ directly to avoid
    triggering any user-defined __getattr__ during initialisation.
 
    The decorator returns the original class (not a wrapper function), so
    subclassing and isinstance() behaviour remain normal.
    """
    # Holds the single shared instance.
    cls._singleton_instance = None

    # Protects instance creation against concurrent first-call races.
    cls._singleton_lock = Lock()

    # Saved so init_once can delegate to it after the first-run guard.
    original_init = cls.__init__

    @wraps(original_init)
    def init_once(self, *args, **kwargs):
        """Run the original __init__ exactly once per singleton instance."""
        # Read via __dict__ to avoid invoking a user-defined __getattr__
        if self.__dict__.get("_initialized", False):
            return
        original_init(self, *args, **kwargs)
        # Write via __dict__ for the same reason.
        self.__dict__["_initialized"] = True

    def new_singleton(subcls, *_, **__):
        """
        Return the singleton instance, creating it if necessary.
 
        Uses double-checked locking: the fast path skips the lock once the
        instance exists; the slow path re-checks inside the lock to guard
        against a race between two simultaneous first calls.
        """
        # Fast path: instance already exists, no locking needed.
        if cls._singleton_instance is None:
            with cls._singleton_lock:
                # Slow path: re-check now that we hold the lock.
                if cls._singleton_instance is None:
                    # Call object.__new__ directly to bypass our patched
                    # __new__ and avoid infinite recursion.
                    cls._singleton_instance = object.__new__(subcls)
        return cls._singleton_instance

    # Replace __new__ and __init__ on the class itself so that subclasses
    # and isinstance() checks continue to work normally.
    cls.__new__ = new_singleton
    cls.__init__ = init_once

    return cls

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("Running Singleton metaclass stand-alone is meaningless: do nothing")
