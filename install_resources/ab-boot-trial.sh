#!/usr/bin/env bash
#
# ab-boot-trial.sh — the trial-boot half of A/B rollback.
#
# RUNS ON THE PI.
#
#   ab-boot-trial.sh check     early on every boot, from ab-boot-check.service
#   ab-boot-trial.sh commit    when the system is healthy — makes the trial permanent
#   ab-boot-trial.sh timeout   from ab-boot-timeout.timer, if nothing committed
#   ab-boot-trial.sh status    print the current trial state
#
# How the rollback works
# ----------------------
# The firmware provides a one-shot flag: reboot "0 tryboot" loads tryboot.txt
# instead of config.txt for exactly one boot. The flag is cleared before the
# firmware starts, so any crash, panic or reset returns to config.txt on the
# next boot — automatically, with nothing on the card to maintain.
#
# So config.txt always points at the slot known to work, and tryboot.txt points
# at the slot being tried:
#
#   config.txt          -> cmdline.txt          -> committed slot
#   tryboot.txt         -> tryboot-cmdline.txt  -> trial slot
#
# A trial that boots and reports healthy is committed by copying the trial
# cmdline over cmdline.txt. A trial that does not is undone by any reset.
#
# What each failure mode does
# ---------------------------
#   root will not mount        panic=10 in the trial cmdline resets the board
#   kernel or systemd hangs    hardware watchdog resets the board
#   boots but never confirms   ab-boot-timeout.timer reboots the board
# In every case the reset lands on config.txt, which is the old slot.
#
set -euo pipefail

STATE_FILE="/boot/firmware/oradio3-boot.state"
TRYBOOT_CONFIG="/boot/firmware/tryboot.txt"
TRIAL_CMDLINE="/boot/firmware/tryboot-cmdline.txt"
CMDLINE="/boot/firmware/cmdline.txt"

log() { printf '%s ab-boot-trial: %s\n' "$(date -Is)" "$*"; }
die() {
	printf '%s ab-boot-trial: ERROR: %s\n' "$(date -Is)" "$*" >&2
	exit 1
}

# reboot(8) returns as soon as the shutdown is queued — the system takes several
# more seconds to actually go down. Returning here would let the caller carry on
# and report a failure for a reboot that is already happening, so block instead.
# If the request was refused, say so rather than hanging forever.
do_reboot() { # do_reboot [reboot-arg...]
	reboot "$@" || die "reboot was refused"
	sleep 120
	die "still running two minutes after requesting a reboot"
}

part_dev() {
	if [[ "$1" =~ [0-9]$ ]]; then echo "${1}p${2}"; else echo "${1}${2}"; fi
}

# Which slot is this system running from? Partition 2 is A, partition 3 is B.
running_slot() {
	local dev disk
	dev="$(findmnt -nro SOURCE / || true)"
	[[ -n "$dev" ]] || return 1
	disk="$(lsblk -nro PKNAME "$dev" 2>/dev/null | head -1 || true)"
	[[ -n "$disk" ]] || return 1
	disk="/dev/$disk"
	case "$dev" in
	"$(part_dev "$disk" 2)") printf 'a' ;;
	"$(part_dev "$disk" 3)") printf 'b' ;;
	*) return 1 ;;
	esac
}

state_get() { # state_get <key>
	[[ -r "$STATE_FILE" ]] || return 1
	local v
	v="$(sed -n "s/^$1=//p" "$STATE_FILE" | tail -1)"
	[[ -n "$v" ]] || return 1
	printf '%s' "$v"
}

state_set() { # state_set <key> <value>
	[[ -r "$STATE_FILE" ]] || return 1
	local tmp="${STATE_FILE}.tmp"
	{
		grep -v "^$1=" "$STATE_FILE" || true
		printf '%s=%s\n' "$1" "$2"
	} >"$tmp"
	mv -f "$tmp" "$STATE_FILE"
	sync
}

# Remove the trial configuration so the firmware cannot be asked to try it
# again. config.txt is never touched here.
clear_trial_files() {
	rm -f "$TRYBOOT_CONFIG" "$TRIAL_CMDLINE"
	sync
}

##### check ###############################################
# Runs early on every boot. Three cases, distinguished by comparing the slot we
# are actually running from against the slot the trial was aiming at.
cmd_check() {
	local trial state now
	trial="$(state_get trial || true)"
	state="$(state_get state || true)"

	if [[ -z "$trial" || "$state" != "trying" ]]; then
		log "no trial in progress"
		return 0
	fi

	now="$(running_slot || echo '?')"
	log "trial slot: $trial, running slot: $now"

	if [[ "$now" == "$trial" ]]; then
		# The trial slot booted far enough to run this. Nothing is committed
		# yet: something still has to call 'commit', or the timeout will undo it.
		log "trial boot of slot $trial is up; awaiting commit"
		return 0
	fi

	# We are on the other slot, so the firmware fell back — the trial did not
	# come up. The board is running the slot known to work; record that and
	# remove the trial configuration so this does not repeat.
	log "ROLLED BACK: slot $trial did not boot, running $now instead"
	state_set state failed
	state_set failed_at "$(date -Is)"
	# Keep the version that failed. oradio3-update.sh reads it and declines to
	# install the same one again, which is what stops a still-inserted USB
	# drive from re-installing a package that has already proved it cannot boot.
	local ver
	ver="$(state_get trial_version || true)"
	[[ -n "$ver" ]] && state_set failed_version "$ver"
	clear_trial_files
	log "trial configuration removed; the board stays on slot $now"
	return 0
}

##### commit ##############################################
# Called once the system is considered healthy. Makes the trial permanent by
# pointing config.txt's cmdline at the trial slot.
cmd_commit() {
	local trial state now
	trial="$(state_get trial || true)"
	state="$(state_get state || true)"

	if [[ -z "$trial" || "$state" != "trying" ]]; then
		log "nothing to commit"
		return 0
	fi

	now="$(running_slot || echo '?')"
	[[ "$now" == "$trial" ]] ||
		die "running slot $now is not the trial slot $trial — refusing to commit"

	[[ -f "$TRIAL_CMDLINE" ]] || die "$TRIAL_CMDLINE is missing; cannot commit"

	# The committed cmdline becomes the trial cmdline, minus panic=10, which is
	# only wanted while a slot is on trial: a committed slot that panics should
	# be left for someone to look at rather than reset in a loop.
	cp -a "$CMDLINE" "${CMDLINE}.bak"
	sed 's/ *panic=[0-9]*//g' "$TRIAL_CMDLINE" >"$CMDLINE"

	clear_trial_files
	# Removing the state file also drops any failed_version, which is right: a
	# version that commits has proved itself, whatever happened on an earlier try.
	rm -f "$STATE_FILE"
	sync

	log "committed slot $trial"
	log "cmdline.txt is now: $(cat "$CMDLINE")"
}

##### timeout #############################################
# Runs a fixed time after boot. If nothing has committed by now the trial is
# considered failed. Rebooting is enough to undo it: the one-shot tryboot flag
# was cleared at the start of this boot, so the reset lands on config.txt.
cmd_timeout() {
	local trial state now
	trial="$(state_get trial || true)"
	state="$(state_get state || true)"

	if [[ -z "$trial" || "$state" != "trying" ]]; then
		log "no trial pending at timeout; nothing to do"
		return 0
	fi

	now="$(running_slot || echo '?')"
	if [[ "$now" != "$trial" ]]; then
		log "not running the trial slot; leaving the check service to handle it"
		return 0
	fi

	log "TRIAL TIMED OUT: slot $trial booted but never committed"
	state_set state timeout
	state_set failed_at "$(date -Is)"
	# Same as a failed boot: the version is on record as not working.
	local ver
	ver="$(state_get trial_version || true)"
	[[ -n "$ver" ]] && state_set failed_version "$ver"

	# Remove the trial configuration first: whatever happens to this reboot, the
	# board must not be asked to try this slot again.
	clear_trial_files

	log "rebooting; the firmware will return to the committed slot"
	sync
	sleep 2
	do_reboot
}

##### status ##############################################
cmd_status() {
	if [[ ! -r "$STATE_FILE" ]]; then
		printf 'no trial state\n'
	else
		sed 's/^/  /' "$STATE_FILE"
	fi
	printf '  running slot : %s\n' "$(running_slot || echo unknown)"
	printf '  committed    : %s\n' "$(grep -o 'root=[^[:space:]]*' "$CMDLINE" 2>/dev/null || echo unknown)"
	printf '  tryboot.txt  : %s\n' "$([[ -f "$TRYBOOT_CONFIG" ]] && echo present || echo absent)"
}

##### dispatch ############################################
case "${1:-}" in
check | commit | timeout)
	[[ $EUID -eq 0 ]] || die "must run as root"
	"cmd_$1"
	;;
status) cmd_status ;;
*)
	cat >&2 <<EOF
Usage: $0 {check|commit|timeout|status}

  check     early on every boot: detect a rollback, or note a trial is up
  commit    make the current trial permanent (call when healthy)
  timeout   undo a trial that booted but never committed
  status    print the current trial state
EOF
	exit 2
	;;
esac
