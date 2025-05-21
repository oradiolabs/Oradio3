#!/bin/bash

# Get the RPI's IP address dynamically
IP_ADDRESS=${1:-$(hostname -I | awk '{print $1}')}

# Resolve the hostname using Avahi
AVAHI_HOSTNAME=$(avahi-resolve -a "$IP_ADDRESS" | awk '{print $2}' | cut -d'.' -f1)
echo $AVAHI_HOSTNAME
# Fallback if the hostname wasn't found
if [[ -z "$AVAHI_HOSTNAME" ]]; then
    echo "Oradio-SpotifyConnect-Unknown"
    exit 1
fi

# Use avahi-browse to find the correct Spotify service name
SPOTIFY_NAME=$(avahi-browse -d local _spotify-connect._tcp -p | awk -F';' -v host="$AVAHI_HOSTNAME" '
    $4 ~ host { print $4; exit }')

# Fallback if no match is found
if [[ -z "$SPOTIFY_NAME" ]]; then
    SPOTIFY_NAME="Oradio-SpotifyConnect-$AVAHI_HOSTNAME"
fi

echo "$SPOTIFY_NAME"
