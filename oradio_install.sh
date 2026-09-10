#!/usr/bin/env bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on January 19, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       2
# @email:         info@stichtingoradio.nl
# @status:        Development

########## INITIALIZE BEGIN ##########

# Fail fast on unset variables and on failures hidden inside a pipeline
# (e.g. `curl ... | python -c ...`). We deliberately do NOT use `set -e`:
# large parts of this script rely on checking a command's exit status
# with `if`/`||` and continuing (see INSTALL_ERROR below), which `set -e`
# would short-circuit in surprising ways.
set -uo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo -e "${RED}Aborting: This script requires bash${NC}"
	exit 1
fi

# Refuse to run as root. Nothing here needs it, as every privileged step calls
# sudo explicitly, and running as root breaks two things silently:
#   - install_resource renders PLACEHOLDER_USER and PLACEHOLDER_GROUP from
#     'id -un' and 'id -gn', so every unit would be installed with User=root,
#     and the '+' chown lines would hand each log file back to root.
#   - install.log is created by the tee below, owned by whoever runs this
#     script. It is the only log in the logrotate glob that no service chowns,
#     so a root-owned one silently stops logrotate - which runs under 'su' and
#     cannot truncate a root-owned file - from ever rotating it.
#
# Checked before the sudo block below, so 'raspi-config nonint do_sudo_pass' is
# not run on behalf of the wrong user.
if [ "$EUID" -eq 0 ]; then
	echo -e "${RED}Aborting: do not run this script as root${NC}"
	exit 1
fi

# Seconds to wait before rebooting
REBOOT_DELAY=3

# Transient unit that resumes this script after the reboot in the initial run.
# Created just before rebooting and removed again by the '--continue' pass, so
# it exists only for the one boot it is needed for.
CONTINUE_UNIT=oradio-install-continue.service
CONTINUE_UNIT_FILE="/etc/systemd/system/$CONTINUE_UNIT"

# Enable passwordless sudo (no password prompt running sudo)
# https://www.raspberrypi.com/documentation/computers/configuration.html#disable-sudo-password
if sudo -n true 2>/dev/null; then
	# Already-passwordless, so not treated as an error
	echo "Passwordless sudo already enabled"
elif ! grep -q '^do_sudo_pass' /usr/bin/raspi-config 2>/dev/null; then
	# Older raspi-config has no do_sudo_pass, so not treated as an error
	echo -e "${YELLOW}Warning: raspi-config has no do_sudo_pass, skipping${NC}"
elif ! sudo -p "Enter Oradio3 password: " raspi-config nonint do_sudo_pass 1; then
	echo -e "${RED}Aborting: Could not enable passwordless sudo${NC}"
	exit 1
fi

# Get the script path and name.
SCRIPT_PATH=$(cd "$(dirname "$(readlink -f "$BASH_SOURCE")")" && pwd)
SCRIPT_NAME=$(basename "$BASH_SOURCE")

# Working directory
cd "$SCRIPT_PATH" || { echo -e "${RED}Aborting: Failed to cd to $SCRIPT_PATH${NC}"; exit 1; }

# Validate constants.env: every non-blank, non-comment line must be a plain KEY=value assignment
while IFS= read -r LINE || [ -n "$LINE" ]; do
	# Skip blanks and comments
	[[ "$LINE" =~ ^[[:space:]]*(#|$) ]] && continue
	if ! [[ "$LINE" =~ ^[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:@-]*$ ]]; then
		echo -e "${RED}Aborting: malformed line in constants.env: '$LINE'${NC}"
		exit 1
	fi
done < "$SCRIPT_PATH/constants.env"

# Constants shared with the Python project (Main/constants.py)
set -a		# Mark variables for export
. "$SCRIPT_PATH/constants.env" || { echo -e "${RED}Aborting: Failed to load constants.env${NC}"; exit 1; }
set +a

# Names defined in constants.env, so install_resource can expand each one
# as PLACEHOLDER_<NAME> without needing to be edited when a constant is added
mapfile -t CONSTANT_NAMES < <(sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$SCRIPT_PATH/constants.env")

# Location of Oradio3 program
MAIN_PATH="$SCRIPT_PATH/Main"
# Location of log files
LOGGING_PATH="$SCRIPT_PATH/logging"
# Location of Oradio3 system sounds
SOUNDS_PATH="$SCRIPT_PATH/system_sounds"
# Location of files to install
RESOURCES_PATH="$SCRIPT_PATH/install_resources"

# Ensure logging directory exists
mkdir -p "$LOGGING_PATH" || { echo -e "${RED}Aborting: Failed to create directory $LOGGING_PATH${NC}"; exit 1; }

# Define log files; log files are created by their respective services
LOGFILE_USB="$LOGGING_PATH/usb.log"
LOGFILE_MPD="$LOGGING_PATH/mpd.log"
LOGFILE_BOOT="$LOGGING_PATH/boot.log"
LOGFILE_CRASH="$LOGGING_PATH/crash.log"
LOGFILE_INSTALL="$LOGGING_PATH/install.log"
LOGFILE_TRACEBACK="$LOGGING_PATH/traceback.log"

# Save the original stdout/stderr before redirecting, so the EXIT trap below
# can restore them whatever they were: a terminal on an interactive run, the
# journal under systemd, a pipe when invoked from another script.
exec 3>&1 4>&2

# Redirect script output to console and file
exec > >(tee -a "$LOGFILE_INSTALL") 2>&1

# When leaving this script stop redirection and wait until redirect process has finished.
# Restoring via the saved descriptors rather than /dev/tty is what makes the
# `wait` safe: it only returns once tee sees EOF, and tee only sees EOF once
# nothing still holds the write end of that pipe. Redirecting to /dev/tty
# instead fails whenever no terminal is attached (cron, a systemd unit,
# `ssh host script.sh < /dev/null`), stdout then stays on the pipe, and the
# script hangs here forever instead of exiting.
trap 'exec 1>&3 2>&4; wait' EXIT

# Script is for Raspberry Pi OS Lite (64bit)
TARGETOS="Debian GNU/Linux 13 (trixie)"
OSVERSION=$(lsb_release -a 2>/dev/null | grep "Description:" | cut -d$'\t' -f2)
if [ "$OSVERSION" != "$TARGETOS" ]; then
	echo -e "${RED}Aborting: Invalid OS version: $OSVERSION${NC}"
	# Stop with error flag
	exit 1
fi

# Clear flag indicating reboot required to complete the installation
unset REBOOT_NEEDED

# Clear flag indicating installation error
unset INSTALL_ERROR

# Install file replacing placeholders and execute follow-up commands.
#
#   install_resource SRC DST [CMD...]
#
#   - If "SRC.template" exists, it is rendered into SRC first, replacing
#     PLACEHOLDER_USER / PLACEHOLDER_GROUP / PLACEHOLDER_<PATH_VAR> tokens
#     with the current user/group and the path variables defined above
#     (MAIN_PATH, LOGGING_PATH, LOGFILE_*).
#   - SRC is copied to DST via sudo only if the two files differ, so
#     re-running this script is idempotent and quiet on unchanged files.
#   - Any trailing CMD arguments run via `sudo bash -c "CMD"` *after* a
#     successful copy (e.g. `chmod +x ...`, `systemctl enable ...`).
#     For executable shell scripts use install_script instead: it adds a
#     syntax check before the copy.
#   - Sets the global INSTALL_ERROR flag (rather than exiting immediately)
#     on any failure, so one bad resource doesn't abort the whole install;
#     the script checks INSTALL_ERROR once, near the end, and exits then.
#   - GOTCHA FOR FUTURE EDITS: a trailing CMD such as `FOO=1` does NOT set
#     FOO in the calling script — it runs inside a throwaway `sudo bash -c`
#     subshell and is discarded when that subshell exits. Don't use a
#     trailing CMD to set a flag like REBOOT_NEEDED; set that directly in
#     the caller based on install_resource's return value instead, e.g.:
#       install_resource "$SRC" "$DST" && REBOOT_NEEDED=true
function install_resource {
	if [ $# -lt 2 ]; then
		echo -e "${RED}Aborting: install_resource has too few arguments: '$*'${NC}"
		echo "Usage: $0 src dst"
		# Stop with error flag
		INSTALL_ERROR=1
		return 1
	fi

	local SRC=$1
	local DST=$2
	shift 2

	# Ensure destination directory exists
	local DST_DIR
	DST_DIR=$(dirname "$DST")
	if ! sudo mkdir -p "$DST_DIR"; then
		echo -e "${RED}Failed to create directory '$DST_DIR'${NC}"
		INSTALL_ERROR=1
		return 1
	fi

	if [ -f "$SRC.template" ]; then

		# Create by replacing placeholders
		cp "$SRC.template" "$SRC" || { echo -e "${RED}Failed to copy $SRC.template to $SRC${NC}"; INSTALL_ERROR=1; return 1; }

		# Replace placeholders. Combined into one sed invocation (instead of one
		# `sed -i` per substitution) to avoid re-opening/rewriting the file N times.
		local SED_ARGS=(-e "s/PLACEHOLDER_USER/$(id -un)/g" -e "s/PLACEHOLDER_GROUP/$(id -gn)/g")
		for VAR_NAME in MAIN_PATH LOGGING_PATH SOUNDS_PATH LOGFILE_USB LOGFILE_MPD \
			LOGFILE_BOOT LOGFILE_CRASH LOGFILE_INSTALL LOGFILE_TRACEBACK \
			"${CONSTANT_NAMES[@]}"; do
			local VALUE="${!VAR_NAME}"
			# Escape & because sed treats it specially in the replacement text
			local ESCAPED_VALUE
			ESCAPED_VALUE=$(echo "$VALUE" | sed 's/[&]/\\&/g')
			local PLACEHOLDER="PLACEHOLDER_${VAR_NAME}"
			# Use | as delimiter instead of / since paths contain /
			SED_ARGS+=(-e "s|$PLACEHOLDER|$ESCAPED_VALUE|g")
		done
		sed -i "${SED_ARGS[@]}" "$SRC" || { echo -e "${RED}Failed to render placeholders in $SRC${NC}"; INSTALL_ERROR=1; return 1; }
	fi

	# Install only if files differ
	if ! cmp -s "$SRC" "$DST"; then
		echo "Installing '$SRC' to '$DST'..."
		if ! sudo cp "$SRC" "$DST"; then
			echo -e "${RED}Failed to install '$DST'${NC}"
			INSTALL_ERROR=1
			return 1
		fi

		# Execute any extra commands (chmod, systemctl enable, etc). Each one is
		# checked independently and failures are recorded but don't stop the loop,
		# so a single bad follow-up command doesn't hide problems with the others.
		for CMD in "$@"; do
			echo "Executing: '$CMD'..."
			if ! sudo bash -c "$CMD"; then
				echo -e "${RED}Command failed: '$CMD'${NC}"
				INSTALL_ERROR=1
			fi
		done
	fi

	return 0
}

# ---------------------------------------------------------------------------
# install_script SRC DST [MODE]
# ---------------------------------------------------------------------------
# Wrapper around install_resource for executable shell scripts: syntax-check the
# source, then install it and set MODE (default 755).
#
# The check runs BEFORE the copy, deliberately. A broken script that reaches
# /usr/local/sbin is live immediately: usb-drive.sh is invoked by udev, and a
# failing remove leaves the stick mounted with its dirty bit set - so the script
# meant to repair that condition becomes the cause of it. Checking after the
# copy would report the problem without preventing it.
#
# When a .template exists that is what gets checked, since install_resource
# renders from it. Placeholder tokens are bare words and do not affect parsing.
#
# MODE is explicit rather than 'chmod +x' so the installed permissions do not
# depend on whatever bits the source file happened to carry.
function install_script {
	if [ $# -lt 2 ]; then
		echo -e "${RED}Aborting: install_script has too few arguments: '$*'${NC}"
		echo "Usage: $0 src dst [mode]"
		INSTALL_ERROR=1
		return 1
	fi

	local SRC=$1
	local DST=$2
	local MODE=${3:-755}
	local CHECK=$SRC

	[ -f "$SRC.template" ] && CHECK="$SRC.template"

	if ! bash -n "$CHECK"; then
		echo -e "${RED}Syntax error in '$CHECK'; not installing '$DST'${NC}"
		INSTALL_ERROR=1
		return 1
	fi

	install_resource "$SRC" "$DST" "chmod $MODE '$DST'"
}

########## INITIALIZE END ##########

if [ "${1:-}" != "--continue" ]; then

########## INITIAL RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Starting '$SCRIPT_NAME'${NC}"

########## OS PACKAGES BEGIN ##########

	# SHARED STATE: install and pkg-helper.sh read and write this same stamp,
	# so whichever runs first spares the others a redundant 'apt-get update'.
	# All three must agree on the path
	STATEDIR="/var/lib/oradio"
	STAMP_FILE="$STATEDIR/apt-update-stamp"
	MAX_AGE=$((6 * 3600))	# 6 hours in seconds

	sudo install -d -m 0755 "$STATEDIR"

	# Get last time the list was updated, 0 if never (or if the stamp file is
	# missing/corrupted — guard against a non-numeric value breaking the
	# arithmetic below, e.g. after a partial write or manual edit).
	last_update=0
	if [[ -f "$STAMP_FILE" ]] && read -r stamp < "$STAMP_FILE" 2>/dev/null; then
		if [[ "$stamp" =~ ^[0-9]+$ ]]; then
			last_update="$stamp"
		fi
	fi

	# Get time since last update
	current_time=$(date +%s)
	age=$((current_time - last_update))

	# Update lists if too old. Only report success (and only refresh the stamp)
	# once `apt-get update` has actually confirmed success — otherwise a transient
	# failure (e.g. no network) would get cached as "up to date" for up to MAX_AGE
	# and mask itself on the next run.
	if (( age > MAX_AGE )); then
		echo -e "${YELLOW}Package lists out of date, updating...${NC}"

		# Fetch the latest package lists, refreshing stale lists
		if sudo apt-get update; then
			# Save time lists were updated
			date +%s | sudo tee "$STAMP_FILE" >/dev/null
			echo -e "${GREEN}Package lists are up to date${NC}"
		else
			echo -e "${RED}Aborting: apt-get update failed (check network/repositories)${NC}"
			exit 1
		fi
	else
		echo -e "${GREEN}Package lists are up to date${NC}"
	fi
	# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

########## OS PACKAGES END ##########

########## ORADIO3 LINUX PACKAGES BEGIN ##########

#***************************************************************#
#   Add any additionally required packages to 'LINUX_PACKAGES'  #
#***************************************************************#
	LINUX_PACKAGES=(
		jq
		git
		mpd
		mpc
		caps
		lsof
		iptables
		dosfstools
		python3-gi
		python3-dev
		python3-dbus
		python3-jinja2
		python3-requests
		python3-watchdog
		python3-rpi-lgpio
	)

	# Cleared here so a package set installed or upgraded below can set it.
	unset REBUILD_PYTHON_ENV

	# Everything else goes through the one implementation of "install if missing,
	# upgrade if a newer candidate exists, confirm afterwards". pkg-helper.sh
	# writes the packages it actually installed or upgraded to ORADIO_PKG_CHANGED,
	# one per line, so this can tell what moved
	PKG_CHANGED_FILE="$(mktemp)"
	if ORADIO_PKG_CHANGED="$PKG_CHANGED_FILE" bash "$SCRIPT_PATH/tools/pkg-helper.sh" "${LINUX_PACKAGES[@]}"; then

		# Rebuild the virtual environment only when a python3-* package moved.
		# The venv is created with --system-site-packages, so it resolves
		# python3-gi, python3-dbus and the rest from the system interpreter;
		# replacing one of those underneath it is what makes a rebuild
		# necessary. mpd, caps and iptables are separate binaries the venv
		# never imports, and rebuilding for them cost several minutes on a Pi
		# for no effect
		mapfile -t CHANGED_PYTHON < <(grep '^python3-' "$PKG_CHANGED_FILE" || true)
		if [ "${#CHANGED_PYTHON[@]}" -gt 0 ]; then
			echo -e "${YELLOW}Python packages changed (${CHANGED_PYTHON[*]}): the virtual environment will be rebuilt${NC}"
			REBUILD_PYTHON_ENV=1
		fi

	else
		echo -e "${RED}Failed to install or upgrade the Oradio3 Linux packages${NC}"
		INSTALL_ERROR=1
	fi
	rm -f "$PKG_CHANGED_FILE"

	# Progress report
	echo -e "${GREEN}Oradio3 packages installed and up to date${NC}"

########## ORADIO3 LINUX PACKAGES END ##########

########## UV BEGIN ##########

	# 'uv' is a drop-in replacement for pip that resolves and installs an
	# entire package set in one pass, typically an order of magnitude faster
	# than pip on a Raspberry Pi. It is not in the Debian/Raspberry Pi OS
	# archives, so we install Astral's prebuilt aarch64 binary.
	#
	# /usr/local/bin (rather than ~/.local/bin) keeps uv reachable from sudo,
	# cron and systemd units, and survives a change of login user.
	#
	# UV_NO_MODIFY_PATH stops the installer appending PATH lines to root's
	# shell profile: /usr/local/bin is already on PATH, so there is nothing
	# to add. INSTALLER_NO_MODIFY_PATH is the older name for the same knob,
	# set as well so this keeps working across installer versions.
	#
	# NOTE: this pipes a remote, unpinned script into `sh`. If reproducibility
	# ever matters more than tracking the latest release, pin it by fetching a
	# specific version instead:
	#   https://astral.sh/uv/0.9.7/install.sh
	UV_BIN=/usr/local/bin/uv
	if [ ! -x "$UV_BIN" ]; then
		echo -e "${YELLOW}uv is missing: installing...${NC}"
		if ! curl -LsSf https://astral.sh/uv/install.sh | sudo env \
				UV_INSTALL_DIR=/usr/local/bin \
				UV_NO_MODIFY_PATH=1 \
				INSTALLER_NO_MODIFY_PATH=1 \
				sh; then
			echo -e "${RED}Aborting: uv installation failed${NC}"
			exit 1
		fi
		if [ ! -x "$UV_BIN" ]; then
			echo -e "${RED}Aborting: uv not found at $UV_BIN after install${NC}"
			exit 1
		fi
	fi

	# Progress report
	echo -e "${GREEN}$("$UV_BIN" --version) is installed${NC}"

########## UV END ##########

########## PYTHON BEGIN ##########

	# If needed, prepare python virtual environment including system site packages.
	# We also (re)create it when ~/.venv is absent: REBUILD_PYTHON_ENV is only
	# set when a python3-* apt package was actually installed or upgraded, so
	# on a re-run where every Linux package is already current but ~/.venv has
	# been removed, the `source` below would fail and every subsequent install
	# would silently target the system Python instead.
	if [ ! -f ~/.venv/bin/activate ] || [ -n "${REBUILD_PYTHON_ENV:-}" ]; then
		echo "Configuring Python virtual environment"
		python3 -m venv --system-site-packages ~/.venv || { echo -e "${RED}Aborting: Failed to create ~/.venv${NC}"; exit 1; }
	fi

	# Activate the python virtual environment in current environment.
	# Guarded: without `set -e`, a failed `source` is silently ignored and
	# every install below would target the system Python instead, which on
	# trixie fails with 'externally-managed-environment' (PEP 668). Because
	# this aborts, the commands below can rely on VIRTUAL_ENV being correct.
	source ~/.venv/bin/activate || { echo -e "${RED}Aborting: Failed to activate ~/.venv${NC}"; exit 1; }

	# Activate python virtual environment when logging in if not yet present
	ADDTOBASHRC="source ~/.venv/bin/activate"
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}" >> ~/.bashrc

	# Set paths to python scripts if not yet present.
	# Quoted so the path survives intact in ~/.bashrc even if SCRIPT_PATH
	# ever contains a space (it doesn't today, but nothing enforces that).
	ADDTOBASHRC="export PYTHONPATH=\"${SCRIPT_PATH}/Main:${SCRIPT_PATH}/module_test\""
	grep -qxF "${ADDTOBASHRC}" ~/.bashrc || echo "${ADDTOBASHRC}" >> ~/.bashrc

	# Progress report
	echo -e "${GREEN}Python virtual environment configured${NC}"

	# https://www.raspberrypi.com/documentation/computers/os.html#use-python-on-a-raspberry-pi

#***************************************************************#
#   Add any additionally required Python modules to 'PYTHON'    #
#***************************************************************#
	PYTHON_PACKAGES=(
		nmcli
		fastapi
		uvicorn
		python-mpd2
		python-multipart
		concurrent-log-handler
	)

	# Ensure Python packages are installed and up-to-date.
	#
	# A single `uv pip install --upgrade` resolves and installs the whole set
	# in one pass. This replaces the earlier `pip list` + `pip list --outdated`
	# + per-package `pip install` approach, which was slow for two reasons:
	# `--outdated` queries the index for every package in the environment (not
	# just ours), and each install paid its own interpreter startup, index
	# round-trip and resolve. uv performs the same "already current?" check
	# itself, so the pre-flight query is redundant.
	#
	# uv targets the environment named by VIRTUAL_ENV, which the guarded
	# `source` above guarantees is set and correct.
	#
	# NOTE: uv always builds via PEP 517, so no --use-pep517 flag exists or
	# is needed. See https://peps.python.org/pep-0517/

	# Snapshot versions before installing, so we can report per package below
	# what was installed, upgraded or already current. Unlike the
	# `pip list --outdated` this replaces, `uv pip list` only reads local
	# metadata - it makes no network requests and costs milliseconds.
	BEFORE_JSON=$("$UV_BIN" pip list --format=json 2>/dev/null) || BEFORE_JSON='[]'

	echo "Installing/upgrading Python packages..."
	if ! "$UV_BIN" pip install --upgrade "${PYTHON_PACKAGES[@]}"; then
		# Retry one at a time so a single unresolvable or broken package is
		# named in the log, instead of the whole batch failing anonymously.
		# This only runs when the fast path failed, so a healthy install
		# still costs exactly one uv invocation.
		echo -e "${YELLOW}Batch install failed: retrying individually...${NC}"
		for package in "${PYTHON_PACKAGES[@]}"; do
			if ! "$UV_BIN" pip install --upgrade "$package"; then
				echo -e "${RED}Failed to install $package${NC}"
				INSTALL_ERROR=1
			fi
		done
	fi

	# Report the outcome per package by diffing the before/after snapshots.
	# Names are normalised per PEP 503 (lowercased, '.' and '_' folded to '-')
	# because pip reports e.g. 'python_mpd2' where PYTHON_PACKAGES says
	# 'python-mpd2'; comparing raw names would flag it missing on every run.
	AFTER_JSON=$("$UV_BIN" pip list --format=json 2>/dev/null) || AFTER_JSON='[]'
	PKGS_JSON=$(printf '%s\n' "${PYTHON_PACKAGES[@]}" | jq -Rnc '[inputs]')

	# `-n` is required: every input arrives via --argjson, so without it jq
	# would block waiting on stdin.
	# Read via process substitution rather than a pipe, so the loop runs in
	# this shell and an INSTALL_ERROR set below actually survives.
	while IFS=$'\t' read -r STATUS NAME DETAIL; do
		case "$STATUS" in
			INSTALLED) echo -e "${GREEN}$NAME $DETAIL installed${NC}" ;;
			UPDATED)   echo -e "${GREEN}$NAME updated: $DETAIL${NC}" ;;
			CURRENT)   echo "$NAME $DETAIL is up-to-date" ;;
			MISSING)   echo -e "${RED}$NAME is NOT installed${NC}"; INSTALL_ERROR=1 ;;
		esac
	done < <(jq -rn \
			--argjson before "$BEFORE_JSON" \
			--argjson after "$AFTER_JSON" \
			--argjson pkgs "$PKGS_JSON" '
		def norm: ascii_downcase | gsub("[._]"; "-");
		def index: map({ (.name | norm): .version }) | add // {};
		($before | index) as $b | ($after | index) as $a |
		$pkgs[] | . as $name | ($name | norm) as $key |
		if   ($a[$key] // null) == null then "MISSING\t\($name)\t"
		elif ($b[$key] // null) == null then "INSTALLED\t\($name)\t\($a[$key])"
		elif $b[$key] != $a[$key]       then "UPDATED\t\($name)\t\($b[$key]) -> \($a[$key])"
		else                                 "CURRENT\t\($name)\t\($a[$key])"
		end')

	# Progress report
	echo -e "${GREEN}Python packages installed and up-to-date${NC}"

########## PYTHON END ##########

########## BOOT OPTIONS BEGIN ##########

	# install_resource returns 0 if it installed something new (or if the
	# file was already up to date — see its "differ" check), non-zero on failure.
	if ! cmp -s "$RESOURCES_PATH/config.txt" /boot/firmware/config.txt 2>/dev/null; then
		if install_resource "$RESOURCES_PATH/config.txt" /boot/firmware/config.txt; then
			REBOOT_NEEDED=true
		fi
	fi

	# Progress report
	echo -e "${GREEN}Boot options configured${NC}"

########## BOOT OPTIONS END ##########

	# Reboot if required for activation
	if [ -v REBOOT_NEEDED ]; then
		# Resume after the reboot from a one-shot systemd unit.
		#
		# This used to append a '--continue' line to ~/.bashrc and switch the
		# console to auto-login, which had three problems. That line fires in
		# EVERY interactive shell until it is removed, so anyone who logged in
		# over SSH while the console pass was running started a second,
		# concurrent install. The output went to tty1, where the person doing
		# the install over SSH could not see it anyway. And auto-login stayed
		# on if the second pass never started, leaving the device in a state
		# the installer chose and never announced.
		#
		# A unit has none of that: it runs exactly once, needs no login and no
		# console, and touches nothing outside its own unit file.
		#
		# User=/Group= are the invoking user, for the same reason this script
		# refuses to run as root: everything it creates has to stay owned by
		# that user. sudo still works from here because the initial run enabled
		# passwordless sudo before reaching this point.
		#
		# TimeoutStartSec=infinity is load-bearing. A Type=oneshot unit is
		# killed after 90 seconds by default, and the '--continue' pass takes
		# minutes -- it would be shot halfway through configuring the device.
		sudo tee "$CONTINUE_UNIT_FILE" >/dev/null <<-EOF
			[Unit]
			Description=Continue Oradio installation after reboot
			After=multi-user.target network-online.target
			Wants=network-online.target

			[Service]
			Type=oneshot
			User=$(id -un)
			Group=$(id -gn)
			WorkingDirectory=$SCRIPT_PATH
			ExecStart=/bin/bash $SCRIPT_PATH/$SCRIPT_NAME --continue
			TimeoutStartSec=infinity

			[Install]
			WantedBy=multi-user.target
		EOF

		sudo systemctl daemon-reload
		if ! sudo systemctl enable "$CONTINUE_UNIT"; then
			echo -e "${RED}Aborting: could not enable $CONTINUE_UNIT${NC}"
			echo -e "${RED}Not rebooting: the installation would not resume${NC}"
			exit 1
		fi

		# This script will automatically be started after reboot
		echo -e "${YELLOW}Reboot required: Installation will continue after reboot in ${REBOOT_DELAY}s${NC}"
		echo -e "${YELLOW}Follow it with: journalctl -fu $CONTINUE_UNIT${NC}"
		echo -e "${YELLOW}or with: tail -f $LOGFILE_INSTALL${NC}"
		sleep "$REBOOT_DELAY"

		# Ensure buffered data is written to files
		sync

		# Let systemd do controlled reboot
		sudo systemctl reboot
	fi

########## INITIAL RUN END ##########

else # Execute if this script IS automatically started after reboot

########## REBOOT RUN BEGIN ##########

	# Progress report
	echo -e "${GREEN}$(date +'%Y-%m-%d %H:%M:%S'): Continueing after reboot${NC}"

	# Take the resume unit out of service. Done FIRST, before anything below
	# can fail: whatever happens to the rest of this pass, the device must not
	# come up trying to resume an installation again at the next boot.
	#
	# Failures are reported, not fatal. A leftover unit re-runs an installation
	# that is idempotent anyway, which is a smaller problem than refusing to
	# finish the one already in progress.
	if [ -f "$CONTINUE_UNIT_FILE" ]; then
		sudo systemctl disable "$CONTINUE_UNIT" || echo -e "${YELLOW}Warning: could not disable $CONTINUE_UNIT${NC}"
		sudo rm -f "$CONTINUE_UNIT_FILE" || echo -e "${YELLOW}Warning: could not remove $CONTINUE_UNIT_FILE${NC}"
		sudo systemctl daemon-reload
	fi

########## REBOOT RUN END ##########

fi

########## CONFIGURATION BEGIN ##########

# Remove artefacts left behind by earlier Oradio versions.
#
# HERE, and not in the INITIAL RUN block above, for two reasons.
#
# Everything the cleanup has to precede is in this section: optimize_boot_time,
# the udev rules, the unit files and the 'systemctl enable' calls. Standing
# immediately in front of them is the tightest guarantee that an old unit is
# gone before the one replacing it is put in place.
#
# More importantly, this section is never interrupted by the reboot. The INITIAL
# RUN block can end in one, and a cleanup placed there would leave the device
# booting once with the old units removed and the new ones not yet installed --
# a boot with, for instance, no USB preparation at all. Removal and replacement
# belong in the same uninterrupted run.
#
# This section runs exactly once per install: the initial pass either falls
# through to it or reboots before reaching it, and the '--continue' pass that
# follows a reboot lands here directly.
#
# install_resource only copies when the file differs, so its trailing-command
# form would run the cleanup only on the install that changes the script. The
# cleanup is idempotent and quiet on a clean device, so it is invoked
# explicitly instead.
#
# Mode 755, not 700 like oradio-crash.sh: this one runs as the Oradio user and
# calls sudo itself, so the user has to be able to execute it -- both from here
# and by hand with --dry-run.
install_script "$RESOURCES_PATH/cleanup_old_versions.sh" /usr/local/sbin/cleanup_old_versions.sh 755
# No progress report: the script prints its own, and unlike a fixed line here it
# distinguishes "nothing found" from "removed".
/usr/local/sbin/cleanup_old_versions.sh

# Minimize Oradio boot time
bash "$RESOURCES_PATH/optimize_boot_time.sh"

# Activate wireless interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#wlan-country-2
sudo raspi-config nonint do_wifi_country NL		# Implicitly activates wifi

# Change hostname and hosts mapping. Use explicit hostname to avoid confusion.
ORADIO_HOSTNAME=oradio
sudo hostnamectl set-hostname "$ORADIO_HOSTNAME"
sudo sed -i "s/^127.0.1.1.*/127.0.1.1\t${ORADIO_HOSTNAME}/g" /etc/hosts

# Set Top Level Domain (TLD) to 'local', enabling access via http://oradio.local
sudo sed -i "s/^.domain-name=.*/domain-name=local/g" /etc/avahi/avahi-daemon.conf

# Allow mDNS on wired and wireless interfaces
sudo sed -i "s/^#allow-interfaces=.*/allow-interfaces=eth0,wlan0/g" /etc/avahi/avahi-daemon.conf

# Progress report
echo -e "${GREEN}Wifi is enabled and network domain is set to '${ORADIO_HOSTNAME}.local'${NC}"

# Comment any active AcceptEnv lines in main config
sudo sed -Ei '/^[[:space:]]*AcceptEnv/ s/^[[:space:]]*/#/' /etc/ssh/sshd_config
# reload sshd with changed config
sudo systemctl reload ssh
# Set safe system-wide defaults
sudo update-locale LANG=C.UTF-8 LC_CTYPE=C.UTF-8
# Progress report
echo -e "${GREEN}Fix installed for \"-bash: warning: setlocale ...\" when SSH-ing from macOS${NC}"

# Get date and time of git last update
gitdate=$(git log -1 --format=%cd --date=format:'%Y-%m-%d-%H-%M-%S')
# Get info about installed Oradio3 version. Prefer an exact tag; fall back to
# "branch @ short-hash" if this checkout isn't on a tag. `--show-current`
# (rather than parsing `git branch`'s `* ` marker) is used so this keeps
# working even if the repo ever has many local branches.
gitinfo="Release '$(git describe --tags 2>&1)'"
if [ $? -gt 0 ]; then
	gitinfo="Branch '$(git branch --show-current)' @ $(git log --pretty='format:%h' -1)"
fi
# Generate new sw version info
sudo bash -c 'cat << EOL > /var/log/oradio_sw_version.log
{
    "dtstamp": "$1",
    "gitinfo": "$2"
}
EOL' -- "$gitdate" "$gitinfo"
# Progress report
echo -e "${GREEN}Oradio software version log configured${NC}"

# Show Raspberry Pi serial number and SW version on login
if ! grep -q "Serial number: " /etc/bash.bashrc; then
	sudo bash -c 'cat << EOL >> /etc/bash.bashrc 
echo "--------------------------------------------------"
# Get Oradio3 serial number and software version
echo "Serial number: \$(vcgencmd otp_dump | grep "28:" | cut -c 4-)"
if [ -f /var/log/oradio_sw_version.log ]; then
	echo "SW version: \$(cat /var/log/oradio_sw_version.log | jq -r ".gitinfo")"
else
	echo "SW version: Unknown (No '"'"'oradio_sw_version.log'"'"')"
fi
echo "--------------------------------------------------"
EOL'
fi

# Install udev rules triggering when inserting/removing ORADIO USB drive
install_resource "$RESOURCES_PATH/99-local.rules" /etc/udev/rules.d/99-local.rules
# Configure the USB service triggered by udev rules
install_resource "$RESOURCES_PATH/usb-drive@.service" /etc/systemd/system/usb-drive@.service
# Install the USB mount/unmount script used by the system service
install_script "$RESOURCES_PATH/usb-drive.sh" /usr/local/sbin/usb-drive.sh
# Configure the USB boot service to start on boot
install_resource "$RESOURCES_PATH/usb-drive-boot.service" /etc/systemd/system/usb-drive-boot.service 'systemctl enable usb-drive-boot.service'
# Progress report
echo -e "${GREEN}USB functionality loaded and configured. System automounts USB drives on '$USB_MOUNT_POINT'${NC}"

# Activate i2c interface
# https://www.raspberrypi.com/documentation/computers/configuration.html#i2c-nonint
sudo raspi-config nonint do_i2c 0	# 0: enable
# Install i2c modules
install_resource "$RESOURCES_PATH/modules" /etc/modules
# Adjust device permissions
install_resource "$RESOURCES_PATH/oradio-dev.conf" /etc/tmpfiles.d/oradio-dev.conf
# Progress report
echo -e "${GREEN}i2c and device permissions configured${NC}"

# Install audio configuration, set volume to reasonable level, play silence to activate
install_resource "$RESOURCES_PATH/asound.conf" /etc/asound.conf \
	'amixer -c DigiAMP cset name="Digital Playback Volume" 120'\
	'aplay -D MPD_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1' \
	'aplay -D SysSound_in /dev/zero -f FLOAT_LE -c 2 -r 44100 -d 1'
# Configure MPD
install_resource "$RESOURCES_PATH/mpd.conf" /etc/mpd.conf
# Install empty MPD database.
#
# Without db_file present MPD scans music_directory at startup. auto_update "no"
# does not prevent that - it governs updates after startup only. Loading a valid
# empty database instead leaves indexing under Oradio's control, which triggers
# it when the USB is mounted.
#
# Installed only when absent: MPD rewrites this file as those updates run, so
# install_resource's "copy when different" would replace a populated index with
# the empty one on every re-run of this script.
if [ -f /var/lib/mpd/tag_cache ]; then
	echo "MPD database already present, leaving it alone"
else
	install_resource "$RESOURCES_PATH/mpd.database" /var/lib/mpd/tag_cache
fi

# The shipped database is only accepted while it matches MPD. Two things can
# invalidate it, and both fail the same silent way - MPD discards it and rescans
# the whole stick at every boot, competing with Oradio's startup:
#
#   format:          MPD's on-disk database format, bumped by an MPD upgrade.
#                    pkg-helper.sh upgrades mpd whenever a newer candidate exists.
#   tag: lines       must match metadata_to_use in mpd.conf, case-insensitively.
#
# Checked here rather than left to be discovered as a slow boot. A mismatch is
# not fatal, so this warns and records a failure rather than aborting the install.
DB_TAGS="$(zcat "$RESOURCES_PATH/mpd.database" 2>/dev/null | sed -n 's/^tag: //p' | tr 'A-Z' 'a-z' | sort | paste -sd, -)"
CFG_TAGS="$(sed -n 's/^metadata_to_use[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' /etc/mpd.conf | tr -d ' ' | tr 'A-Z' 'a-z' | tr ',' '\n' | sort | paste -sd, -)"

if [ -z "$DB_TAGS" ] || [ -z "$CFG_TAGS" ]; then
	echo -e "${YELLOW}Could not compare MPD database tags ('$DB_TAGS') with mpd.conf ('$CFG_TAGS')${NC}"
	INSTALL_ERROR=1
elif [ "$DB_TAGS" != "$CFG_TAGS" ]; then
	echo -e "${YELLOW}MPD database tags '$DB_TAGS' do not match metadata_to_use '$CFG_TAGS'${NC}"
	echo -e "${YELLOW}MPD will discard the database and rescan the USB at every boot${NC}"
	echo -e "${YELLOW}Regenerate mpd.database, or revert metadata_to_use in mpd.conf${NC}"
	INSTALL_ERROR=1
else
	echo "MPD database tags match mpd.conf ($CFG_TAGS)"
fi

# Format version the shipped database was written for, against the MPD that is
# actually installed. Informational: MPD validates on 'format:', and the version
# string is only a provenance signal, but a large gap is worth seeing.
DB_FORMAT="$(zcat "$RESOURCES_PATH/mpd.database" 2>/dev/null | sed -n 's/^format: //p')"
DB_MPD_VERSION="$(zcat "$RESOURCES_PATH/mpd.database" 2>/dev/null | sed -n 's/^mpd_version: //p')"
MPD_VERSION="$(dpkg-query -W -f='${Version}' mpd 2>/dev/null || true)"
echo "MPD database format $DB_FORMAT, written by MPD $DB_MPD_VERSION; installed MPD ${MPD_VERSION:-unknown}"
# Configure the MPD service to start on boot
install_resource "$RESOURCES_PATH/mpd-oradio.conf" /etc/systemd/system/mpd.service.d/oradio.conf 'systemctl enable mpd.service'
# Progress report
echo -e "${GREEN}Audio installed and configured${NC}"

# Configure log file rotation to limit logfile size
install_resource "$RESOURCES_PATH/logrotate.conf" /etc/logrotate.d/oradio
# Configure rotation timer to limit logfile size
install_resource "$RESOURCES_PATH/logrotate-timer-override.conf" /etc/systemd/system/logrotate.timer.d/oradio.conf
# Progress report
echo -e "${GREEN}Log files rotation configured${NC}"

# Install the about script
install_script "$RESOURCES_PATH/about" /usr/local/bin/about
# Progress report
echo -e "${GREEN}Support tools installed${NC}"

# Configure the oradio crash handling script
install_script "$RESOURCES_PATH/oradio-crash.sh" /usr/local/sbin/oradio-crash.sh 700
# Configure the oradio crash handling service
install_resource "$RESOURCES_PATH/oradio-crash.service" /etc/systemd/system/oradio-crash.service
# Configure the oradio prestart script
install_script "$RESOURCES_PATH/oradio-prestart.sh" /usr/local/sbin/oradio-prestart.sh
# Configure the oradio service to start on boot
install_resource "$RESOURCES_PATH/oradio.service" /etc/systemd/system/oradio.service 'systemctl enable oradio.service'
# Progress report
echo -e "${GREEN}Start Oradio3 on boot configured${NC}"

# Stop if any installation failed. Checked once, here, rather than exiting
# immediately at each failure point above, so a single bad resource/package
# doesn't prevent the rest of a mostly-good install from completing — the
# operator gets one clear summary of everything that went wrong instead of
# having to re-run the script repeatedly to discover problems one at a time.
if [ -v INSTALL_ERROR ]; then
	echo -e "${RED}Aborting: Installation completed with errors${NC}"
	# Stop with error flag
	exit 1
fi

########## CONFIGURATION END ##########

# Progress report
echo -e "${GREEN}Installation completed. Rebooting to start Oradio3 in ${REBOOT_DELAY}s${NC}"
sleep "$REBOOT_DELAY"

# Ensure buffered data is written to files
sync

# Let systemd do controlled reboot
sudo systemctl reboot
