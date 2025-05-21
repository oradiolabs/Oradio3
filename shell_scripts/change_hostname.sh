#!/bin/bash

TEMP_HOSTNAME="oradio"
ORIGINAL_HOSTNAME=$(hostname)
echo "ORIGINAL_HOSTNAME=$ORIGINAL_HOSTNAME"
sudo hostnamectl hostname $TEMP_HOSTNAME 
#sudo systemctl restart avahi-daemon
