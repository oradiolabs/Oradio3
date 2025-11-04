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
    """
    Thread-safe singleton decorator for any class.

    Ensures that a class only ever has **one instance**. and is thread-safe using a lock.
    @wrap() ensures that the wrapper looks and behaves like the original function or method.
    Guarantees that the class's __init__ method runs **only once**, even if the class is instantiated multiple times.

    MOTE: Single underscore indicates internal use; accessing it here is intentional for the decorator.

    Args:
        cls: The class to decorate.

    Returns:
        object: The singleton instance of the decorated class.

    Usage:
        @singleton
        class MyClass:
            pass
    """
    # pylint: disable=protected-access
    cls._instance = None  # Store the singleton instance (per class)
    cls._lock = Lock()    # Lock to make instance creation thread-safe
    # pylint: enable=protected-access

    # Save the original __init__ method
    original_init = cls.__init__

    @wraps(cls.__init__)
    def init_once(self, *args, **kwargs):
        """
        Replacement __init__ that runs only once per once.
        """
        # pylint: disable=protected-access
        # Check if the instance has already been initialized
        if getattr(self, "_initialized", False):
            return  # Skip re-initialization
        # Call the original __init__
        original_init(self, *args, **kwargs)
        # Mark as initialized
        self._initialized = True
        # pylint: enable=protected-access

    # Replace the class's __init__ with our wrapper
    cls.__init__ = init_once

    @wraps(cls)
    def wrapper(*args, **kwargs):
        """
        Wrapper function that controls instance creation.
        Implements double-checked locking for thread safety.
        """
        # pylint: disable=protected-access
        if cls._instance is None:
            # Acquire the lock before creating the instance
            with cls._lock:
                if cls._instance is None:   # Double-check inside the lock
                    cls._instance = cls(*args, **kwargs)
        return cls._instance
        # pylint: enable=protected-access

    return wrapper

# Entry point for stand-alone operation
if __name__ == '__main__':
    print("Running Singleton metaclass stand-alone is meaningless: do nothing")
