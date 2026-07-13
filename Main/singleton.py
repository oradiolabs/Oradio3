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
@summary:       Provides the singleton decorator.
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
    The single instance and its creation lock are held in the decorator's
    closure, keeping them out of the class namespace entirely. This avoids
    both pylint warnings and any risk of colliding with attributes defined
    by the decorated class itself.
    Patches __new__ and __init__ in place so that subclassing and
    isinstance() behaviour remain normal.
    """
    # Held in the closure - invisible and unreachable from outside.
    instance = None
    lock = Lock()

    # Saved so init_once can delegate to it after the first-run guard.
    original_init = cls.__init__

    @wraps(original_init)
    def init_once(self, *args, **kwargs):
        """Run the original __init__ exactly once per singleton instance."""
        # Read via __dict__ to avoid invoking a user-defined __getattr__.
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
        nonlocal instance
        # Fast path: instance already exists, no locking needed.
        if instance is None:
            with lock:
                # Slow path: re-check now that we hold the lock.
                if instance is None:
                    # Call object.__new__ directly to bypass our patched
                    # __new__ and avoid infinite recursion.
                    instance = object.__new__(subcls)
        return instance

    # Replace __new__ and __init__ on the class itself so that subclasses
    # and isinstance() checks continue to work normally.
    cls.__new__ = new_singleton
    cls.__init__ = init_once
    return cls

##### Stand-alone entry point #############################

if __name__ == '__main__':
    print("Running Singleton metaclass stand-alone is meaningless: do nothing")
