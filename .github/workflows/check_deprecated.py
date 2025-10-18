#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on October 18, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Identify runtime deprecated functionality
"""
import sys
import warnings
import importlib

if len(sys.argv) < 2:
    print("Usage: python check_deprecated.py <requirements_file>", file=sys.stderr)
    sys.exit(1)

requirements_file = sys.argv[1]

modules_to_check = []
try:
    with open(requirements_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                module_name = line.split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0]
                modules_to_check.append(module_name)
except FileNotFoundError:
    print("requirements-lint.txt not found.", file=sys.stderr)
    sys.exit(1)

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter('always', DeprecationWarning)
    for mod_name in modules_to_check:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            print(f"Module {mod_name} not installed")
    for warning in w:
        print(f"⚠️ Deprecation warning: {warning.message}")
