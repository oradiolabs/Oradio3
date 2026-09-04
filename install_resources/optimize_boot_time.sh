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
# @version:       3
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Purpose:       Optimizes Oradio boot process
#
# @target:        Raspberry Pi OS Trixie Lite 64-bit, Pi 3 Model A+ (512 MB, Wi-Fi only)
#
# The metric this script optimises is when oradio.service can exec, which
# requires i2c, gpio and the sound card to be usable. multi-user.target is not
# a useful measure here: it is gated by units this script deliberately
# deprioritises.
#
# Measure with 'systemd-analyze plot'. Oradio's own hardware-readiness waits are
# logged per boot by oradio-prestart.sh.
#
# Recovery from a bad boot means reading the SD card on another machine. This
# script keeps no backups; use a card image instead.

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

CMDLINE_FILE="/boot/firmware/cmdline.txt"
BLACKLIST_FILE="/etc/modprobe.d/oradio-boot-blacklist.conf"

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
	local unit="$1" err

	if ! systemctl list-unit-files --no-legend -- "$unit" 2>/dev/null | grep -q .; then
		echo -e "${YELLOW}Unit '$unit' not present, skipping${NC}"
		return 0
	fi

	if [[ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" == "masked" ]]; then
		echo "Unit '$unit' already masked"
	elif err="$(sudo systemctl mask "$unit" 2>&1 >/dev/null)"; then
		echo "Unit '$unit' masked"
	else
		echo -e "${YELLOW}Warning: failed to mask unit '$unit'${NC}"
		[[ -n "$err" ]] && printf '%s\n' "$err" >&2
		FAILURES=$((FAILURES + 1))
	fi
}

# -----------------------------------------
# Remove packages slowing down boot process
# -----------------------------------------
# Note: 'dpkg -s' also succeeds for removed-but-not-purged packages, so the
# package status is matched explicitly.
#
# cloud-init provisions user, hostname, Wi-Fi and SSH keys on FIRST BOOT from
# the user-data Raspberry Pi Imager writes to the boot partition. That has
# already run by the time this script executes, so purging it does not break the
# "flash with Imager -> run oradio_install.sh" flow.
#
# WARNING: a card prepared this way can no longer be customised per-device by
# Imager when cloned. Correct for an identical golden image, wrong if each unit
# needs unique settings. For the latter, remove cloud-init from this list,
# disable it at runtime with 'touch /etc/cloud/cloud-init.disabled', and re-add
# the cloud-* entries to UNITS_TO_MASK and GENERATORS_TO_MASK below.
PACKAGES_TO_REMOVE=(
	cloud-init		# First-boot provisioning only; see the warning above. Purging
					# removes its units and its systemd generator.
)

for package in "${PACKAGES_TO_REMOVE[@]}"; do
	if dpkg-query -W -f='${Status}\n' "$package" 2>/dev/null \
			| grep -q '^install ok installed'; then
		echo "Removing package '$package'..."
		if sudo apt-get purge -y "$package" >/dev/null; then
			echo "Package '$package' removed"
		else
			echo -e "${RED}Failed to purge '$package'${NC}" >&2
			FAILURES=$((FAILURES + 1))
		fi
	else
		echo "Package '$package' not installed"
	fi
done

# Purging cloud-init orphans a set of Python packages (jsonschema, jinja2, babel
# and friends). They are NOT removed automatically: 'apt autoremove' would also
# take netcat-openbsd, eatmydata and gdisk, which other things may want. Review
# by hand with 'apt autoremove --dry-run' if SD space matters.
#
# The flag file is not owned by the package, so purging leaves it behind.
sudo rm -f /etc/cloud/cloud-init.disabled
sudo rmdir /etc/cloud 2>/dev/null || true

# Guard: if the purge failed, the cloud-init units and generator are no longer
# masked further down, because those entries were removed on the assumption that
# the package is gone. Say so loudly rather than silently regressing the boot.
if dpkg-query -W -f='${Status}\n' cloud-init 2>/dev/null \
		| grep -q '^install ok installed'; then
	echo -e "${RED}cloud-init is still installed: its units and generator are NOT masked${NC}" >&2
	echo -e "${YELLOW}Either purge it by hand, or re-add the cloud-* entries to${NC}" >&2
	echo -e "${YELLOW}UNITS_TO_MASK and GENERATORS_TO_MASK and re-run this script${NC}" >&2
	FAILURES=$((FAILURES + 1))
fi

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

# ---------------------------------------
# /boot/firmware/cmdline.txt optimization
# ---------------------------------------
# cmdline.txt MUST remain exactly one line. Rebuild it explicitly rather than
# using sed, which would append to every line if a trailing newline exists.

#ONNO: Uncomment quiet and loglevel before merge to main
CMDLINE_OPTS=(
	logo.nologo				# No framebuffer logo
#ONNO: Temporarily show all boot output for debugging
#	quiet					# Suppress most kernel boot messages
#	loglevel=3				# Errors only

	# Contiguous Memory Allocator. The device tree reserves 64 MiB by default for
	# the camera and the KMS display stack, neither of which this device uses
	# (camera_auto_detect=0, no vc4-kms-v3d). What is reclaimed becomes page cache
	# on a 512 MB board. I2S DMA buffers fit comfortably; raise to 32M if audio
	# glitches.
	cma=16M

	# Skip systemd-gpt-auto-generator entirely. The SD card is MBR-partitioned, so
	# the generator can never find a GPT to act on.
	systemd.gpt_auto=0

	# 'brd' (the ramdisk driver) is BUILT IN to the rpt kernel and defaults to 16
	# devices, each producing udev events and systemd device units for a ramdisk
	# nothing uses. Built-in, so it cannot be blacklisted; this is the only way to
	# limit it. Use 1, not 0: some kernel versions treat rd_nr=0 as "the default".
	brd.rd_nr=1

	# usb-storage waits this long before probing a new device, to let it settle.
	# Commented out, so the OS default of 1s applies.
	#
	# Do NOT set this to 0: the SCSI scan then runs before the device is ready, the
	# partition table read fails, and /dev/sda1 never appears at all. Nothing
	# downstream can recover from that.
	#
	# FORMAT: the 'ms' suffix needs the reworked driver (kernel 6.11 and later).
	#
	# SCOPE: delay_use belongs to usb-storage and does nothing for a device bound
	# to the uas driver. Verify after boot:
	#   cat /sys/module/usb_storage/parameters/delay_use
	#   dmesg | grep -i delay_use   # a rejected value logs "invalid for parameter"
#	usb-storage.delay_use=100ms
)

# ---------------------------------------------------------------------------
# Rebuild cmdline.txt by KEY, not by substring
# ---------------------------------------------------------------------------
# Every option in CMDLINE_OPTS is OWNED by this script: any token already on the
# line that shares its key is discarded and replaced by the value above. The file
# therefore converges on CMDLINE_OPTS whatever it contained before.
#
# Matching on the key before '=' rather than on the whole token is what makes
# that true. A whole-string comparison appends a changed value and leaves the old
# token in place, and the kernel then takes whichever comes last - so the
# effective setting would depend on append order rather than on this file.
# Key matching also collapses duplicates as a side effect.
#
# CMDLINE_REMOVE holds keys to strip and NOT re-add. To keep one specific value
# of an otherwise removed key, put that value in CMDLINE_OPTS: it is stripped by
# key first, then re-added. Dropping 'console' from CMDLINE_REMOVE is not needed
# to keep panics on HDMI - adding 'console=tty1' to CMDLINE_OPTS is enough, and
# still discards console=serial0,115200.
#
# Tokens whose key is in neither list are preserved verbatim and in their
# original order: root=, rootfstype=, rootwait, fsck.repair=,
# usb-storage.quirks=, and everything the firmware prepends.
CMDLINE_REMOVE=(
	quiet					# Suppress most kernel boot messages
	loglevel				# Errors only
	usb-storage.delay_use	# Wait before probing a device. Removing defaults to 1s
	console					# Serial and tty consoles: synchronous, slow, and absent in the field
	fastboot				# Old Raspbian "skip fsck"; superseded by fsck.mode=
	elevator				# Kernel logs "does not have any effect anymore"; use sysfs per device
)

declare -A CMDLINE_MANAGED=()	# Keys this script controls
declare -A CMDLINE_FOUND=()		# What was on the line for those keys, for reporting

for option in "${CMDLINE_OPTS[@]}"; do
	CMDLINE_MANAGED["${option%%=*}"]=1
done
for key in "${CMDLINE_REMOVE[@]}"; do
	CMDLINE_MANAGED["$key"]=1
done

# read -ra rather than an unquoted expansion: no globbing, no surprises if a
# value ever contains a character the shell would otherwise interpret.
read -ra cmdline_tokens <<<"$(head -n1 "$CMDLINE_FILE")"

cmdline_keep=()
for token in "${cmdline_tokens[@]}"; do
	key="${token%%=*}"
	if [[ -n "${CMDLINE_MANAGED[$key]:-}" ]]; then
		# Accumulate: a key can legitimately appear more than once (console=),
		# and duplicates from a previous buggy run must all be reported.
		CMDLINE_FOUND["$key"]="${CMDLINE_FOUND[$key]:-}${CMDLINE_FOUND[$key]:+ }$token"
	else
		cmdline_keep+=("$token")
	fi
done

for option in "${CMDLINE_OPTS[@]}"; do
	key="${option%%=*}"
	was="${CMDLINE_FOUND[$key]:-}"
	if [ "$was" = "$option" ]; then
		echo "  unchanged : $option"
	elif [ -n "$was" ]; then
		echo "  updated   : $option   (was: $was)"
	else
		echo "  added     : $option"
	fi
done
for key in "${CMDLINE_REMOVE[@]}"; do
	[[ -n "${CMDLINE_FOUND[$key]:-}" ]] || continue
	# Skip keys that CMDLINE_OPTS re-adds; those were reported as updated above.
	# Exact string comparison, not grep: a key like 'brd.rd_nr' or
	# 'usb-storage.delay_use' would otherwise be a regex where '.' matches any
	# character. Harmless with the current lists, a trap for the next one.
	readded=""
	for option in "${CMDLINE_OPTS[@]}"; do
		[ "${option%%=*}" = "$key" ] && { readded=1; break; }
	done
	[ -n "$readded" ] && continue
	echo "  removed   : ${CMDLINE_FOUND[$key]}"
done

line="${cmdline_keep[*]} ${CMDLINE_OPTS[*]}"
line="$(tr -s ' ' <<<"$line" | sed -e 's/^ //' -e 's/ $//')"

# Refuse to write a line that cannot boot. A cmdline.txt without root= leaves an
# unbootable card that needs another machine to fix, and this script keeps no
# backups by design - so the check has to happen before the write, not after.
if [[ " $line " != *" root="* ]]; then
	echo -e "${RED}ABORT: rebuilt cmdline has no root= parameter. Not writing.${NC}"
	echo "Rebuilt line was: $line"
	exit 1
fi

printf '%s\n' "$line" | sudo tee "$CMDLINE_FILE" >/dev/null
echo "Wrote $CMDLINE_FILE"

# -----------------------
# Kernel Module Blacklist
# -----------------------
# Only modules that are genuinely built as modules on this kernel are listed.
# Built-in modules cannot be blacklisted, and 'blacklist' does not prevent
# dependency loading, so 'install ... /bin/true' is used for a hard block.
BLACKLIST_CONTENT=(
	udf                  # Optical disc filesystem - not used
	bcm2835_codec        # Hardware video codec - not used by an audio appliance
	vc4                  # VideoCore graphics driver
	drm_kms_helper       # Kernel Mode Setting helper
	drm                  # Direct Rendering Manager - no display on this device
	bcm2835_isp          # Camera ISP - probes, fails, unregisters
	bcm2835_mmal_vchiq   # MMAL/VCHIQ camera transport - same
	vc_sm_cma            # VideoCore shared memory - same
	bcm2835_v4l2         # V4L2 capture interface - same. camera_auto_detect=0 in
	                     # config.txt suppresses the overlay but not the modalias
	                     # match that loads these
	snd_bcm2835          # Staging BCM2835 audio; Oradio uses the DigiAMP+ I2S DAC
)

# Note: 'fuse' is deliberately NOT blacklisted. Blocking it breaks USB
# automounting, ntfs-3g and anything else built on FUSE.
# Note: 'squashfs' and 'configfs' are typically built into the Pi kernel and
# cannot be blacklisted; verify with 'modinfo -n <module>' before adding.

sudo touch "$BLACKLIST_FILE"

for module in "${BLACKLIST_CONTENT[@]}"; do
	# Only act on modules that exist as loadable modules on this kernel
	path="$(modinfo -n "$module" 2>/dev/null || true)"
	if [[ -z "$path" || "$path" == "(builtin)" ]]; then
		echo -e "${YELLOW}Module '$module' is built-in or absent, skipping${NC}"
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

# ---------------------------------------------
# Preload the DigiAMP+ sound stack
# ---------------------------------------------
# Left to udev, these modules load during coldplug and the sound card only
# registers once the udev worker queue reaches it. Listing them here moves the
# load into systemd-modules-load.service, which runs well before that queue is
# drained. oradio-prestart.sh gates oradio.service on the card appearing in
# /proc/asound/cards, so this directly determines when Oradio can start.
#
# Cost: systemd-modules-load takes longer and contends for CPU with the rest of
# early boot. Net gain is large.
#
# Only the three top-level modules are listed; snd_soc_core, snd_pcm and the
# rest arrive as dependencies. i2c-bcm2835 is needed first for the PCM5122's
# control bus and comes from /etc/modules, which systemd reads as
# modules-load.d/modules.conf and therefore sorts before this file.
#
# Module names differ between kernel versions (snd_soc_iqaudio_dac vs
# snd_soc_rpi_iqaudio_dac). Verify with 'lsmod | grep snd' after a boot.
SOUND_MODULES_FILE="/etc/modules-load.d/oradio-sound.conf"
printf '%s\n' \
	"snd_soc_bcm2835_i2s" \
	"snd_soc_pcm512x_i2c" \
	"snd_soc_iqaudio_dac" \
	| sudo tee "$SOUND_MODULES_FILE" >/dev/null
echo "Configured early sound module load in $SOUND_MODULES_FILE"

# ------------------------------------
# Stop loading the unused zram module
# ------------------------------------
# /usr/lib/modules-load.d/20-zram-generator.conf loads zram.ko on every boot,
# while zram-generator is masked below and no zram device is ever configured.
# The module still registers /dev/zram0, costing a udev event and three systemd
# device units in the busiest part of the boot.
#
# A /dev/null symlink of the same name in /etc/modules-load.d overrides the file
# in /usr/lib, the same way unit overrides work.
ZRAM_MODLOAD="/etc/modules-load.d/20-zram-generator.conf"
if [[ ! -e /usr/lib/modules-load.d/20-zram-generator.conf ]]; then
	echo -e "${YELLOW}zram modules-load config not present, skipping${NC}"
elif [[ "$(readlink -f "$ZRAM_MODLOAD" 2>/dev/null || true)" == "/dev/null" ]]; then
	echo "zram module load already masked"
elif sudo ln -sf /dev/null "$ZRAM_MODLOAD"; then
	echo "Masked zram module load in $ZRAM_MODLOAD"
else
	echo -e "${YELLOW}Warning: failed to mask $ZRAM_MODLOAD${NC}"
	FAILURES=$((FAILURES + 1))
fi

# -------------------------------------------------------
# Disable systemd generators that do nothing on an Oradio
# -------------------------------------------------------
# Generators run serially before ANY unit starts, so their cost sits at the very
# bottom of the critical chain.
#
# A /dev/null symlink in /etc/systemd/system-generators overrides the binary of
# the same name in /usr/lib/systemd/system-generators, the same way unit file
# overrides work.
#
# Two are deliberately NOT masked:
#   systemd-fstab-generator - required; builds the mount units from /etc/fstab
#   systemd-debug-generator - cheap, and it is what makes systemd.mask=,
#                             systemd.wants= and systemd.debug_shell work from
#                             cmdline.txt. On a device where recovery means
#                             pulling the SD card, that escape hatch is worth
#                             paying for.
GENERATOR_SRC_DIR="/usr/lib/systemd/system-generators"
GENERATOR_DIR="/etc/systemd/system-generators"
GENERATORS_TO_MASK=(
	rpi-swap-generator					# The most expensive generator on this image. Sizes
										# a swapfile whose unit is masked anyway
										# (rpi-resize-swap-file.service, below)
	netplan								# Shipped by netplan-generator, which cannot be
										# purged without taking network-manager with it. A
										# separate binary from /usr/sbin/netplan, so the
										# dpkg-divert further down does not cover it.
	zram-generator						# Produces nothing: host-memory-limit in
										# /etc/systemd/zram-generator.conf is 0, so it bails
	dpkg-limit							# No dpkg activity on a deployed unit
	systemd-gpt-auto-generator			# MBR disk; also covered by systemd.gpt_auto=0
	systemd-cryptsetup-generator		# No /etc/crypttab
	systemd-veritysetup-generator		# No /etc/veritytab
	systemd-integritysetup-generator	# No /etc/integritytab
	systemd-hibernate-resume-generator	# No resume= on cmdline, no hibernation
	systemd-rc-local-generator			# No /etc/rc.local
	systemd-run-generator				# Only acts on systemd.run= from cmdline
	systemd-ssh-generator				# ssh.socket ships with openssh-server; this
										# only adds sshd-unix-local.socket
	systemd-system-update-generator		# Only acts if /system-update exists
	systemd-tpm2-generator				# No TPM on a Pi 3A+
	systemd-getty-generator				# No console= left on cmdline; getty@tty1 is
										# pulled in by getty.target, not by this
	systemd-sysv-generator				# Every /etc/init.d script on a clean image has a
										# native unit. Verified by the guard below.
)

# Guard: masking systemd-sysv-generator silently stops any /etc/init.d script
# that has no native systemd unit. Verify before doing it, rather than
# discovering it when a service quietly stops starting after an apt upgrade.
sysv_orphans=""
for initd in /etc/init.d/*; do
	[[ -f "$initd" ]] || continue
	name="$(basename "$initd" .sh)"
	if ! systemctl cat "$name.service" >/dev/null 2>&1; then
		sysv_orphans+=" $name"
	fi
done
if [[ -n "$sysv_orphans" ]]; then
	echo -e "${YELLOW}SysV scripts without a native unit:$sysv_orphans${NC}"
	echo -e "${YELLOW}Not masking systemd-sysv-generator; they would stop starting${NC}"
	GENERATORS_TO_MASK=("${GENERATORS_TO_MASK[@]/systemd-sysv-generator}")
fi

sudo install -d -m 0755 "$GENERATOR_DIR"
for generator in "${GENERATORS_TO_MASK[@]}"; do
	[[ -n "$generator" ]] || continue
	if [[ ! -x "$GENERATOR_SRC_DIR/$generator" ]]; then
		echo -e "${YELLOW}Generator '$generator' not present, skipping${NC}"
		continue
	fi
	if [[ "$(readlink -f "$GENERATOR_DIR/$generator" 2>/dev/null || true)" == "/dev/null" ]]; then
		echo "Generator '$generator' already masked"
	elif sudo ln -sf /dev/null "$GENERATOR_DIR/$generator"; then
		echo "Generator '$generator' masked"
	else
		echo -e "${YELLOW}Warning: failed to mask generator '$generator'${NC}"
		FAILURES=$((FAILURES + 1))
	fi
done

# -----------------------------------
# Trim udev rules that block workers
# -----------------------------------
# mtp-probe opens every USB device and issues control transfers to ask whether it
# is an MTP device. Each probe blocks a udev worker while block devices queue
# behind it. Oradio has no MTP devices.
if [[ -e /usr/lib/udev/rules.d/69-libmtp.rules ]]; then
	sudo ln -sf /dev/null /etc/udev/rules.d/69-libmtp.rules
	echo "Masked udev rules 69-libmtp.rules"
else
	echo -e "${YELLOW}udev rules 69-libmtp.rules not present, skipping${NC}"
fi

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
	systemd-binfmt.service			# No foreign binary formats are used. It triggers the
									# binfmt_misc automount and waits for it, holding
									# sysinit.target. Mask both or neither.
	proc-sys-fs-binfmt_misc.automount	# Triggered only by the above
	apt-daily.timer					# No daily apt index refresh
	apt-daily.service
	apt-daily-upgrade.timer			# No unattended upgrades
	apt-daily-upgrade.service
	fstrim.timer					# Not relevant for SD card
	dpkg-db-backup.timer			# No dpkg database backup needed
	man-db.timer					# No man page index on an appliance
	e2scrub_all.timer				# Only relevant for LVM-backed ext4
	e2scrub_reap.service
	rpi-resize-swap-file.service	# This device runs with NO swap - a deliberate choice
									# for a 512 MB appliance running one Python app.
									# To restore swap: set host-memory-limit in
									# /etc/systemd/zram-generator.conf, unmask the
									# zram-generator above, and unmask the zram module
									# load configured earlier in this script.
	rpi-zram-writeback.timer		# Writeback for a zram device that is never created
	rpi-eeprom-update.service		# Pi 3A+ has no bootloader EEPROM
	NetworkManager-wait-online.service	# Oradio detects network availability itself
	hciuart.service					# Bluetooth disabled in config.txt via disable-bt
	bluetooth.service
	keyboard-setup.service			# No keyboard attached
	console-setup.service			# No local console font/keymap needed
	sshswitch.service				# SSH comes from ssh.socket, not the /boot flag file;
									# also holds an After=boot-firmware.mount that would
									# trigger the automount during boot
	systemd-journal-flush.service	# Nothing to flush: Storage=volatile in the journald
									# drop-in below means journald never uses
									# /var/log/journal. It is ordered before
									# systemd-tmpfiles-setup.service on the chain into
									# sysinit.target.
									#
									# LOAD-BEARING PAIR: unmask this before setting
									# Storage=persistent for any reason. This unit is what
									# migrates the journal from /run to /var, so with it
									# masked persistent journalling silently does not
									# work - journald keeps writing to tmpfs and the log
									# is still lost on every reboot.
	dev-mqueue.mount				# POSIX message queues, unused
	sys-kernel-debug.mount			# debugfs, unused in the field
	sys-kernel-tracing.mount		# tracefs, unused in the field
									# These three run in parallel with the rest of early
									# boot, so what they cost is contention, not ordering.
									# COST: no ftrace or debugfs. Unmask the last two before
									# any kernel-level investigation.
)

# Note: alsa-state.service and alsa-restore.service are deliberately NOT masked.
# alsa-state saves the mixer state at shutdown, and that saved state is what
# 'alsactl restore' reads back. The softvol controls /etc/asound.conf declares
# (VolumeMPD, VolumeSpotCon1/2, VolumeSysSound) do not exist until they are
# restored, and librespot's ExecStartPre sets VolumeSpotCon1 with Restart=always
# behind it - without the controls that unit restarts forever.
# oradio.service does not depend on alsa-restore.service: it runs 'alsactl
# restore' itself from an ExecStartPre, after oradio-prestart.sh has confirmed
# the card exists.
#
# Note: systemd-random-seed.service is deliberately NOT masked. It is cheap and
# librespot needs credible randomness for TLS.
#
# Note: avahi-daemon is deliberately NOT masked. mDNS (.local) discovery is
# wanted on this device.
#
# Note: logrotate.timer is deliberately NOT masked. Oradio's own logs in
# /etc/logrotate.d/oradio rotate on a size threshold, which is only evaluated
# when the timer runs logrotate. Masking it lets those files grow without limit
# and eventually fill the SD card.

for unit in "${UNITS_TO_MASK[@]}"; do
	mask_unit "$unit"
done

# ---------------------------------------------
# Mask modprobe@ instances that do nothing here
# ---------------------------------------------
# Template instances, not unit files, so mask_unit's list-unit-files check
# cannot see them and would skip them with a misleading "not present".
#
# drm is already blocked by 'install drm /bin/true' in the blacklist above, so
# this unit modprobes a module that cannot load. efi_pstore has no meaning on a
# Pi. Both contend for CPU and I/O in the busiest part of early boot.
#
# Check what pulls them in before assuming this is the right layer:
#   systemctl list-dependencies --reverse modprobe@drm.service
# If it is systemd-vconsole-setup.service, mask that instead - it is pointless
# on a headless appliance and takes modprobe@drm with it.
for instance in modprobe@drm.service modprobe@efi_pstore.service; do
	if [[ "$(readlink -f "/etc/systemd/system/$instance" 2>/dev/null || true)" == "/dev/null" ]]; then
		echo "Unit instance '$instance' already masked"
	elif sudo ln -sf /dev/null "/etc/systemd/system/$instance"; then
		echo "Unit instance '$instance' masked"
	else
		echo -e "${YELLOW}Warning: failed to mask '$instance'${NC}"
		FAILURES=$((FAILURES + 1))
	fi
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

# ---------------------------------------------
# Scheduling priorities during the boot scramble
# ---------------------------------------------
# Past this point the boot is I/O bound rather than dependency bound: several
# services start close together and contend for one SD card on four cores.
#
# avahi-daemon spends most of its startup blocked, so deprioritising it costs
# nothing real while freeing I/O bandwidth for the Python interpreter's imports.
#
# Side effect: multi-user.target is gated by avahi-daemon, so the headline
# 'systemd-analyze' figure gets worse. Expected, and not the metric that
# matters here - see the header.
write_boot_dropin() {
	local unit="$1" content="$2"
	local dir="/etc/systemd/system/${unit}.d"

	# Same guard as mask_unit: a drop-in for a unit that is not on the system is
	# silent dead config, and systemd warns about it on every daemon-reload.
	if ! systemctl list-unit-files --no-legend -- "$unit" 2>/dev/null | grep -q .; then
		echo -e "${YELLOW}Unit '$unit' not present, skipping drop-in${NC}"
		return 0
	fi

	sudo install -d -m 0755 "$dir"
	if printf '%s\n' "$content" | sudo tee "${dir}/oradio-sched.conf" >/dev/null; then
		echo "Scheduling drop-in written for '$unit'"
	else
		echo -e "${YELLOW}Warning: failed to write drop-in for '$unit'${NC}"
		FAILURES=$((FAILURES + 1))
	fi
}

# Blocked most of its runtime; get out of the way of the app
write_boot_dropin avahi-daemon.service '[Service]
IOSchedulingClass=idle
Nice=10'

# The thing the user is actually waiting for
write_boot_dropin oradio.service '[Service]
Nice=-5
IOSchedulingClass=best-effort
IOSchedulingPriority=0'

# ------------------------------------------
# NetworkManager: skip the netplan round-trip
# ------------------------------------------
# Debian's NetworkManager integrates with netplan: at startup it runs "netplan
# generate", which in turn runs "systemctl daemon-reload". NM blocks on that
# child, and the reload leaves the systemd manager unresponsive while it runs.
#
# There are three separate netplan entry points, in three different packages,
# and none of them can be removed by purging:
#   1. The NM settings plugin      - lives inside network-manager itself
#   2. /usr/sbin/netplan           - netplan.io is a hard dep of network-manager
#   3. The systemd generator       - netplan-generator pulls in netplan.io
# Purging any of them takes NetworkManager with it (verified with
# 'apt-get -s purge'), so each is neutralised in place instead. Item 3 is handled
# by the generator masking section above; items 1 and 2 are handled here.
#
# Both are needed: either one alone is not enough.
NM_CONF="/etc/NetworkManager/conf.d/10-plugins.conf"
sudo install -d -m 0755 "$(dirname "$NM_CONF")"
sudo tee "$NM_CONF" >/dev/null <<'NMEOF'
# Oradio uses keyfile connection profiles only, stored in
# /etc/NetworkManager/system-connections. Restricting the settings plugin list
# drops the netplan and ifupdown plugins, neither of which has anything to read:
# /etc/netplan is empty and /etc/network/interfaces does not exist.
[main]
plugins=keyfile
NMEOF
sudo chmod 644 "$NM_CONF"
echo "Wrote $NM_CONF"

# Divert the binary so NM's exec fails with ENOENT and it skips the generate.
# NM already tolerates this: it tries /usr/local/sbin and /usr/local/bin first
# and handles both being absent.
#
# Idempotent: 'dpkg-divert --list' prints nothing when no diversion exists, and
# --add fails if one already does. The diversion survives netplan package
# upgrades, which install into the diverted path and stay unreachable.
if ! dpkg-divert --list /usr/sbin/netplan | grep -q .; then
	echo "Diverting /usr/sbin/netplan so NetworkManager skips 'netplan generate'..."
	if ! sudo dpkg-divert --local --rename --add /usr/sbin/netplan >/dev/null; then
		echo -e "${RED}Failed to divert /usr/sbin/netplan${NC}" >&2
		FAILURES=$((FAILURES + 1))
	fi
else
	echo "/usr/sbin/netplan already diverted"
fi

# ----------------------------
# Reduce journald write volume
# ----------------------------
# NOTE: Storage=volatile means the journal lives in RAM only, so there is no
# 'journalctl -b -1' after a field unit misbehaves. A deliberate trade against SD
# card wear.
#
# To reverse it, all three are required: set Storage=persistent here, create
# /var/log/journal, and unmask systemd-journal-flush.service in UNITS_TO_MASK
# above. That unit is what migrates the journal from /run to /var, so leaving it
# masked means journald keeps writing to tmpfs whatever Storage= says.
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/oradio.conf"
sudo mkdir -p "$(dirname "$JOURNALD_DROPIN")"
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=8M\n' | sudo tee "$JOURNALD_DROPIN" >/dev/null
echo "Configured volatile journal in $JOURNALD_DROPIN"

# Apply unit file and udev rule changes
sudo systemctl daemon-reload
sudo udevadm control --reload-rules

if [[ "$FAILURES" -gt 0 ]]; then
	echo -e "${RED}Boot time optimizations incomplete: $FAILURES step(s) failed${NC}" >&2
	exit 1
fi

echo -e "${GREEN}Boot time optimizations applied${NC}"
