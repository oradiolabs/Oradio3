#!/bin/bash

# Script to extract the current librespot device name

systemctl show librespot | grep ^Environment= | tr ' ' '\n' | grep LIBRESPOT_NAME | cut -d= -f2-
