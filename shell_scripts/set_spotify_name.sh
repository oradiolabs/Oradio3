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

# Override-map maken indien niet bestaand
OVERRIDE_DIR="/etc/systemd/system/librespot.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/override.conf"

sudo mkdir -p "$OVERRIDE_DIR"

# Create override file
sudo bash -c "cat > $OVERRIDE_FILE" <<EOF
[Service]
Environment="LIBRESPOT_NAME=$SPOTIFY_NAME"
EOF

# Reload systemd
sudo systemctl daemon-reload

# Restart librespot service
sudo systemctl restart librespot.service

# Return name for use in web interface
echo $SPOTIFY_NAME
