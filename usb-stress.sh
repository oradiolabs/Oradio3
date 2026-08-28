#!/bin/bash
#
# usb-stress.sh - stress-test the ORADIO USB mount/unmount handling.
#
# Exercises the hotplug paths automatically by deauthorizing and reauthorizing
# the USB device in sysfs. That is a full USB re-enumeration - descriptor reads,
# driver binding, usb-storage scan honouring delay_use, partition scan, udev
# events - so it is a close analogue of a physical replug, and unlike a physical
# replug it can be repeated hundreds of times unattended.
#
# What it does NOT test: the electrical side of insertion (contact bounce,
# inrush current, a partially seated connector). Do a smaller run of physical
# replugs as well; see the matrix in the report footer.
#
# Usage:
#   sudo ./usb-stress.sh cycle [N]      run N insert/remove cycles (default 20)
#   sudo ./usb-stress.sh case <name>    run one named case
#   sudo ./usb-stress.sh sweep [N]      test a range of usb-storage.delay_use
#                                       values, N cycles each (default 25)
#   sudo ./usb-stress.sh report         summarise hotplug results
#   sudo ./usb-stress.sh sweep-report   summarise sweep results
#   sudo ./usb-stress.sh reset          clear results and restore the device
#
# Cases: remove insert rapid double-add repeat-remove busy-remove
#
# Sweep values default to "0 10ms 25ms 50ms 100ms 200ms"; override with
#   sudo SWEEP_VALUES="100ms 250ms 500ms" ./usb-stress.sh sweep
#
set -uo pipefail

MOUNTPOINT="${MOUNTPOINT:-/media/oradio}"
MONITOR="/run/usb_present"
LABEL="ORADIO"
RESULTS="${RESULTS:-/var/log/usb-stress.csv}"
USBLOG="${USBLOG:-/home/pi/Oradio3/logging/usb.log}"
SETTLE=15				# Seconds to wait for a state change before calling it a failure

# delay_use sweep
DELAY_PARAM="/sys/module/usb_storage/parameters/delay_use"
SWEEPDIR="${SWEEPDIR:-/var/log/usb-stress-sweep}"
SWEEP_VALUES="${SWEEP_VALUES:-0 10ms 25ms 50ms 100ms 200ms}"
SWEEP_CYCLES="${SWEEP_CYCLES:-25}"
DELAY_ORIG=""			# Restored on exit

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'

PASS=0; FAIL=0

now_ms() { echo $(( $(date +%s%3N) )); }

say()  { echo "$*"; }
ok()   { echo "  ${GREEN}PASS${NC} $*"; }
bad()  { echo "  ${RED}FAIL${NC} $*"; }
warn() { echo "  ${YELLOW}WARN${NC} $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Must run as root"; exit 1; }

# ---------------------------------------------------------------------------
# Locate the USB device backing the ORADIO partition
# ---------------------------------------------------------------------------
# Walks up from the block device to the USB interface (the directory holding
# bInterfaceNumber), then one more level to the USB device itself, which is the
# node exposing 'authorized'.
find_usb_dev() {
	local part dev p
	part="$(blkid -L "$LABEL" 2>/dev/null)" || return 1
	dev="$(lsblk -no pkname "$part" 2>/dev/null | head -1)"
	[ -n "$dev" ] || return 1
	p="$(readlink -f "/sys/block/$dev")"
	while [ "$p" != "/" ] && [ -n "$p" ]; do
		if [ -f "$p/bInterfaceNumber" ]; then
			dirname "$p"
			return 0
		fi
		p="$(dirname "$p")"
	done
	return 1
}

USBDEV=""
resolve_usbdev() {
	if [ -z "$USBDEV" ]; then
		USBDEV="$(find_usb_dev)" || return 1
	fi
	[ -f "$USBDEV/authorized" ] || return 1
	return 0
}

# ---------------------------------------------------------------------------
# Predicates and waits
# ---------------------------------------------------------------------------
is_mounted()   { mountpoint -q "$MOUNTPOINT" 2>/dev/null; }
not_mounted()  { ! mountpoint -q "$MOUNTPOINT" 2>/dev/null; }
flag_present() { [ -e "$MONITOR" ]; }
flag_absent()  { [ ! -e "$MONITOR" ]; }
part_present() { blkid -L "$LABEL" >/dev/null 2>&1; }

mounted_ok()   { is_mounted && flag_present; }
removed_ok()   { not_mounted && flag_absent; }

# wait_for <timeout_s> <predicate-fn>   -> echoes elapsed ms, returns 0/1
wait_for() {
	local t0 deadline
	t0="$(now_ms)"; deadline=$(( t0 + $1 * 1000 ))
	while :; do
		if "$2"; then echo $(( $(now_ms) - t0 )); return 0; fi
		[ "$(now_ms)" -lt "$deadline" ] || { echo $(( $(now_ms) - t0 )); return 1; }
		sleep 0.05
	done
}

# ---------------------------------------------------------------------------
# Device actuation
# ---------------------------------------------------------------------------
usb_off() { echo 0 > "$USBDEV/authorized"; }
usb_on()  { echo 1 > "$USBDEV/authorized"; }

# Diagnostics captured whenever a case fails, so a failure at cycle 137
# overnight is still explicable in the morning.
capture() {
	local tag="$1"
	{
		echo "===== $tag  $(date '+%F %T') ====="
		echo "--- mount ---";      findmnt -n "$MOUNTPOINT" 2>&1 || echo "(not mounted)"
		echo "--- flag ---";       ls -l "$MONITOR" 2>&1 || echo "(absent)"
		echo "--- blkid ---";      blkid 2>&1 | grep -i "$LABEL" || echo "(no $LABEL)"
		echo "--- lsblk ---";      lsblk 2>&1
		echo "--- units ---";      systemctl --no-pager --failed 2>&1 | head -20
		echo "--- usb-drive ---";  systemctl status 'usb-drive*' --no-pager 2>&1 | head -40
		echo "--- kernel ---";     dmesg | tail -30
		echo "--- usb.log ---";    tail -10 "$USBLOG" 2>/dev/null
		echo
	} >> "${RESULTS%.csv}.diag"
}

record() {  # record <case> <result> <elapsed_ms> <detail>
	[ -f "$RESULTS" ] || echo "timestamp,case,result,elapsed_ms,detail" > "$RESULTS"
	printf '%s,%s,%s,%s,"%s"\n' "$(date '+%F %T')" "$1" "$2" "$3" "$4" >> "$RESULTS"
	if [ "$2" = "PASS" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); capture "$1"; fi
}

# Any unit left in 'failed' is a failure regardless of whether the mount looks
# right - a wedged unit blocks the next event even when this cycle passed.
units_clean() {
	local f
	f="$(systemctl list-units 'usb-drive*' --state=failed --no-legend --no-pager 2>/dev/null)"
	[ -z "$f" ]
}

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
case_remove() {
	say "case: remove"
	is_mounted || { warn "not mounted at start; inserting first"; case_insert >/dev/null; }
	usb_off
	local ms rc=0
	ms="$(wait_for "$SETTLE" removed_ok)" || rc=1
	if [ $rc -eq 0 ] && units_clean; then
		ok "unmounted and flag cleared in ${ms}ms"; record remove PASS "$ms" ""
	else
		bad "state after ${ms}ms: mounted=$(is_mounted && echo y || echo n) flag=$(flag_present && echo y || echo n)"
		record remove FAIL "$ms" "mounted=$(is_mounted && echo y || echo n) flag=$(flag_present && echo y || echo n) units_clean=$(units_clean && echo y || echo n)"
	fi
}

case_insert() {
	say "case: insert"
	not_mounted || { warn "already mounted at start; removing first"; usb_off; wait_for "$SETTLE" removed_ok >/dev/null; }
	usb_on
	local ms rc=0
	ms="$(wait_for "$SETTLE" mounted_ok)" || rc=1
	if [ $rc -eq 0 ] && units_clean; then
		ok "mounted and flag set in ${ms}ms"; record insert PASS "$ms" ""
	else
		bad "state after ${ms}ms: mounted=$(is_mounted && echo y || echo n) flag=$(flag_present && echo y || echo n)"
		record insert FAIL "$ms" "mounted=$(is_mounted && echo y || echo n) flag=$(flag_present && echo y || echo n) part=$(part_present && echo y || echo n)"
	fi
}

# Remove and re-insert with no settle time between. Targets the flock: an add
# and a remove instance can now be in flight at once, and the order they take
# the lock is not guaranteed.
case_rapid() {
	say "case: rapid remove+insert (no settle between)"
	is_mounted || { usb_on; wait_for "$SETTLE" mounted_ok >/dev/null; }
	usb_off
	sleep 0.2
	usb_on
	local ms rc=0
	ms="$(wait_for "$SETTLE" mounted_ok)" || rc=1
	if [ $rc -eq 0 ] && units_clean; then
		ok "recovered to mounted in ${ms}ms"; record rapid PASS "$ms" ""
	else
		bad "did not settle to mounted after ${ms}ms"
		record rapid FAIL "$ms" "mounted=$(is_mounted && echo y || echo n) flag=$(flag_present && echo y || echo n)"
	fi
}

# A spurious udev add while already mounted. The 'change' match added to
# 99-local.rules makes these more frequent than before, so the already-mounted
# guard needs to hold.
case_double_add() {
	say "case: duplicate add event while mounted"
	is_mounted || { usb_on; wait_for "$SETTLE" mounted_ok >/dev/null; }
	local part; part="$(blkid -L "$LABEL")"
	udevadm trigger --action=add "/sys/class/block/$(basename "$part")" 2>/dev/null
	udevadm settle --timeout=10
	sleep 1
	if mounted_ok && units_clean; then
		ok "still mounted, no failed units"; record double-add PASS 0 ""
	else
		bad "duplicate add disturbed the mount"
		record double-add FAIL 0 "mounted=$(is_mounted && echo y || echo n) units_clean=$(units_clean && echo y || echo n)"
	fi
}

# Remove twice. The second must be a clean no-op, not a failed unit.
case_repeat_remove() {
	say "case: remove event with nothing mounted"
	not_mounted || { usb_off; wait_for "$SETTLE" removed_ok >/dev/null; }
	systemctl start usb-drive@remove.service 2>/dev/null
	sleep 1
	if removed_ok && units_clean; then
		ok "no-op remove handled cleanly"; record repeat-remove PASS 0 ""
	else
		bad "spurious remove left bad state or a failed unit"
		record repeat-remove FAIL 0 "units_clean=$(units_clean && echo y || echo n)"
	fi
}

# Pull the stick while a process has its cwd inside the mount. Exercises the
# umount -l fallback; a plain umount will return EBUSY here.
case_busy_remove() {
	say "case: remove while mount point is busy"
	is_mounted || { usb_on; wait_for "$SETTLE" mounted_ok >/dev/null; }
	( cd "$MOUNTPOINT" && sleep 25 ) &
	local holder=$!
	sleep 0.5
	usb_off
	local ms rc=0
	ms="$(wait_for "$SETTLE" removed_ok)" || rc=1
	kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
	if [ $rc -eq 0 ] && units_clean; then
		ok "lazy unmount succeeded in ${ms}ms"; record busy-remove PASS "$ms" ""
	else
		bad "busy mount not torn down after ${ms}ms"
		record busy-remove FAIL "$ms" "mounted=$(is_mounted && echo y || echo n)"
	fi
}

# ---------------------------------------------------------------------------
# delay_use sweep
# ---------------------------------------------------------------------------
# WHY SWEEP AT ALL: 100ms is not a measured property of this stick. It is the
# figure the kernel patch author suggested as working for most USB pen drives.
# The sweep tells you where YOUR hardware actually fails, so the shipped value
# can be justified by data rather than by a mailing list post.
#
# HOW TO READ THE RESULT: do not ship the lowest value that passes. The cost
# function is asymmetric - too low means /dev/sda1 never appears at all and
# nothing downstream can recover, too high costs milliseconds that vanish into
# the ~12s of slack before Oradio reads the mount. Take the lowest passing value
# and multiply by 3-4.
#
# WHAT THIS CANNOT MEASURE: the stick is cleanly idle at every re-authorize
# here. After an unclean removal mid-write, the controller may rebuild its flash
# translation tables on next power-up, which takes materially longer. The floor
# found here is a best case, not a worst case. That is the main reason for the
# margin above.

get_delay() { cat "$DELAY_PARAM" 2>/dev/null; }

# Returns 0 if the value was accepted. A pre-rework driver parses delay_use as a
# plain integer and rejects anything with an 'ms' suffix.
set_delay() {
	echo "$1" > "$DELAY_PARAM" 2>/dev/null || return 1
	[ "$(get_delay)" = "$1" ] || return 1
	return 0
}

# Drop 'ms' values if this kernel cannot parse them, rather than silently
# testing the default over and over and reporting a flat, meaningless curve.
usable_sweep_values() {
	local v out=() saved
	saved="$(get_delay)"
	for v in $SWEEP_VALUES; do
		if set_delay "$v"; then
			out+=("$v")
		else
			warn "kernel rejected delay_use=$v; skipping" >&2
		fi
	done
	set_delay "$saved" >/dev/null 2>&1
	echo "${out[@]}"
}

do_sweep() {
	local values v i rc
	[ -w "$DELAY_PARAM" ] || { echo "${RED}$DELAY_PARAM not writable${NC}"; exit 1; }
	DELAY_ORIG="$(get_delay)"
	say "Current delay_use: $DELAY_ORIG (restored on exit)"

	values="$(usable_sweep_values)"
	[ -n "$values" ] || { echo "${RED}No usable sweep values${NC}"; exit 1; }
	say "Sweeping: $values  (${SWEEP_CYCLES} cycles each)"

	mkdir -p "$SWEEPDIR"
	for v in $values; do
		echo
		echo "================ delay_use=$v ================"
		if ! set_delay "$v"; then
			warn "could not set delay_use=$v; skipping"
			continue
		fi

		# The new value only applies to devices probed from now on. The
		# remove/insert below re-probes, so the first cycle already uses it.
		RESULTS="$SWEEPDIR/${v}.csv"
		rm -f "$RESULTS" "${RESULTS%.csv}.diag"

		for ((i = 1; i <= SWEEP_CYCLES; i++)); do
			printf '  cycle %d/%d\r' "$i" "$SWEEP_CYCLES"
			case_remove >/dev/null 2>&1
			case_insert >/dev/null 2>&1

			# A failed insert at a low delay_use usually means the partition
			# never appeared. Recover before the next cycle so one bad value
			# does not poison the rest of the sweep.
			if ! mounted_ok; then
				usb_off; sleep 1; usb_on
				wait_for 20 mounted_ok >/dev/null || warn "device did not recover at delay_use=$v"
			fi
		done
		printf '                              \r'

		rc=$(awk -F, 'NR>1 && $3=="FAIL"' "$RESULTS" 2>/dev/null | wc -l)
		echo "  delay_use=$v -> $rc failure(s) in $((SWEEP_CYCLES*2)) operations"
	done

	set_delay "$DELAY_ORIG" >/dev/null 2>&1
	sweep_report
}

sweep_report() {
	[ -d "$SWEEPDIR" ] || { echo "No sweep results in $SWEEPDIR"; return 1; }
	echo
	echo "==================== delay_use sweep report ===================="
	printf "%-10s %6s %6s %8s %10s %10s\n" "DELAY" "OPS" "FAIL" "FAIL_%" "INS_AVG" "INS_MAX"

	local f v lowest=""
	# Sort numerically by the millisecond value so the curve reads in order.
	for f in $(ls "$SWEEPDIR"/*.csv 2>/dev/null | while read -r x; do
			v="$(basename "$x" .csv)"
			printf '%s\t%s\n' "$(echo "$v" | sed 's/ms$//;s/^$/0/')" "$x"
		done | sort -n | cut -f2); do
		v="$(basename "$f" .csv)"
		read -r ops fails avg max <<<"$(awk -F, '
			NR>1 { ops++; if ($3=="FAIL") fails++
			       if ($2=="insert" && $3=="PASS" && $4+0>0) { s+=$4; c++; if ($4+0>mx) mx=$4 } }
			END { printf "%d %d %s %s", ops, fails+0,
			      (c ? sprintf("%.0f", s/c) : "-"), (mx ? mx : "-") }' "$f")"
		printf "%-10s %6s %6s %8s %10s %10s\n" "$v" "$ops" "$fails" \
			"$(awk -v a="$fails" -v b="$ops" 'BEGIN{printf "%.1f", (b?100*a/b:0)}')" "$avg" "$max"
		[ "$fails" -eq 0 ] && [ -z "$lowest" ] && lowest="$v"
	done

	echo
	if [ -n "$lowest" ]; then
		echo "Lowest value with zero failures: ${GREEN}${lowest}${NC}"
		echo "Do NOT ship that value. Multiply by 3-4 for margin: an unclean"
		echo "removal can make the stick slower to become ready than anything"
		echo "this sweep reproduces, and the failure mode is a silent no-mount."
	else
		echo "${RED}Every value tested had failures.${NC} Either the stick is slow"
		echo "(extend the range: SWEEP_VALUES='100ms 250ms 500ms 1000ms') or the"
		echo "failures are not delay-related - check the .diag files."
	fi
	echo "==============================================================="
}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
do_report() {
	[ -f "$RESULTS" ] || { echo "No results at $RESULTS"; return 1; }
	echo
	echo "==================== USB stress test report ===================="
	echo "Results file: $RESULTS"
	echo
	awk -F, 'NR>1 {
		n[$2]++; if ($3=="PASS") p[$2]++; else f[$2]++
		if ($4+0 > 0) { s[$2]+=$4; c[$2]++; if ($4+0 > mx[$2]) mx[$2]=$4 }
	}
	END {
		printf "%-16s %6s %6s %6s %10s %10s\n", "CASE","RUNS","PASS","FAIL","AVG_MS","MAX_MS"
		for (k in n) printf "%-16s %6d %6d %6d %10s %10s\n", k, n[k], p[k]+0, f[k]+0,
			(c[k] ? sprintf("%.0f", s[k]/c[k]) : "-"), (mx[k] ? mx[k] : "-")
	}' "$RESULTS" | sort
	echo
	local total fails
	total=$(( $(wc -l < "$RESULTS") - 1 ))
	fails=$(awk -F, 'NR>1 && $3=="FAIL"' "$RESULTS" | wc -l)
	if [ "$fails" -eq 0 ]; then
		echo "${GREEN}All $total runs passed.${NC}"
	else
		echo "${RED}$fails of $total runs FAILED.${NC} Diagnostics: ${RESULTS%.csv}.diag"
		echo
		awk -F, 'NR>1 && $3=="FAIL" { print "  " $1 "  " $2 "  " $5 }' "$RESULTS"
	fi
	echo
	echo "Not covered here - run these by hand:"
	echo "  1. Boot WITH stick fitted          -> mounted before oradio.service"
	echo "  2. Boot WITHOUT stick fitted       -> usb-drive-boot exits 0, NOT failed"
	echo "  3. Boot without, insert after boot -> udev path mounts it"
	echo "  4. Physical replug x20             -> covers contact bounce and inrush"
	echo "  5. Remove during playback          -> MPD recovers, no stale mount"
	echo "  Use 'usb-boot-check.sh' for 1-3."
	echo "================================================================"
}

# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------
preflight() {
	resolve_usbdev || { echo "${RED}Cannot find a USB device with label $LABEL.${NC}"; exit 1; }
	say "USB device: $USBDEV  ($(cat "$USBDEV/idVendor"):$(cat "$USBDEV/idProduct"))"

	# A .swu on the stick makes every insert start the updater, which ends in a
	# reboot. That would destroy the run and possibly the install.
	if is_mounted && compgen -G "$MOUNTPOINT/*.swu" >/dev/null; then
		echo "${RED}ABORT: a .swu update package is present on the stick.${NC}"
		echo "Each insert would start oradio3-update.service and reboot the Pi."
		echo "Move the .swu off the stick before stress testing."
		exit 1
	fi

	if systemctl is-active --quiet oradio3-update.service; then
		echo "${RED}ABORT: oradio3-update.service is running.${NC}"; exit 1
	fi
	say "Preflight OK"
}

restore() {
	# Put delay_use back before re-authorizing, so the stick comes back under
	# the setting the system normally runs with.
	[ -n "$DELAY_ORIG" ] && echo "$DELAY_ORIG" > "$DELAY_PARAM" 2>/dev/null
	[ -n "$USBDEV" ] && [ -f "$USBDEV/authorized" ] && echo 1 > "$USBDEV/authorized" 2>/dev/null
	wait_for "$SETTLE" mounted_ok >/dev/null 2>&1
}
trap restore EXIT

# ---------------------------------------------------------------------------
main() {
	case "${1:-}" in
		cycle)
			preflight
			local n="${2:-20}" i
			say "Running $n cycles. Ctrl-C is safe; the stick is re-authorized on exit."
			for ((i = 1; i <= n; i++)); do
				echo
				echo "---------- cycle $i/$n ----------"
				case_remove
				case_insert
				# Interleave the edge cases rather than running them in a block,
				# so they land in varied system states instead of one quiet one.
				case $(( i % 4 )) in
					1) case_rapid ;;
					2) case_double_add ;;
					3) case_repeat_remove; case_insert ;;
					0) case_busy_remove; case_insert ;;
				esac
			done
			do_report
			;;
		case)
			preflight
			case "${2:-}" in
				remove)        case_remove ;;
				insert)        case_insert ;;
				rapid)         case_rapid ;;
				double-add)    case_double_add ;;
				repeat-remove) case_repeat_remove ;;
				busy-remove)   case_busy_remove ;;
				*) echo "Unknown case: ${2:-}"; exit 1 ;;
			esac
			;;
		sweep)
			preflight
			[ -n "${2:-}" ] && SWEEP_CYCLES="$2"
			do_sweep
			;;
		sweep-report) trap - EXIT; sweep_report ;;
		report)       trap - EXIT; do_report ;;
		reset)  rm -f "$RESULTS" "${RESULTS%.csv}.diag"; rm -rf "$SWEEPDIR"; say "Results cleared." ;;
		*)
			sed -n '2,26p' "$0" | sed 's/^# \?//'
			exit 1
			;;
	esac
}

main "$@"
