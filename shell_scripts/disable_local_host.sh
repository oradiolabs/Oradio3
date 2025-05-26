#!/bin/bash
TARGET_HOSTNAME="mijnOradio"
# Only change hostname if needed
if [[ "$(hostname)" != "$TARGET_HOSTNAME" ]]; then
    echo "Changing hostname to $TARGET_HOSTNAME..."
    sudo hostnamectl hostname "$TARGET_HOSTNAME"
    until systemctl is-active --quiet avahi-daemon; do sleep 0.5; done
fi
#sudo systemctl restart avahi-daemon.service
sudo pkill -f avahi-publish
# Optionally restart your fallback service
# send stderr (2) to stdout (1)  and stdout to /dev/null, so nothing at output
# so avahi-publish will return to script  
sudo avahi-publish -s "$TARGET_HOSTNAME" _http._tcp 8000 > /dev/null 2>&1 &

