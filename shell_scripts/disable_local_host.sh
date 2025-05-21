#!/bin/bash
## Get the current IP address
#IP_ADDRESS=$(ifconfig wlan0 | grep 'inet ' | awk '{print $2}')
## Get the current hostname
#AVAHI_HOSTNAME=$(avahi-resolve -a $IP_ADDRESS |awk '{print $2}' | cut -d'.' -f1)
# disable the 'local' setting  for local host naming 
sudo sed -i "s/^publish-addresses=.*/publish-addresses=yes/g" /etc/avahi/avahi-daemon.conf
sudo sed -i "s/^publish-domain=.*/publish-domain=no/g" /etc/avahi/avahi-daemon.conf
# reload the service daemon
sudo systemctl daemon-reload
# restart the avahi-daemon service
sudo systemctl restart avahi-daemon.service
