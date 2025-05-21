#!/bin/bash

SERVICE_NAME="oradio"
PORT=8000

# Get service line and hostname line using grep context
SERVICE_INFO=$(avahi-browse -r -t -d local _http._tcp | grep "=.*oradio" -A1)
# Extract the hostname from the second line
HOST=$(echo "$SERVICE_INFO" | grep 'hostname' | awk -F'[][]' '{print $2}')

if [[ -n "$HOST" ]]; then
    echo "Webserver is at: http://$HOST:$PORT"
else
    echo "Service '$SERVICE_NAME' not found."
fi
