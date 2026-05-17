#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on May 15, 2026
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Placeholder for testing error service
"""
from threading import Thread
from multiprocessing import Process

##### Oradio modules ####################
from oj_utils import put_error_message, ErrorMessage, put_command_message, CommandMessage

def dangerous():
    raise Exception("boom")

def worker(type="main"):
    put_command_message(CommandMessage("worker", f"[MODULE] worker in {type} context started"))
    try:
        dangerous()
    except Exception:
        put_error_message(ErrorMessage(f"worker", f"[MODULE] worker in {type} context failed"))

def start_thread():
    Thread(target=worker, args=("thread",), daemon=True).start()

def start_process():
    p = Process(target=worker, args=("process",))
    p.start()
    return p
