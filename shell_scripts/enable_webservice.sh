#!/bin/bash
avahi-publish -s "oradio-ws" _http._tcp 8000 &
echo $! > /tmp/avahi_web_pid
