#!/bin/bash

READY_FILE="/run/user/1000/mpv_ready/ready"
TIMEOUT=45

echo "Waiting for $READY_FILE to appear..."

for i in $(seq 1 "$TIMEOUT"); do
    if [ -e "$READY_FILE" ]; then
        echo "mpv.service is ready."
        exit 0
    fi
    sleep 1
done

echo "Timeout: mpv.service did not become ready."
exit 1
