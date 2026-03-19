#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on March 12, 2026
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Purpose:       Optimizes Oradio boot process

# Stop script on command errors, unset variables used
set -o errexit -o nounset

# Color definitions
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# -----------------------------------------
# Remove packages slowing sown boot process
# -----------------------------------------
PACKAGES_TO_REMOVE=(
	cloud-init		# No cloud services used
	modemmanager	# No mobile modem present
)

for package_with_comment in "${PACKAGES_TO_REMOVE[@]}"; do
    # Extract only the first word (before any whitespace)
    package="${package_with_comment%%[[:space:]]*}"

	# Remove package if installed
	if dpkg -s "$package" >/dev/null 2>&1; then
		echo "Removing package '$package'..."
		sudo apt-get purge -y "$package" >/dev/null
		echo "Package '$package' removed"
	else
		echo "Package '$package' is purged"
	fi
done

# -------------------
# SSH Parallelization
# -------------------
#!/bin/bash
SSH_UNIT_FILE="/lib/systemd/system/ssh.service"

# Replace any line starting with "After=" in the [Unit] section with "After=basic.target"
sudo sed -i '/^\[Unit\]/,/^\[/{s/^After=.*/After=basic.target/}' "$SSH_UNIT_FILE"
echo "Modified $SSH_UNIT_FILE to start after basic.target"

# ---------------------------------------
# /boot/firmware/cmdline.txt optimization
# ---------------------------------------
CMDLINE_FILE="/boot/firmware/cmdline.txt"
CMDLINE_OPTS=(
	elevator=deadline	# Better SD/USB IO
	quiet				# Suppress most boot messages
	loglevel=0			# Show only critical errors
	fastboot			# Skip hardware checks and delays
)

# Append options only if not already present
for option_with_comment in "${CMDLINE_OPTS[@]}"; do
    # Extract only the first word (before any whitespace)
    option="${option_with_comment%%[[:space:]]*}"
	# Add option if not yet present
    if ! grep -q "$option" "$CMDLINE_FILE"; then
		echo "Adding '$option' to $CMDLINE_FILE"
        sudo sed -i "/\b$option\b/! s/$/ $option/" "$CMDLINE_FILE"
	else
		echo "Option '$option' found in $CMDLINE_FILE"
    fi
done

# -----------------------
# Kernel Module Blacklist
# -----------------------
BLACKLIST_FILE="/etc/modprobe.d/raspi-boot-blacklist.conf"
# Comments explain why the module is unnecessary for a headless system.
BLACKLIST_CONTENT=(
	udf				# Optical disc filesystem (DVD/BD) – not used
	squashfs		# Compressed read-only filesystem – often used by live systems/snaps
	cramfs			# Old compressed filesystem – rarely used today
	drm				# Direct Rendering Manager (GPU/display stack)
	vc4				# Raspberry Pi VideoCore IV graphics driver
	drm_kms_helper	# Kernel Mode Setting helper for graphics
	fuse			# Filesystem in Userspace support
	configfs		# Kernel configuration filesystem used by advanced kernel subsystems
	bcm2835_v4l2	# Raspberry Pi camera V4L2 driver
	bcm2835_codec	# Raspberry Pi hardware video codec
)

# Ensure the blacklist file exists so entries can be appended safely
sudo touch "$BLACKLIST_FILE"

# Blacklist modules if not already present
for module_with_comment in "${BLACKLIST_CONTENT[@]}"; do
    # Extract only the module name (first token before any whitespace)
    line="blacklist ${module_with_comment%%[[:space:]]*}"
	if ! grep -Fxq "$line" "$BLACKLIST_FILE"; then
		echo "Add '$line' to $BLACKLIST_FILE"
		echo "$line" | sudo tee -a "$BLACKLIST_FILE" > /dev/null || true
	else
		echo "'$line' found in $BLACKLIST_FILE"
	fi
done

# -----------------------------------------------------------------------------------------
# Future-proof masking of services and their related units, excluding system-critical units
# -----------------------------------------------------------------------------------------
SERVICES_TO_MASK=(
	apt-daily						# No daily apt updates
	fstrim.timer					# Not relevant for SD card
	dpkg-db-backup					# No dpkgs backup needed
	rpi-eeprom-update				# No update of bootloader EEPROM
	rpi-resize-swap-file			# Swap settings are stable
	NetworkManager-wait-online		# Oradio detects if network is available
)

# Define a whitelist of safe unit types/extensions to mask
SAFE_EXTENSIONS=("service" "timer" "socket" "path")

for service_with_comment in "${SERVICES_TO_MASK[@]}"; do
    # Extract only the first word (before any whitespace)
    service="${service_with_comment%%[[:space:]]*}"

	# Mask the main service if not already masked
	if ! systemctl is-enabled "$service" | grep -q masked; then
		sudo systemctl mask "$service" || true
	fi
	echo "Service '$service' masked"

	# Stop and mask directly related units (same base name)
	base="${service%.service}"
	for ext in "${SAFE_EXTENSIONS[@]}"; do
		unit="${base}.${ext}"
		# Skip if same as main service
		[[ "$unit" == "$service" || "$unit" == "${service}.service" ]] && continue
		if systemctl list-unit-files --no-legend | grep -q "^$unit"; then
			# Mask if not masked
			if ! systemctl is-enabled "$unit" | grep -q masked; then
				sudo systemctl mask "$unit" || true
			fi
			echo "Related unit '$unit' masked"
		fi
	done

	# Detect reverse dependencies safely
	triggering_units=$(systemctl list-dependencies --reverse --plain --no-legend "$service" | awk '{print $1}' | grep -E '\.(service|timer|socket|path)$' || true)
	for unit in $triggering_units; do
		# Skip if same as main service
		[[ "$unit" == "$service" || "$unit" == "${service}.service" ]] && continue
		# Skip system-critical units
		if [[ "$unit" =~ ^(basic|sysinit|multi-user|graphical|dbus|user) ]]; then
			continue
		fi
		# Mask if not masked
		if ! systemctl is-enabled "$unit" | grep -q masked; then
			sudo systemctl mask "$unit" || true
		fi
		echo "Reverse dependency '$unit' masked"
	done

done

echo -e "${GREEN}Boot time optimizations applied${NC}"
