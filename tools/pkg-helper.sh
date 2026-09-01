#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on November 2, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       2
# @email:         info@stichtingoradio.nl
# @status:        Development
# @purpose:       Install/update packages passed as arguments
#
#       Usage: pkg-helper.sh <package> <package> ...
#
#       Run as pi, not root: every privileged step calls sudo on its own.
#
#       The network is only touched when there is something to do. A run in
#       which every package is already installed and the package lists are
#       fresh makes no network calls at all and cannot fail on a Pi that is
#       offline. That matters because USB_sync.sh calls this before touching
#       the USB, and a health check on a broken stick must not be blocked by
#       a missing internet connection.

##### Initialize #####################

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

#---------- Ensure using bash ----------

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo -e "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Parse arguments into list of packages to install/upgrade ----------

# Exit if no packages provided
if [ "$#" -eq 0 ]; then
	echo -e "${RED}No packages provided${NC}"
	echo "Usage: ${0##*/} <package> <package> ..."
	exit 1
fi

# Required packages
REQUIRED_PACKAGES=("$@")

#---------- State directory ----------

# Our own directory, not /var/lib/apt: a stray file in apt's state directory is
# the kind of thing a future apt version or a cleanup tool decides to remove.
#
# SHARED STATE: install and oradio_install.sh read and write this same stamp,
# so that whichever runs first spares the others a redundant 'apt-get update'.
# All three must agree on the path — change it in one place only and the
# coordination silently stops working
STATEDIR="/var/lib/oradio"
STAMP_FILE="$STATEDIR/apt-update-stamp"
APT_LOCK="$STATEDIR/apt.lock"
MAX_AGE=$((6 * 3600))	# 6 hours in seconds

sudo install -d -m 0755 "$STATEDIR"

# Optional: when ORADIO_PKG_CHANGED is set to a writable path, the names of the
# packages this run installed or upgraded are written there, one per line (the
# file is truncated first, so it is empty when nothing changed). Lets a caller
# react to a change - oradio_install.sh rebuilds its Python venv - without this
# script having to signal through its exit code, which callers running under
# 'set -e' would treat as a failure
if [ -n "${ORADIO_PKG_CHANGED:-}" ]; then
	: > "$ORADIO_PKG_CHANGED"
fi

#---------- Helpers ----------

# Connectivity test, evaluated at most once and only when something actually
# needs downloading. --max-time bounds it: curl's default connect timeout is
# over two minutes, and a captive portal or a blackholing DNS server will use
# every second of it. Tests the apt mirror rather than a search engine, so it
# tests the host we are about to use
NET_STATE="unknown"

function have_internet {
	case "$NET_STATE" in
		yes) return 0 ;;
		no)  return 1 ;;
	esac
	if curl -sS -I --max-time 5 https://deb.debian.org >/dev/null 2>&1; then
		NET_STATE="yes"
		return 0
	fi
	NET_STATE="no"
	return 1
}

# All apt calls go through here:
#  - flock serializes against a second run of this script and against
#    unattended-upgrades, which otherwise gives a dpkg lock error that set -e
#    turns into an exit
#  - three separate things can stop and ask a question, and each needs its own
#    switch: apt itself ('Do you want to continue?') needs -y, passed by the
#    caller; debconf, used by maintainer scripts (tzdata's timezone, iptables-
#    persistent's save prompt), needs DEBIAN_FRONTEND=noninteractive; and dpkg
#    asking whether to replace a config file you have edited needs the
#    force-conf options, which DEBIAN_FRONTEND does NOT cover. confold keeps
#    the edited file, confdef takes the package default where there is no
#    conflict
#  - HideAutoRemove suppresses the "packages were automatically installed and
#    are no longer required" list. It is about packages this script never
#    touched, and running 'apt autoremove' is a decision for whoever maintains
#    the Pi, not a side effect of installing rclone
#
# 'env' rather than a VAR=value prefix: the assignment has to attach to
# apt-get, not to flock. Same option set as the apt-get call in the top-level
# install script, so the two behave identically
function apt_run {
	sudo flock -w 300 "$APT_LOCK" \
		env DEBIAN_FRONTEND=noninteractive \
		apt-get -o Dpkg::Options::=--force-confdef \
		        -o Dpkg::Options::=--force-confold \
		        -o APT::Get::HideAutoRemove=true "$@"
}

# dpkg tracks state as 'Status: <want> <error> <state>'. Only the third field
# says whether the files are actually on disk, and only 'installed' means they
# are. 'dpkg -s' exits 0 for any package dpkg has an entry for, including one
# removed without --purge (state 'config-files') and one left half-written by
# an interrupted install ('half-installed', 'unpacked', 'half-configured').
# Those all report as installed to 'dpkg -s' while the binary is missing
function pkg_state {
	dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null || true
}

function is_installed {
	[ "$(pkg_state "$1")" = "installed" ]
}

# Version currently unpacked, empty if the package is not installed
function pkg_installed_version {
	dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

# Version apt would install right now, from the cached lists. Empty or '(none)'
# means no configured repository offers this package at all - a typo in the
# package list, or a repository that failed to configure.
# 'apt-cache policy' rather than 'apt list --upgradable': the latter prints
# "WARNING: apt does not have a stable CLI interface" whenever its output is not
# a terminal, which is every run from cron or through a pipe
function pkg_candidate_version {
	apt-cache policy "$1" 2>/dev/null | awk '/^  Candidate:/ {print $2; exit}'
}

# Packages pinned with 'apt-mark hold' are deliberately left behind; apt will
# refuse to upgrade them, so they must not be reported as a failure
HELD_PACKAGES=" $(apt-mark showhold 2>/dev/null | tr '\n' ' ') "

function is_held {
	[[ "$HELD_PACKAGES" == *" $1 "* ]]
}

#---------- Refresh the package lists if they are stale ----------

# An empty or corrupted stamp - a power cut during the write below will do it -
# must not become an arithmetic syntax error that set -e turns into an exit
last_update=0
if [ -f "$STAMP_FILE" ] && read -r stamp < "$STAMP_FILE" 2>/dev/null; then
	if [[ "$stamp" =~ ^[0-9]+$ ]]; then
		last_update="$stamp"
	fi
fi

age=$(( $(date +%s) - last_update ))

if (( age > MAX_AGE )); then
	if ! have_internet; then
		echo -e "${YELLOW}No internet connection: continuing with the cached package lists${NC}"
	elif apt_run update; then
		# Written only after a successful update, so a failed run retries
		date +%s | sudo tee "$STAMP_FILE" >/dev/null
		echo "Package lists updated"
	else
		# Not fatal. One broken third-party repository entry is enough to fail
		# the whole update, and that must not stop a run whose packages are all
		# installed and current already. If the stale lists really do lack
		# something needed, the install below fails and the verification at the
		# end catches it. The stamp is deliberately not written, so the next run
		# retries rather than waiting out MAX_AGE
		echo -e "${YELLOW}Warning: 'apt-get update' failed, continuing with the cached package lists${NC}"
	fi
else
	echo "Package lists are up to date"
fi
# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

#---------- Decide what needs doing ----------

# Everything below reads only local data (dpkg's database and apt's cached
# lists), so this whole section works with no network connection

TO_INSTALL=()	# not installed at all
TO_UPGRADE=()	# installed, but a newer candidate exists

for package in "${REQUIRED_PACKAGES[@]}"; do

	state="$(pkg_state "$package")"
	have="$(pkg_installed_version "$package")"
	want="$(pkg_candidate_version "$package")"

	# No repository offers it. Nothing further to decide
	if [ -z "$want" ] || [ "$want" = "(none)" ]; then
		if [ "$state" = "installed" ]; then
			echo -e "${YELLOW}$package is installed ($have) but no configured repository offers it${NC}"
		else
			echo -e "${RED}$package is not available from any configured repository${NC}"
			echo "Check the package name and that the repositories are configured"
			exit 1
		fi
		continue
	fi

	if ! is_installed "$package"; then
		# Anything other than 'installed' needs apt to put it right, including
		# 'config-files' after a remove without --purge and the half-written
		# states an interrupted install leaves behind
		if [ -n "$state" ]; then
			echo -e "${YELLOW}$package is in state '$state', not installed: repairing...${NC}"
		else
			echo -e "${YELLOW}$package is missing: installing...${NC}"
		fi
		TO_INSTALL+=("$package")
		continue
	fi

	# dpkg --compare-versions, not string equality: Debian version ordering
	# understands epochs and '~' pre-release markers, so 1.0~rc1 correctly
	# sorts before 1.0
	if dpkg --compare-versions "$have" ge "$want"; then
		echo "$package is up-to-date ($have)"
	elif is_held "$package"; then
		echo -e "${YELLOW}$package is held at $have (candidate $want): leaving it alone${NC}"
	else
		echo -e "${YELLOW}$package is outdated ($have -> $want): upgrading...${NC}"
		TO_UPGRADE+=("$package")
	fi

done

#---------- Do it, in one transaction ----------

WORK=("${TO_INSTALL[@]}" "${TO_UPGRADE[@]}")

if [ "${#WORK[@]}" -gt 0 ]; then

	if ! have_internet; then
		if [ "${#TO_INSTALL[@]}" -gt 0 ]; then
			# Nothing to fall back on: the packages are not there
			echo -e "${RED}No internet connection, cannot install: ${TO_INSTALL[*]}${NC}"
			exit 1
		fi
		# Installed and working, just not current. Not worth failing over
		echo -e "${YELLOW}No internet connection: keeping the installed version of ${TO_UPGRADE[*]}${NC}"
		WORK=()
	fi
fi

if [ "${#WORK[@]}" -gt 0 ]; then
	# One call for all of them rather than one call each: apt resolves the
	# dependencies of the whole set together, instead of pulling in and then
	# replacing packages as it works through a list one at a time
	apt_run install -y "${WORK[@]}"
fi

#---------- Confirm the result ----------

# The point of this pass: every requested package is present and current, and
# says so from dpkg's own database rather than from apt's exit code. An
# 'apt-get install' that exits 0 having silently skipped something is exactly
# what this catches
FAILED=()

for package in "${REQUIRED_PACKAGES[@]}"; do

	if ! is_installed "$package"; then
		echo -e "${RED}$package is still not installed (state '$(pkg_state "$package")')${NC}"
		FAILED+=("$package")
		continue
	fi

	have="$(pkg_installed_version "$package")"
	want="$(pkg_candidate_version "$package")"

	if [ -z "$want" ] || [ "$want" = "(none)" ]; then
		# Installed, but unavailable: already reported above, nothing to verify
		continue
	fi

	if dpkg --compare-versions "$have" ge "$want"; then
		continue
	fi

	if is_held "$package"; then
		# Deliberate, so not a failure
		continue
	fi

	echo -e "${RED}$package is still at $have, expected $want${NC}"
	FAILED+=("$package")

done

if [ "${#FAILED[@]}" -gt 0 ]; then
	echo -e "${RED}Not available and up to date: ${FAILED[*]}${NC}"
	exit 1
fi

# [*] not the bare name: an array in scalar context expands to element 0, so
# the old version of this line reported only the first package
echo -e "${GREEN}Packages available and up to date: ${REQUIRED_PACKAGES[*]}${NC}"

# Nice to know, not essential: what this run actually changed
if [ "${#TO_INSTALL[@]}" -gt 0 ]; then
	echo "  Installed: ${TO_INSTALL[*]}"
fi
if [ "${#TO_UPGRADE[@]}" -gt 0 ]; then
	echo "  Upgraded:  ${TO_UPGRADE[*]}"
fi

# Report the changes to the caller if it asked for them. WORK is empty when
# there was nothing to do, and also when work was skipped for lack of a network
# connection, so this reports what actually happened rather than what was wanted
if [ -n "${ORADIO_PKG_CHANGED:-}" ] && [ "${#WORK[@]}" -gt 0 ]; then
	printf '%s\n' "${WORK[@]}" > "$ORADIO_PKG_CHANGED"
fi
