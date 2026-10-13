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
    Applying @singleton to a class ensures, thread-safely, that only one
    instance is ever created and that its __init__ runs exactly once,
    regardless of how many times, or from how many threads, the class is
    instantiated. A thread that arrives while the first __init__ is still
    running waits for it, so it never sees a half-built instance. The
    decorator patches __new__ and __init__ in place, so subclassing and
    isinstance() checks continue to work normally.
"""
from threading import Lock, RLock
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

    # Serialises the first __init__, one per decorated class. Per class, not
    # global, so A.__init__ constructing singleton B cannot deadlock against
    # another thread constructing B. An RLock because __init__ runs while it
    # is held: a re-entrant construction of the same class on the same thread
    # must not deadlock on itself (it then finds __init__ in progress and
    # simply returns the instance, see init_once).
    init_lock = RLock()

    # Saved so init_once can delegate to it after the first-run guard.
    original_init = cls.__init__

    @wraps(original_init)
    def init_once(self, *args, **kwargs):
        """
        Run the original __init__ exactly once per singleton instance.

        The check and the call happen under init_lock, so two threads doing
        the first construction at the same time cannot both run __init__, and
        the second one returns only once the instance is fully built. If
        __init__ raises, _initialized stays unset and the next construction
        tries again.
        """
        # Fast path, read via __dict__ to avoid invoking a user-defined
        # __getattr__. Set only after __init__ completed, so True is final.
        if self.__dict__.get("_initialized", False):
            return
        with init_lock:
            # Re-check now that we hold the lock: another thread may have
            # finished __init__ while this one waited.
            if self.__dict__.get("_initialized", False):
                return
            # Re-entered from inside our own __init__ on this thread: the
            # instance is being built right now, so running __init__ a second
            # time would only start it over halfway through.
            if self.__dict__.get("_initializing", False):
                return
            self.__dict__["_initializing"] = True
            try:
                original_init(self, *args, **kwargs)
                # Write via __dict__ for the same reason as the read above.
                self.__dict__["_initialized"] = True
            finally:
                self.__dict__["_initializing"] = False

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
    print("Running singleton decorator stand-alone is meaningless: do nothing")
