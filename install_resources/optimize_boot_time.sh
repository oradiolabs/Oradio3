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
# @version:       2
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Purpose:       Optimizes Oradio boot process
#
# @target:        Raspberry Pi OS Trixie Lite 64-bit, Pi 3 Model A+ (512 MB, Wi-Fi only)
#
# MEASURED RESULT (Pi 3A+, Trixie Lite 64-bit, August 2026):
#                        before      after
#   kernel                6.07s      2.83s
#   basic.target         11.97s      3.89s   <- when Oradio can start
#   multi-user.target    16.83s      9.46s
#   total                25.62s     12.31s
#
#   Order the Oradio service against basic.target, NOT network.target or
#   network-online.target. NetworkManager takes ~4.8s to associate Wi-Fi and
#   inflates multi-user.target, but Oradio detects the network itself and does
#   not need to wait for it.
#
# HEADLESS: this script removes the display stack, so there is no HDMI output
# and no serial console. Recovery from a bad boot means reading the SD card on
# another machine. The script keeps no backups; use a card image instead.

# Stop script on command errors, unset variables and failed pipes
set -o errexit -o nounset -o pipefail

# Color definitions
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

# Count non-fatal problems so the script can still exit 1 at the end
FAILURES=0

# Report and exit 1 on any unhandled command failure
trap 'echo -e "${RED}Failed at line ${LINENO}${NC}" >&2; exit 1' ERR

CONFIG_FILE="/boot/firmware/config.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
BLACKLIST_FILE="/etc/modprobe.d/oradio-boot-blacklist.conf"
MARK_BEGIN="# >>> Oradio boot optimization >>>"
MARK_END="# <<< Oradio boot optimization <<<"

# Refuse to run as root directly: the script uses sudo deliberately so that
# file ownership stays correct and mistakes are easier to trace
if [[ "${EUID}" -eq 0 ]]; then
	echo -e "${RED}Run this script as a normal user; it calls sudo itself${NC}" >&2
	exit 1
fi

# ----------------
# Helper function
# ----------------
# Mask a unit, but only if it actually exists.
# 'systemctl mask' silently succeeds on non-existent units, which hides typos.
mask_unit() {
	local unit="$1"

	if ! systemctl list-unit-files --no-legend -- "$unit" 2>/dev/null | grep -q .; then
		echo "Unit '$unit' not present, skipping"
		return 0
	fi

	if [[ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" == "masked" ]]; then
		echo "Unit '$unit' already masked"
	elif sudo systemctl mask "$unit" >/dev/null; then
		echo "Unit '$unit' masked"
	else
		echo -e "${YELLOW}Warning: failed to mask unit '$unit'${NC}"
		FAILURES=$((FAILURES + 1))
	fi
}

# -----------------------------------------
# Remove packages slowing down boot process
# -----------------------------------------
# Note: cloud-init is deliberately NOT in this list. See the section below.
# Note: 'dpkg -s' also succeeds for removed-but-not-purged packages, so the
# package status is matched explicitly.
PACKAGES_TO_REMOVE=(
	modemmanager	# No mobile modem present
)

for package in "${PACKAGES_TO_REMOVE[@]}"; do
	if dpkg-query -W -f='${Status}\n' "$package" 2>/dev/null \
			| grep -q '^install ok installed'; then
		echo "Removing package '$package'..."
		sudo apt-get purge -y "$package" >/dev/null
		echo "Package '$package' removed"
	else
		echo "Package '$package' not installed"
	fi
done

# ------------------------------------------
# apt: drop the unused foreign architecture
# ------------------------------------------
# Raspberry Pi OS 64-bit enables armhf by default so 32-bit binaries can run.
# On a pure arm64 appliance that doubles the package indices fetched by every
# 'apt update' for no benefit. Removing it is refused by dpkg if any armhf
# package is installed, but check first so the failure is explained, not raised.
if dpkg --print-foreign-architectures 2>/dev/null | grep -qx armhf; then
	armhf_pkgs="$(dpkg-query -W -f='${Package}:${Architecture}\n' 2>/dev/null \
		| grep ':armhf$' || true)"
	if [[ -n "$armhf_pkgs" ]]; then
		echo -e "${YELLOW}Keeping armhf: packages are installed for it${NC}"
		echo "$armhf_pkgs" | sed 's/^/  /'
	elif sudo dpkg --remove-architecture armhf; then
		echo "Removed foreign architecture armhf"
	else
		echo -e "${YELLOW}Warning: failed to remove architecture armhf${NC}"
		FAILURES=$((FAILURES + 1))
	fi
else
	echo "Foreign architecture armhf not enabled"
fi

# Translated package descriptions are several MB per 'apt update' and are not
# used on an appliance that is administered in English over SSH.
APT_LANG_CONF="/etc/apt/apt.conf.d/99oradio-no-translations"
printf 'Acquire::Languages "none";\n' | sudo tee "$APT_LANG_CONF" >/dev/null
echo "Disabled apt translation downloads in $APT_LANG_CONF"

# -----------------------------------------------
# cloud-init: keep installed, disable after setup
# -----------------------------------------------
# Raspberry Pi OS Trixie images from 24 November 2025 onwards ship cloud-init
# and use it for first-boot provisioning instead of firstrun.sh. Purging it
# would break imaging new Oradio units with Raspberry Pi Imager 2.x, so it is
# disabled at runtime rather than removed.
#
# WARNING: if this image is cloned to produce cards for new units, those clones
# will not run cloud-init provisioning either. That is correct for a fully
# prepared golden image, but wrong if you expect Imager to customise the clone.
CLOUD_INIT_DISABLED="/etc/cloud/cloud-init.disabled"

if dpkg-query -W -f='${Status}\n' cloud-init 2>/dev/null \
		| grep -q '^install ok installed'; then
	if [[ -f "$CLOUD_INIT_DISABLED" ]]; then
		echo "cloud-init already disabled"
	else
		sudo mkdir -p "$(dirname "$CLOUD_INIT_DISABLED")"
		sudo touch "$CLOUD_INIT_DISABLED"
		echo "Disabled cloud-init via $CLOUD_INIT_DISABLED"
	fi
else
	echo "cloud-init not installed"
fi

# --------------------------------------
# /boot/firmware/config.txt optimization
# --------------------------------------
# Disable the KMS display stack: no HDMI output on a deployed Oradio.
#
# NOTE: this stops vc4 loading, but systemd-logind still pulls in
# modprobe@drm.service, which loads drm (~700 KB) even with no display.
# The module block further down is what actually prevents that.
sudo sed -i \
	-e 's/^\s*dtoverlay=vc4-kms-v3d.*$/#&  # Oradio: headless, no KMS/' \
	-e 's/^\s*max_framebuffers=.*$/#&  # Oradio: headless/' \
	-e 's/^\s*camera_auto_detect=1/camera_auto_detect=0/' \
	-e 's/^\s*display_auto_detect=1/display_auto_detect=0/' \
	-e 's/^\s*auto_initramfs=1/auto_initramfs=0/' \
	"$CONFIG_FILE"
echo "Adjusted existing settings in $CONFIG_FILE"

# Append the Oradio block once, in an idempotent marked section
if ! grep -Fq "$MARK_BEGIN" "$CONFIG_FILE"; then
	{
		echo ""
		echo "$MARK_BEGIN"
		echo "initial_turbo=60      # Full clock during boot instead of ramping up"
		echo "disable_splash=1      # No rainbow splash screen"
		echo "boot_delay=0          # No artificial delay before reading the SD card"
		echo "dtoverlay=disable-bt  # No Bluetooth on an Oradio device"
		echo "gpu_mem=16            # Minimal GPU split now that KMS is disabled"
		echo "$MARK_END"
	} | sudo tee -a "$CONFIG_FILE" >/dev/null
	echo "Added Oradio block to $CONFIG_FILE"
else
	echo "Oradio block already present in $CONFIG_FILE"
fi

# ---------------------------------------
# /boot/firmware/cmdline.txt optimization
# ---------------------------------------
# cmdline.txt MUST remain exactly one line. Rebuild it explicitly rather than
# using sed, which would append to every line if a trailing newline exists.
CMDLINE_OPTS=(
	quiet			# Suppress most kernel boot messages
	loglevel=3		# Errors only
	logo.nologo		# No framebuffer logo
)

line="$(head -n1 "$CMDLINE_FILE")"

for option in "${CMDLINE_OPTS[@]}"; do
	if grep -qw -- "$option" <<<"$line"; then
		echo "Option '$option' already in $CMDLINE_FILE"
	else
		line="$line $option"
		echo "Adding '$option' to $CMDLINE_FILE"
	fi
done

# Console output on the boot path is synchronous and slow, and there is no
# console on a deployed unit anyway
line="$(sed -E 's/\bconsole=serial0,[0-9]+ ?//g' <<<"$line")"

# Collapse duplicate spaces and write back as a single line
line="$(tr -s ' ' <<<"$line" | sed -e 's/^ //' -e 's/ $//')"
printf '%s\n' "$line" | sudo tee "$CMDLINE_FILE" >/dev/null
echo "Wrote $CMDLINE_FILE"

# -----------------------
# Kernel Module Blacklist
# -----------------------
# Only modules that are genuinely built as modules on this kernel are listed.
# Built-in modules cannot be blacklisted, and 'blacklist' does not prevent
# dependency loading, so 'install ... /bin/true' is used for a hard block.
BLACKLIST_CONTENT=(
	udf				# Optical disc filesystem (DVD/BD) - not used
	cramfs			# Old compressed filesystem - not used
	bcm2835_codec	# Hardware video codec - not used by an audio appliance
	vc4				# Raspberry Pi VideoCore graphics driver
	drm_kms_helper	# Kernel Mode Setting helper
	drm				# Direct Rendering Manager - no display on this device
)

# Note: 'fuse' is deliberately NOT blacklisted. Blocking it breaks USB
# automounting, ntfs-3g and anything else built on FUSE.
# Note: 'squashfs' and 'configfs' are typically built into the Pi kernel and
# cannot be blacklisted; verify with 'modinfo -n <module>' before adding.

sudo touch "$BLACKLIST_FILE"

for module in "${BLACKLIST_CONTENT[@]}"; do
	# Only act on modules that exist as loadable modules on this kernel
	if ! modinfo -n "$module" >/dev/null 2>&1; then
		echo "Module '$module' is built-in or absent, skipping"
		continue
	fi

	entry="install $module /bin/true"
	if ! grep -Fxq "$entry" "$BLACKLIST_FILE"; then
		echo "Add '$entry' to $BLACKLIST_FILE"
		echo "$entry" | sudo tee -a "$BLACKLIST_FILE" >/dev/null
	else
		echo "'$entry' found in $BLACKLIST_FILE"
	fi
done

# --------------------------------
# SSH: switch to socket activation
# --------------------------------
# Socket activation keeps ssh.service off the boot path entirely rather than
# merely reordering it: sshd is spawned per connection. This is the upstream
# Debian 13 default, but Raspberry Pi OS images may still enable ssh.service.
#
# There is a brief window where nothing listens on port 22, because ssh.socket
# cannot bind while sshd holds the port. Existing sessions survive (KillMode is
# 'process'), and the switch is rolled back automatically if the socket fails.
#
# NOTE: after this change, 'Port' and 'ListenAddress' in sshd_config are
# ignored. Override ListenStream= in /etc/systemd/system/ssh.socket.d/ instead.
if ! systemctl list-unit-files --no-legend -- ssh.socket 2>/dev/null | grep -q .; then
	echo "No ssh.socket unit available, leaving SSH configuration alone"
elif [[ "$(systemctl is-enabled ssh.socket 2>/dev/null || true)" == "enabled" ]]; then
	echo "ssh.socket already enabled: SSH is off the boot path"
else
	echo "Switching SSH to socket activation..."

	sudo systemctl disable ssh.service >/dev/null 2>&1 || true
	sudo systemctl enable ssh.socket >/dev/null 2>&1 || true

	# Free the port, then hand it to the socket
	sudo systemctl stop ssh.service >/dev/null 2>&1 || true

	if sudo systemctl start ssh.socket >/dev/null 2>&1 \
			&& [[ "$(systemctl is-active ssh.socket 2>/dev/null || true)" == "active" ]]; then
		echo "SSH now handled by ssh.socket"
	else
		echo -e "${RED}ssh.socket failed to start, rolling back to ssh.service${NC}" >&2
		FAILURES=$((FAILURES + 1))
		sudo systemctl disable ssh.socket >/dev/null 2>&1 || true
		sudo systemctl enable --now ssh.service >/dev/null 2>&1 || true
		if [[ "$(systemctl is-active ssh.service 2>/dev/null || true)" == "active" ]]; then
			echo -e "${YELLOW}Rolled back: ssh.service is listening again${NC}"
		else
			echo -e "${RED}WARNING: no SSH listener is active. Do NOT reboot${NC}" >&2
		fi
	fi
fi

# --------------------------------------------------
# Mask units that are not needed on an Oradio device
# --------------------------------------------------
# Explicit list rather than a reverse-dependency walk: on a fixed appliance
# image the unit set is known, and a graph walk risks masking something that
# another unit hard-Requires.
UNITS_TO_MASK=(
	apt-daily.timer					# No daily apt index refresh
	apt-daily.service
	apt-daily-upgrade.timer			# No unattended upgrades
	apt-daily-upgrade.service
	fstrim.timer					# Not relevant for SD card
	dpkg-db-backup.timer			# No dpkg database backup needed
	man-db.timer					# No man page index on an appliance
	logrotate.timer					# Journal is volatile, see below
	e2scrub_all.timer				# Only relevant for LVM-backed ext4
	e2scrub_reap.service
	rpi-resize-swap-file.service	# Swap is provided by zram, no swapfile to resize
	rpi-eeprom-update.service		# Pi 3A+ has no bootloader EEPROM
	NetworkManager-wait-online.service	# Oradio detects network availability itself
	hciuart.service					# Bluetooth disabled in config.txt via disable-bt
	bluetooth.service
	triggerhappy.service			# No input devices / hotkeys
	triggerhappy.socket
	keyboard-setup.service			# No keyboard attached
	console-setup.service			# No local console font/keymap needed
	sshswitch.service				# SSH comes from ssh.socket, not the /boot flag file;
									# also holds an After=boot-firmware.mount that would
									# trigger the automount during boot
	systemd-random-seed.service		# Optional: only if boot entropy is a bottleneck
	# cloud-init stages: provisioning is complete on a deployed unit.
	# Unit names differ between versions: 24.x renamed cloud-init.service to
	# cloud-init-network.service. Both are listed; absent ones are skipped.
	cloud-init-local.service
	cloud-init.service
	cloud-init-network.service
	cloud-init-main.service
	cloud-config.service
	cloud-final.service
	cloud-init-hotplugd.socket		# Hotplug hook, not used by Oradio
	cloud-init-hotplugd.service
	cloud-config.target
	cloud-init.target
)

# Note: avahi-daemon is deliberately NOT masked. It is installed via the
# cloud-init user-data, so mDNS (.local) discovery is wanted on this device.

for unit in "${UNITS_TO_MASK[@]}"; do
	mask_unit "$unit"
done

# ---------------------------------------------------
# /boot/firmware: automount instead of boot-time fsck
# ---------------------------------------------------
# Nothing reads /boot/firmware at runtime; only kernel and firmware updates do.
# Mounting it on demand removes both the fsck and the mount from the boot path,
# while keeping the path transparently available when something touches it.
#
# A broken fstab drops the system to emergency mode, so the new file is
# validated before it is installed.
FSTAB="/etc/fstab"
FSTAB_OPTS="defaults,noauto,x-systemd.automount,x-systemd.idle-timeout=60"

if ! grep -qE '^[^#].*[[:space:]]/boot/firmware[[:space:]]' "$FSTAB"; then
	echo -e "${YELLOW}No /boot/firmware entry in $FSTAB, skipping automount${NC}"
elif grep -qE '^[^#].*[[:space:]]/boot/firmware[[:space:]].*x-systemd\.automount' "$FSTAB"; then
	echo "/boot/firmware already configured as automount"
else
	# Warn about units that would trigger the automount during boot anyway
	triggers="$(systemctl list-dependencies --reverse --plain --no-legend \
		boot-firmware.mount 2>/dev/null | sed '1d' | tr -d ' ' || true)"
	if [[ -n "$triggers" ]]; then
		echo -e "${YELLOW}Units ordered against boot-firmware.mount:${NC}"
		echo "$triggers" | sed 's/^/  /'
		echo -e "${YELLOW}These will still trigger the mount at boot${NC}"
	fi

	tmp_fstab="$(mktemp)"
	# Rewrite only the /boot/firmware line: on-demand options, no fsck pass
	awk -v opts="$FSTAB_OPTS" 'BEGIN { OFS = "\t" }
		/^[[:space:]]*#/ { print; next }
		NF >= 6 && $2 == "/boot/firmware" { $4 = opts; $5 = "0"; $6 = "0"; print; next }
		{ print }' "$FSTAB" > "$tmp_fstab"

	# Validate before installing; findmnt parses fstab the way systemd does
	if findmnt --verify --tab-file "$tmp_fstab" >/dev/null 2>&1; then
		sudo cp "$tmp_fstab" "$FSTAB"
		sudo chmod 644 "$FSTAB"
		echo "Configured /boot/firmware as automount in $FSTAB"
	else
		echo -e "${RED}Generated fstab failed validation, leaving $FSTAB unchanged${NC}" >&2
		findmnt --verify --tab-file "$tmp_fstab" || true
		FAILURES=$((FAILURES + 1))
	fi
	rm -f "$tmp_fstab"
fi

# ----------------------------
# Reduce journald write volume
# ----------------------------
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/oradio.conf"
sudo mkdir -p "$(dirname "$JOURNALD_DROPIN")"
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=8M\n' \
	| sudo tee "$JOURNALD_DROPIN" >/dev/null
echo "Configured volatile journal in $JOURNALD_DROPIN"

# Apply unit file changes
sudo systemctl daemon-reload

if [[ "$FAILURES" -gt 0 ]]; then
	echo -e "${RED}Boot time optimizations incomplete: $FAILURES step(s) failed${NC}" >&2
	exit 1
fi

echo -e "${GREEN}Boot time optimizations applied${NC}"
