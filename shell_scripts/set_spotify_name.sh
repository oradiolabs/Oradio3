#!/bin/bash

# Change the librespot/Spotify name
if [ -z "$1" ]; then
	echo "Usage: $0 <new spotify name>"
	exit 1
fi

# Replaces spaces with underscores
# Removes non-alphanumeric characters except underscores and dashes
# Trims leading/trailing special characters
# Limits to 63 characters
to_librespot_name() {
	echo "$1" | \
		tr ' ' '_' | \
		tr -cd '[:alnum:]_-' | \
		sed 's/^[ _-]*//' | \
		sed 's/[ _-]*$//' | \
		cut -c1-63
}

# Set Spotify name
SPOTIFY_NAME=$(to_librespot_name "$1")

# Create override directory
OVERRIDE_DIR="/etc/systemd/system/librespot.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/override.conf"
sudo mkdir -p "$OVERRIDE_DIR"

# Create override file
sudo bash -c "cat > $OVERRIDE_FILE" <<EOF
[Service]
Environment="LIBRESPOT_NAME=$SPOTIFY_NAME"
EOF

#ISSUE: It is not possible to reload/restart only librespot.service: Either Oradio stops working, or Oradio service restarts as well
# Challenge is to find a solution to ONLY restart librespot service, WITHOUT restarting Oradio
# Work around:
# - Script does NOT restart the librespot service
# - User is informed to power-cycle the Oradio to activate the new Spotify name

# Reload systemd
#sudo systemctl daemon-reload

# Restart librespot service: Supposedly using stop/start, not restart, prevents autostart service to restart. HOWEVER: Oradio then stops responding to buttons
#sudo systemctl stop librespot.service
#sudo systemctl start librespot.service

# Restart librespot service: This causes autostart service to restart as well
#sudo systemctl restart librespot.service

# Return name for use in web interface
echo $SPOTIFY_NAME
