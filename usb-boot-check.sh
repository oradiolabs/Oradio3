#!/bin/bash
#
# usb-boot-check.sh - record one boot's USB outcome to a CSV.
#
# Boot behaviour cannot be exercised by usb-stress.sh, because the interesting
# part is the race between USB enumeration and usb-drive-boot.service, and that
# only happens during an actual boot. This runs once per boot, waits for things
# to settle, and appends a row.
#
# Install:
#   sudo cp usb-boot-check.sh /usr/local/sbin/
#   sudo chmod 755 /usr/local/sbin/usb-boot-check.sh
#   sudo cp usb-boot-check.service /etc/systemd/system/
#   sudo systemctl enable usb-boot-check.service
#
# Then run the matrix - roughly 10 reboots in each state:
#   A) stick fitted at power-on
#   B) no stick fitted
#   C) no stick at power-on, inserted about 30s later
#
# The script cannot tell A from C on its own, so declare the scenario:
#   sudo usb-boot-check.sh --scenario B
# or leave it to auto-label as "auto" and sort by the mounted_at_ms column.
#
# Read the results with:  usb-stress.sh report   (or just look at the CSV)
#
# TROUBLESHOOTING "only one row after several reboots":
#   Compare the boot_id column against the .trace file next to the CSV. The
#   trace records every invocation before anything that can fail, so:
#     - trace has one line per boot, CSV has one row  -> the script died; the
#       trace says where
#     - trace has one line total                      -> the unit is not running
#       each boot; check 'systemctl is-enabled usb-boot-check.service'
#     - CSV rows share a boot_id                      -> it ran twice in a boot
#   Note journald is Storage=volatile here, so 'journalctl -b -1' cannot help.
#
set -uo pipefail

MOUNTPOINT="${MOUNTPOINT:-/media/oradio}"
MONITOR="/run/usb_present"
RESULTS="${RESULTS:-/home/pi/Oradio3/usb-boot-check.csv}"
USBLOG="${USBLOG:-/home/pi/Oradio3/logging/usb.log}"
TRACE="${TRACE:-${RESULTS%.csv}.trace}"
SCENARIO="auto"
SETTLE=20

# Unique per boot. This is the column that answers "did the harness run on every
# boot?" - without it, a CSV with one row is ambiguous between "the service never
# started" and "it started and died before writing". Also lets you spot the
# opposite problem: two rows sharing a boot_id means it ran twice.
BOOT_ID="$(cut -c1-8 /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"

# Trace the invocation IMMEDIATELY, before anything that can fail. journald is
# Storage=volatile on this system, so a crash leaves no evidence anywhere else
# once you reboot again.
#
# Every write is followed by an fsync. Without it, a row written ~10s into boot
# sits in the page cache and is lost if the unit is power-cycled before ext4
# commits it - which is exactly how an Oradio is switched off in the field. The
# cost is one flush per boot; the alternative is silently missing samples in the
# only scenario that matters.
flush() { sync -d "$@" 2>/dev/null || sync; }
trace() {
	echo "$(date '+%F %T') boot=$BOOT_ID $*" >> "$TRACE" 2>/dev/null || true
	flush "$TRACE"
}
trace "invoked pid=$$ args=[$*]"

# Any early exit is recorded too, so "started but produced no row" is visible.
WROTE_ROW=no
on_exit() {
	local rc=$?
	[ "$WROTE_ROW" = yes ] || trace "EXITED WITHOUT WRITING A ROW (rc=$rc)"
	return 0
}
trap on_exit EXIT

[ "${1:-}" = "--scenario" ] && { SCENARIO="${2:-auto}"; }

# Marker dropped by ExecStop at shutdown. Its ABSENCE at startup means the
# previous shutdown was not clean - a power cut, a hang, or a pulled plug.
# Recorded per boot so the matrix distinguishes 'sudo reboot' from a power
# cycle automatically, instead of relying on remembering which you did.
#
# This matters for more than bookkeeping: an unclean removal is the case where
# the stick's controller may rebuild its flash translation tables on next
# power-up and take materially longer to become ready. If delay_use=100ms is
# ever going to be too low, it will be on a row where prev_shutdown=unclean.
CLEAN_MARKER="${CLEAN_MARKER:-${RESULTS%.csv}.clean}"

if [ "${1:-}" = "--shutdown" ]; then
	# Run from ExecStop. Touch and flush, so the next boot can tell this
	# shutdown was orderly.
	: > "$CLEAN_MARKER" 2>/dev/null && flush "$CLEAN_MARKER"
	trace "clean shutdown marker written"
	exit 0
fi

if [ -e "$CLEAN_MARKER" ]; then
	prev_shutdown=clean
	rm -f "$CLEAN_MARKER"
else
	prev_shutdown=unclean
fi
trace "previous shutdown: $prev_shutdown"

if [ "$(id -u)" -ne 0 ]; then
	trace "not root; aborting"
	echo "Must run as root"
	exit 1
fi

if ! touch "$RESULTS" 2>/dev/null; then
	trace "cannot write $RESULTS; aborting"
	echo "Cannot write $RESULTS" >&2
	exit 1
fi

# Give the udev fallback a chance before judging. usb-drive-boot may have
# skipped or lost its race, and the udev path lands ~1.7s later; anything
# beyond SETTLE is a genuine failure to mount.
deadline=$(( $(date +%s) + SETTLE ))
while [ "$(date +%s)" -lt "$deadline" ]; do
	mountpoint -q "$MOUNTPOINT" 2>/dev/null && break
	sleep 0.25
done

mounted=$(mountpoint -q "$MOUNTPOINT" 2>/dev/null && echo yes || echo no)
flag=$([ -e "$MONITOR" ] && echo yes || echo no)
part=$(blkid -L ORADIO 2>/dev/null || echo none)

# Unit outcome. A skipped unit reports ConditionResult=no, which is NOT the same
# as a failure and is easy to misread in plain 'systemctl status' output.
boot_result=$(systemctl show usb-drive-boot.service -p Result --value 2>/dev/null)
boot_cond=$(systemctl show usb-drive-boot.service -p ConditionResult --value 2>/dev/null)
boot_state=$(systemctl show usb-drive-boot.service -p ActiveState --value 2>/dev/null)
failed_units=$(systemctl list-units 'usb-drive*' --state=failed --no-legend --no-pager 2>/dev/null | wc -l)

# Milliseconds from the script's own start to the successful mount, as logged by
# usb-drive.sh this boot. Empty when nothing mounted.
mount_ms=$(grep -a "Success: mounted" "$USBLOG" 2>/dev/null | tail -1 | grep -oE '\(([0-9]+)ms\)' | tr -d '(ms)')

# Which path won: the boot unit logs nothing distinctive, so infer from whether
# the slice for the udev-triggered template unit exists.
udev_ran=$(systemctl show system-usb\\x2ddrive.slice -p ActiveState --value 2>/dev/null)

kernel_time=$(cut -d' ' -f1 /proc/uptime)

# The delay actually LOADED, not the one configured. usb-stress.sh sweep writes
# this parameter at runtime, and a run killed with SIGKILL leaves it at whatever
# value it was on - which then silently applies to every later boot until
# something resets it. Recording it per boot means a failure can be attributed
# rather than guessed at.
delay_use=$(cat /sys/module/usb_storage/parameters/delay_use 2>/dev/null || echo unknown)

# INCONSISTENT is the state worth catching: mounted but no flag file means the
# Python watchdog will never be told, so the unit is silently mute.
if [ "$mounted" = yes ] && [ "$flag" = yes ] && [ "$failed_units" -eq 0 ]; then
	verdict=OK
elif [ "$mounted" = no ] && [ "$part" = none ] && [ "$failed_units" -eq 0 ]; then
	verdict=NO_STICK			# Correct outcome when nothing is fitted
elif [ "$mounted" != "$flag" ]; then
	verdict=INCONSISTENT
else
	verdict=FAIL
fi

# -s not -f: the earlier 'touch' guarantees the file exists, so testing for
# existence would skip the header and leave a headerless CSV forever.
[ -s "$RESULTS" ] || echo "timestamp,boot_id,scenario,prev_shutdown,verdict,mounted,flag,partition,boot_result,boot_condition,boot_state,failed_units,mount_ms,udev_slice,delay_use,uptime_s" > "$RESULTS"
printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
	"$(date '+%F %T')" "$BOOT_ID" "$SCENARIO" "$prev_shutdown" "$verdict" "$mounted" "$flag" "$part" \
	"$boot_result" "$boot_cond" "$boot_state" "$failed_units" "${mount_ms:-}" \
	"${udev_ran:-none}" "$delay_use" "$kernel_time" >> "$RESULTS"
flush "$RESULTS"
WROTE_ROW=yes
trace "wrote row: verdict=$verdict mounted=$mounted flag=$flag delay_use=$delay_use prev=$prev_shutdown"

echo "usb-boot-check: $verdict (boot=$BOOT_ID prev=$prev_shutdown mounted=$mounted flag=$flag boot_result=$boot_result cond=$boot_cond)"

# Keep a diagnostic snapshot for anything that is not a clean result.
if [ "$verdict" != OK ] && [ "$verdict" != NO_STICK ]; then
	{
		echo "===== $(date '+%F %T')  verdict=$verdict scenario=$SCENARIO ====="
		systemctl status 'usb-drive*' --no-pager 2>&1 | head -40
		echo "--- lsblk ---"; lsblk 2>&1
		echo "--- kernel usb/scsi ---"; journalctl -b -k --no-pager 2>/dev/null | grep -iE 'usb|scsi|sd[a-z]' | tail -40
		echo "--- usb.log ---"; tail -20 "$USBLOG" 2>/dev/null
		echo
	} >> "${RESULTS%.csv}.diag"
fi
