#!/bin/bash
# disable the 'local' setting  for local host naming 
sudo sed -i "s/^publish-addresses=.*/publish-addresses=yes/g" /etc/avahi/avahi-daemon.conf
sudo sed -i "s/^publish-domain=.*/publish-domain=yes/g" /etc/avahi/avahi-daemon.conf
# reload the service daemon
sudo systemctl daemon-reload
# restart the avahi-daemon service
sudo systemctl restart avahi-daemon.service

