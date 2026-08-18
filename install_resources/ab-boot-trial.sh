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

# Held by oradio3-update.sh for the duration of an install. The timeout must not
# reboot while that lock is held: a reboot part-way through an install leaves
# the target slot neither the old release nor the new one, and the target during
# a trial is the slot being fallen back to.
UPDATE_LOCK="/run/oradio3-update.lock"

# The unit whose health decides a trial. At the timeout, a trial is committed if
# this is running and has not been restarting; otherwise it is rolled back.
#
# Set empty to disable the check, which makes the timeout an unconditional
# rollback and leaves committing entirely to `ab-boot-trial.sh commit`.
HEALTH_UNIT="oradio.service"

# How many restarts within the trial still counts as healthy. Restart=always
# means a crash loop can be momentarily "active"; a slot whose application keeps
# dying is not one to make permanent.
HEALTH_MAX_RESTARTS=3
BOOT_DIR="/boot/firmware"
TRYBOOT_CONFIG="$BOOT_DIR/tryboot.txt"
TRIAL_CMDLINE="$BOOT_DIR/tryboot-cmdline.txt"
CMDLINE="$BOOT_DIR/cmdline.txt"

log() { printf '%s ab-boot-trial: %s\n' "$(date -Is)" "$*"; }
die() {
	printf '%s ab-boot-trial: ERROR: %s\n' "$(date -Is)" "$*" >&2
	exit 1
}

# /boot/firmware is an autofs mountpoint (optimize_boot_time.sh puts
# x-systemd.automount in fstab, which is what keeps local-fs.target from waiting
# on the boot device). The real mount happens the first time anything looks up a
# path underneath it, so every read below triggers it as a side effect.
#
# That side effect is not something to rely on. Requires=local-fs.target in the
# unit file guarantees the autofs point exists, NOT that the partition mounted:
# a bad card, a wrong PARTUUID or a corrupt FAT now let the boot finish and fail
# here instead. And state_get cannot tell that apart from "no trial in progress",
# because both are just an unreadable file — one means do nothing, the other
# means a rollback is going unrecorded. So trigger the mount deliberately and
# check it landed, before reading anything.
boot_partition_ready() {
	# Looking up a path *inside* the mountpoint is what triggers autofs.
	stat -t "$BOOT_DIR/." >/dev/null 2>&1 || true
	# While untriggered, findmnt reports the autofs placeholder rather than the
	# partition; once mounted, both are listed and the real one is last.
	local fstype
	fstype="$(findmnt -nro FSTYPE "$BOOT_DIR" 2>/dev/null | tail -1 || true)"
	[[ -n "$fstype" && "$fstype" != "autofs" ]]
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

# Writing state must never be fatal. This runs Before=multi-user.target, and a
# boot partition that is missing or read-only would otherwise take the whole
# boot down with it — losing the rollback record is bad, losing the boot is
# worse.
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

	# Do not report "no trial in progress" when the truth is "cannot tell".
	# Failing the unit would be worse than useless here — it is ordered
	# Before=multi-user.target and the board is already running something — so
	# say so loudly and leave the state file alone.
	if ! boot_partition_ready; then
		log "ERROR: $BOOT_DIR is not mounted; the trial state cannot be read"
		log "if a trial is in progress it will neither commit nor be recorded"
		log "the next reset still returns to the committed slot, so this is not fatal"
		return 0
	fi

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

	# Unlike check, commit is asked for explicitly and writes to this partition.
	# Refusing is right: a "successful" commit that wrote nothing would leave the
	# board one reset away from silently reverting.
	boot_partition_ready ||
		die "$BOOT_DIR is not mounted; refusing to commit"

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

# Is the application actually working? "Active" alone is weak — Restart=always
# means a crash loop is active between crashes — so also require that it has not
# been restarting.
health_ok() {
	local state restarts
	state="$(systemctl is-active "$HEALTH_UNIT" 2>/dev/null || true)"
	if [[ "$state" != "active" ]]; then
		log "$HEALTH_UNIT is $state, not active"
		return 1
	fi

	restarts="$(systemctl show -p NRestarts --value "$HEALTH_UNIT" 2>/dev/null || echo 0)"
	[[ "$restarts" =~ ^[0-9]+$ ]] || restarts=0
	if ((restarts > HEALTH_MAX_RESTARTS)); then
		log "$HEALTH_UNIT is active but has restarted $restarts times"
		return 1
	fi

	log "$HEALTH_UNIT: active, $restarts restart(s)"
	return 0
}

##### timeout #############################################
# Runs a fixed time after boot. If nothing has committed by now the trial is
# considered failed. Rebooting is enough to undo it: the one-shot tryboot flag
# was cleared at the start of this boot, so the reset lands on config.txt.
cmd_timeout() {
	local trial state now

	# This subcommand reboots the board. Doing that on the strength of a state
	# file that could not be read is the one mistake here worth avoiding
	# absolutely: it would reboot a perfectly healthy system, and on a slot whose
	# boot partition is unreadable it would do so every five minutes forever.
	if ! boot_partition_ready; then
		log "ERROR: $BOOT_DIR is not mounted; not deciding a trial blind"
		log "not rebooting: a trial that cannot be read cannot be rolled back safely"
		return 0
	fi

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

	# An install in progress reboots by itself when it finishes. Rebooting out
	# from under it is what turns a failed update into a broken fallback slot.
	if [[ -e "$UPDATE_LOCK" ]] && ! flock -n "$UPDATE_LOCK" true 2>/dev/null; then
		log "an update is installing; not rebooting"
		log "it will reboot when it completes, or the trial ends at the next boot"
		return 0
	fi

	# Nothing has committed by hand. Decide from the application's state rather
	# than rolling back regardless: five minutes of it running is the evidence a
	# trial is meant to gather.
	if [[ -n "$HEALTH_UNIT" ]] && health_ok; then
		log "$HEALTH_UNIT is healthy; committing slot $trial"
		cmd_commit
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
	# Report this first: with an automounted boot partition, "no trial state" and
	# "cannot see the boot partition" look identical from here otherwise.
	if boot_partition_ready; then
		printf '  boot part.   : mounted (%s)\n' \
			"$(findmnt -nro SOURCE "$BOOT_DIR" 2>/dev/null | tail -1 || echo unknown)"
	else
		printf '  boot part.   : NOT MOUNTED — everything below is unreliable\n'
	fi

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
