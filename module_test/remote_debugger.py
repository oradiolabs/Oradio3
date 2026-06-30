#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on January 11, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2026, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Remote debugging helper for Oradio scripts

@info
    Remote Python debugging requires a running Python Debug Server in your IDE.
    Call any module with these arguments to connect:
        python your_program.py -rd yes -ip 192.168.xxx.xxx -p 5678
    Install the debugger package if needed:
        pip install pydevd
"""
import os
import socket
import argparse

##### Oradio modules ######################################
from constants import (
    YELLOW, GREEN, NC,
    DEBUGGER_CONNECTED,
    DEBUGGER_NOT_CONNECTED,
    DEBUGGER_DISABLED,
    DEBUGGER_ENABLED,
)

##### Remote debugger toggle ##############################
# Set to DEBUGGER_ENABLED locally when remote debugging is needed.
# Must be DEBUGGER_DISABLED in production/committed code.
#REMOTE_DEBUGGER = DEBUGGER_ENABLED
REMOTE_DEBUGGER = DEBUGGER_DISABLED


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the argument parser for remote-debug CLI options.

    Accepted arguments:
        -rd / --rmdebug    'yes' to enable remote debugging, 'no' or omitted to skip.
        -ip / --ipaddress  IP address of the host running the IDE debug server.
        -p  / --portnr     Port number the IDE debug server is listening on.
    """
    # pylint: disable=invalid-name
    # MESSAGE_DEBUG is intentionally upper-case; it is a module-level constant.
    MESSAGE_DEBUG = (
        "Remote debug options: -rd [no|yes] -ip [host-ip-address] -p [host-portnr]"
    )
    # pylint: enable=invalid-name

    parser = argparse.ArgumentParser(description="Remote Debug")
    parser.add_argument("-rd", "--rmdebug",    type=str, nargs="?", const="no", help=MESSAGE_DEBUG)
    parser.add_argument("-ip", "--ipaddress",  type=str, nargs="?", const="no", help=MESSAGE_DEBUG)
    parser.add_argument("-p",  "--portnr",     type=str, nargs="?", const="no", help=MESSAGE_DEBUG)
    return parser


if REMOTE_DEBUGGER == DEBUGGER_ENABLED:
    import pydevd  # noqa: E402  (conditional import — only when debugger is enabled)

    def setup_remote_debugging() -> tuple[str, str]:
        """
        Optionally connect to a remote IDE debug server (debugger enabled build).

        Parses CLI arguments and, when '-rd yes' is supplied, attempts to
        connect to the pydevd debug server at the given host and port.

        Returns:
            tuple[str, str]: (debugger_status, connection_status) where:
                - debugger_status:  DEBUGGER_ENABLED  — this build has the debugger active.
                - connection_status:
                    DEBUGGER_CONNECTED     — '-rd yes' was supplied and connection succeeded.
                    DEBUGGER_NOT_CONNECTED — '-rd yes' was supplied but connection failed,
                                             or '-rd no' / omitted (debugging not requested).
        """
        parser = _build_arg_parser()
        args = parser.parse_args()

        if args.rmdebug == "yes" and (not args.ipaddress or not args.portnr):
            raise argparse.ArgumentError(None, "Both -ip and -p are required when -rd is 'yes'")

        allowed_options = [None, "no", "yes"]
        if args.rmdebug not in allowed_options:
            parser.error(
                "Remote debug options: -rd [no|yes] -ip [host-ip-address] -p [host-portnr]"
            )

        print(f"Remote debug option = {args.rmdebug}")
        connection_status = DEBUGGER_NOT_CONNECTED

        if args.rmdebug == "yes":
            ip_address = args.ipaddress
            port_nr = int(args.portnr)
            print("Remote debugging started")
            # Suppress pydevd file-validation warnings (safe to disable in debug sessions)
            os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
            try:
                pydevd.settrace(ip_address, port=port_nr)
            except ConnectionRefusedError:
                print(f"{YELLOW}Failed to connect to debugger at {ip_address}:{port_nr}.")
                print(f"Is the IDE pydevd server running and listening?{NC}")
            except (socket.error, OSError) as err:
                print(f"{YELLOW}Network error while connecting to debugger: {err}{NC}")
            else:
                print(f"{GREEN}Oradio connected to debugger.{NC}")
                connection_status = DEBUGGER_CONNECTED
        else:
            print("Remote debugging not requested.")

        return DEBUGGER_ENABLED, connection_status

else:
    def setup_remote_debugging() -> tuple[str, str]:
        """
        No-op stub used when the debugger is disabled (production build).

        Still parses CLI arguments so callers do not see unexpected-argument
        errors when '-rd' flags are passed on the command line.

        Returns:
            tuple[str, str]: (debugger_status, connection_status) where:
                - debugger_status:  DEBUGGER_DISABLED  — debugging is compiled out.
                - connection_status: DEBUGGER_NOT_CONNECTED — no connection attempted.
        """
        parser = _build_arg_parser()
        args = parser.parse_args()

        if args.rmdebug == "yes":
            print(
                f"{YELLOW}Remote debugger is disabled in this build; "
                f"-rd yes, -ip, and -p arguments are ignored.{NC}"
            )

        return DEBUGGER_DISABLED, DEBUGGER_NOT_CONNECTED
