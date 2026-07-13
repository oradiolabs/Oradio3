#!/usr/bin/env python3
"""
  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on July 06, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Shared boilerplate for Oradio module-test (*_test.py) scripts, factored
    out into module_test_harness.py because every module test was
    repeating it:
        * KeyPressStopWaiter: background helper that waits for a keypress
          to signal a test loop to stop.
        * DebugMessageHandlers: subscribes a DebugMessageHandler to one or
          more message topics (Commands, Incidents) for the duration of a
          test, and unsubscribes/stops them again afterwards.
        * setup_debugger_or_exit: remote-debugger setup that exits the
          process if debugging is enabled but not connected.
        * module_test_session: combines the above with the "Starting/
          Exiting test program" banners every module test prints, so a
          test's __main__ block reduces to one `with` statement.
"""
import sys
from contextlib import contextmanager

##### Oradio modules ######################################
from utilities import ThreadTemplate
from remote_debugger import setup_remote_debugging
from messaging import DebugMessageHandler

##### GLOBAL constants ####################################
from constants import (
    RED, NC,
    DEBUGGER_ENABLED,
    DEBUGGER_NOT_CONNECTED,
)

class KeyPressStopWaiter(ThreadTemplate):
    """
    Background helper that blocks on a single keypress and then signals
    its own ThreadTemplate loop to stop.

    Interactive module-test menus commonly need a "press Return to stop"
    thread running alongside a polling/measuring loop in the main thread.
    Rather than every test file rolling its own Thread + Event for this,
    they can share this class and poll the `stopping` property:

        waiter = KeyPressStopWaiter()
        waiter.safe_start()
        while not waiter.stopping:
            ...do polling/measuring...
        waiter.safe_stop()

    do_work() is intentionally single-shot: after the first keypress it
    sets _stop_event directly (rather than waiting for an external
    safe_stop() call), since one keypress is all this is designed to
    wait for.
    """

    def __init__(self, prompt: str = "Press Return on keyboard to stop this test") -> None:
        """
        Args:
            prompt: Text shown to the user while waiting for the keypress.
        """
        super().__init__(interval=0.0, name="KeyPressStopWaiter")
        self._prompt = prompt

    def do_work(self) -> None:
        """Block on a single keypress, then signal our own loop to stop."""
        _ = input(self._prompt)
        self._stop_event.set()

def setup_debugger_or_exit() -> None:
    """
    Set up remote debugging per remote_debugger.py's configuration.

    If remote debugging is enabled but did not connect, prints an error
    and terminates the process -- there is no point continuing a module
    test that expects a debugger session that isn't there.
    """
    debugger_status, connection_status = setup_remote_debugging()
    if debugger_status == DEBUGGER_ENABLED and connection_status == DEBUGGER_NOT_CONNECTED:
        print(f"{RED}A remote debugging error, check the remote IP connection {NC}")
        sys.exit()

class DebugMessageHandlers:
    """
    Context manager that subscribes a DebugMessageHandler to each given
    message topic (e.g. Commands, Incidents) so published messages are
    echoed to the console for the duration of a module test, and
    unsubscribes + stops every handler again on exit.

    Usage:
        with DebugMessageHandlers(Commands, Incidents) as handlers:
            _start_module_test(handlers.get_queue(Commands))
    """
    def __init__(self, *topics) -> None:
        """
        Args:
            *topics: One or more message-bus topics, each exposing
                subscribe() and unsubscribe(queue), e.g. Commands, Incidents.
        """
        self._topics = topics
        self._handlers: dict = {}

    def __enter__(self) -> "DebugMessageHandlers":
        for topic in self._topics:
            self._handlers[topic] = DebugMessageHandler(topic.subscribe())
        return self

    def get_queue(self, topic):
        """
        Return the queue for a previously-subscribed topic.

        Args:
            topic: One of the topics passed to __init__.

        Returns:
            The Queue instance the topic is publishing into.
        """
        return self._handlers[topic].get_queue()

    def __exit__(self, exc_type, exc, tb) -> None:
        for topic, handler in self._handlers.items():
            topic.unsubscribe(handler.get_queue())
            handler.stop()

@contextmanager
def module_test_session(*topics):
    """
    Standard wrapper for a module test's __main__ block.

    Prints the start/exit banners, sets up (or exits on failure of) remote
    debugging, and subscribes/unsubscribes a DebugMessageHandler per topic
    -- the sequence every *_test.py's __main__ block was repeating.

    Args:
        *topics: Message-bus topics to subscribe debug handlers to, e.g.
            Commands, Incidents. Pass none if a test doesn't publish/listen
            on any topic.

    Yields:
        DebugMessageHandlers: use handlers.get_queue(topic) to retrieve a
            topic's queue, e.g. to hand to a test's command loop.

    Usage:
        if __name__ == '__main__':
            with module_test_session(Incidents):
                _start_module_test()

        if __name__ == '__main__':
            with module_test_session(Commands, Incidents) as handlers:
                _start_module_test(handlers.get_queue(Commands))
    """
    print("\nStarting test program...\n")
    setup_debugger_or_exit()
    with DebugMessageHandlers(*topics) as handlers:
        yield handlers
    print("\nExiting test program...\n")
