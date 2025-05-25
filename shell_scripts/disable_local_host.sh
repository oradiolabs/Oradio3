#!/bin/bash

sudo hostnamectl hostname mijnOradio
sudo systemctl restart avahi-daemon.service
sudo pkill -f avahi-publish
# Optionally restart your fallback service
sudo avahi-publish -s "mijnOradio" _http._tcp 8000 &