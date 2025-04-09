#!/bin/bash
# Get the current IP address
IP_ADDRESS=$(ifconfig wlan0 | grep 'inet ' | awk '{print $2}')
# Retry hostname resolution until it's available
MAX_RETRIES=30
for i in $(seq 1 $MAX_RETRIES); do
    HOSTNAME=$(avahi-resolve -a $IP_ADDRESS | awk '{print $2}')
    if [[ -n "$HOSTNAME" ]]; then
        echo "Resolved hostname: $HOSTNAME"
        echo "LIBRESPOT_NAME=$HOSTNAME"
        break
    fi
    echo "Waiting for Avahi to resolve hostname... ($i/$MAX_RETRIES)"
    sleep 1
done
logger "enable_local_host.sh: Resolved after $i attemps"

# Get the current hostname
AVAHI_HOSTNAME=$(avahi-resolve -a $IP_ADDRESS |awk '{print $2}' | cut -d'.' -f1)
echo "AVAHI_HOSTNAME = $AVAHI_HOSTNAME"
# assign AVAHI_HOSTNAME to LIBRESPOT_NAME in librespot.service
sudo sed -i "s/LIBRESPOT_NAME=.*/LIBRESPOT_NAME=$AVAHI_HOSTNAME\"/g" /etc/systemd/system/librespot.service	
# disable the 'local' setting  for local host naming 
sudo sed -i "s/^publish-addresses=.*/publish-addresses=yes/g" /etc/avahi/avahi-daemon.conf
sudo sed -i "s/^publish-domain=.*/publish-domain=yes/g" /etc/avahi/avahi-daemon.conf
# reload the service daemon
#sudo systemctl daemon-reload
# restart the avahi-daemon service
#sudo systemctl restart avahi-daemon.service
# restart librespot service		
#sudo systemctl restart librespot.service	

