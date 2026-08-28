#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# @Purpose:  Install or remove the USB boot-check test harness
#
# The harness records one row per boot describing whether the ORADIO stick was
# mounted, which path mounted it, and whether the previous shutdown was clean.
# It is a DEVELOPMENT AND TEST tool, not part of a shipped Oradio: it adds a
# unit to every boot and writes to the logging directory. Remove it before
# building a production image.
#
# Usage:
#   ./usb-boot-check_install.sh install     install and enable
#   ./usb-boot-check_install.sh remove      stop, disable and delete
#   ./usb-boot-check_install.sh status      show what is currently installed
#   ./usb-boot-check_install.sh purge       remove, and delete collected results
#
# 'remove' deliberately KEEPS the CSV, trace and diag files: they are the
# output of a test run that may have taken days of reboots to collect, and
# deleting them as a side effect of uninstalling the collector would be a poor
# trade. Use 'purge' to discard them explicitly.
#

# Stop script on command errors, unset variables and failed pipes
set -o errexit -o nounset -o pipefail

# Color definitions
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

# Report and exit 1 on any unhandled command failure
trap 'echo -e "${RED}Failed at line ${LINENO}${NC}" >&2; exit 1' ERR

readonly SCRIPT_SRC="usb-boot-check.sh"
readonly UNIT_SRC="usb-boot-check.service"
readonly SCRIPT_DST="/usr/local/sbin/usb-boot-check.sh"
readonly UNIT_DST="/etc/systemd/system/usb-boot-check.service"
readonly UNIT_NAME="usb-boot-check.service"

# Must match the RESULTS/TRACE/CLEAN_MARKER defaults in usb-boot-check.sh.
readonly RESULTS="/home/pi/Oradio3/usb-boot-check.csv"
readonly TRACE="/home/pi/Oradio3/usb-boot-check.trace"
readonly DIAG="/home/pi/Oradio3/usb-boot-check.diag"
readonly MARKER="/home/pi/Oradio3/usb-boot-check.clean"

# Ownership for the result files. The unit runs as root, so without this the
# CSV ends up root-owned inside the pi user's directory and anything else in
# Oradio3 that tries to touch it fails.
readonly OWNER="pi:pi"

# Refuse to run as root directly: the script uses sudo deliberately so that
# file ownership stays correct and mistakes are easier to trace
if [[ "${EUID}" -eq 0 ]]; then
	echo -e "${RED}Run this script as a normal user; it calls sudo itself${NC}" >&2
	exit 1
fi

# Run from the directory holding this script, so the source files are found
# whether it is invoked as ./x.sh, bash x.sh, or by absolute path.
cd "$(dirname "$(readlink -f "$0")")"

# ----------------
# Helper functions
# ----------------

# True if the unit is known to systemd at all. 'systemctl is-enabled' on an
# unknown unit returns a non-zero exit under errexit, so probe the unit file
# list instead of trusting a status query.
unit_exists() {
	[[ -f "$UNIT_DST" ]]
}

do_install() {
	local f
	for f in "$SCRIPT_SRC" "$UNIT_SRC"; do
		if [[ ! -f "$f" ]]; then
			echo -e "${RED}Missing source file '$f' in $(pwd)${NC}" >&2
			exit 1
		fi
	done

	# Catch a broken script before it is wired into every boot. A syntax error
	# installed and enabled would fail silently once per boot, and with
	# journald Storage=volatile the evidence is gone after the next reboot.
	if ! bash -n "$SCRIPT_SRC"; then
		echo -e "${RED}'$SCRIPT_SRC' has a syntax error; not installing${NC}" >&2
		exit 1
	fi

	echo "Installing $SCRIPT_DST"
	sudo install -m 755 -o root -g root "$SCRIPT_SRC" "$SCRIPT_DST"

	echo "Installing $UNIT_DST"
	sudo install -m 644 -o root -g root "$UNIT_SRC" "$UNIT_DST"

	sudo systemctl daemon-reload
	sudo systemctl enable "$UNIT_NAME"

	# Create the result files up front, owned by the user rather than root.
	# The script appends as root; ownership only matters for reading, editing
	# and deleting them afterwards without sudo.
	sudo touch "$RESULTS" "$TRACE"
	sudo chown "$OWNER" "$RESULTS" "$TRACE"

	# Deliberately NOT started here. The whole point of the harness is to
	# observe a boot, and a manual start now would write a row describing a
	# system that has been up for however long, mislabelled as boot data.
	echo -e "${GREEN}Installed and enabled.${NC}"
	echo "Reboot to collect the first row, then: cat $RESULTS"
}

do_remove() {
	local removed=0

	if unit_exists; then
		# Order matters: disable before deleting the unit file, or systemd
		# cannot resolve the [Install] section and the wants/ symlink is left
		# behind as a dangling link that shows up in every later boot's logs.
		echo "Disabling $UNIT_NAME"
		sudo systemctl disable "$UNIT_NAME" || true

		# stop() runs ExecStop, which writes the clean-shutdown marker. Removed
		# below, otherwise the next boot after uninstalling would see a stale
		# marker if the harness is ever reinstalled.
		sudo systemctl stop "$UNIT_NAME" || true

		echo "Removing $UNIT_DST"
		sudo rm -f "$UNIT_DST"
		removed=1
	else
		echo -e "${YELLOW}Unit not installed, skipping${NC}"
	fi

	if [[ -f "$SCRIPT_DST" ]]; then
		echo "Removing $SCRIPT_DST"
		sudo rm -f "$SCRIPT_DST"
		removed=1
	else
		echo -e "${YELLOW}Script not installed, skipping${NC}"
	fi

	sudo rm -f "$MARKER"

	# Clear any failed state left behind, so 'systemctl --failed' is clean
	# after uninstalling rather than referencing a unit that no longer exists.
	sudo systemctl daemon-reload
	sudo systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true

	if [[ "$removed" -eq 1 ]]; then
		echo -e "${GREEN}Removed.${NC}"
	else
		echo -e "${YELLOW}Nothing to remove.${NC}"
	fi

	if [[ -s "$RESULTS" || -s "$TRACE" ]]; then
		echo "Results kept:"
		[[ -s "$RESULTS" ]] && echo "  $RESULTS ($(($(wc -l < "$RESULTS") - 1)) rows)"
		[[ -s "$TRACE"   ]] && echo "  $TRACE"
		[[ -s "$DIAG"    ]] && echo "  $DIAG"
		echo "Use '$0 purge' to delete them."
	fi
}

do_purge() {
	do_remove
	echo "Deleting collected results"
	sudo rm -f "$RESULTS" "$TRACE" "$DIAG"
	echo -e "${GREEN}Purged.${NC}"
}

do_status() {
	echo "Script : $([[ -f "$SCRIPT_DST" ]] && echo "$SCRIPT_DST" || echo "not installed")"
	echo "Unit   : $([[ -f "$UNIT_DST"   ]] && echo "$UNIT_DST"   || echo "not installed")"

	if unit_exists; then
		echo "Enabled: $(systemctl is-enabled "$UNIT_NAME" 2>/dev/null || echo unknown)"
		echo "State  : $(systemctl is-active "$UNIT_NAME" 2>/dev/null || echo inactive)"
	fi

	if [[ -s "$RESULTS" ]]; then
		echo "Results: $RESULTS ($(($(wc -l < "$RESULTS") - 1)) rows)"
		# Distinct boot_ids is the number that matters: it is the count of
		# boots actually observed, which is not the same as the row count if
		# the script was ever run by hand as well.
		echo "Boots  : $(awk -F, 'NR>1 {print $2}' "$RESULTS" | sort -u | wc -l) distinct boot_id(s)"
		echo "Verdict: $(awk -F, 'NR>1 {print $5}' "$RESULTS" | sort | uniq -c | tr '\n' ' ')"
	else
		echo "Results: none collected"
	fi
}

case "${1:-}" in
	install) do_install ;;
	remove)  do_remove ;;
	purge)   do_purge ;;
	status)  do_status ;;
	*)
		echo "Usage: $0 {install|remove|purge|status}"
		echo
		echo "  install  copy files, enable the unit (does not start it)"
		echo "  remove   stop, disable and delete; KEEPS collected results"
		echo "  purge    remove, and delete the CSV/trace/diag as well"
		echo "  status   show what is installed and how much data exists"
		exit 1
		;;
esac
