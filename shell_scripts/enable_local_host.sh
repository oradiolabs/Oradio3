#!/bin/bash
# Temporarily set the hostname
sudo hostnamectl hostname oradio
# Restart avahi-daemon first to pick up the new hostname
sudo systemctl restart avahi-daemon.service
# Start avahi-publish in the background
sudo avahi-publish -s "oradio" _http._tcp 8000 &
# Store the PID of the backgrounded avahi-publish command
echo $! > /tmp/oradio_webserver.pid
