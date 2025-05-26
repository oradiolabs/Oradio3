#!/bin/bash

TARGET_HOSTNAME="oradio"

# Only change hostname if needed
if [[ "$(hostname)" != "$TARGET_HOSTNAME" ]]; then
    echo "Changing hostname to $TARGET_HOSTNAME..."
    sudo hostnamectl hostname "$TARGET_HOSTNAME"
    until systemctl is-active --quiet avahi-daemon; do sleep 0.5; done
fi

# Start avahi-publish in background
# send stderr (2) to stdout (1)  and stdout to /dev/null, so nothing at output
# so avahi-publish will return to script  
sudo avahi-publish -s "$TARGET_HOSTNAME" _http._tcp 8000 > /dev/null 2>&1 &

# Capture and store PID
AVAHI_PID=$!
echo "$AVAHI_PID" > /tmp/oradio_webserver.pid
echo "Started avahi-publish with PID: $AVAHI_PID"

# Return to Python or shell
exit 0