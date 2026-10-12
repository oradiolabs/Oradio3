#!/usr/bin/env bash
# Check USB autosuspend for the stick with label ORADIO, with or without hub.
# Usage: check_usb_autosuspend.sh [LABEL] [WAIT_SECONDS]
# Default wait 45 s: 5 s SCSI disk + 30 s USB delay + margin.
# Only reads /sys and the log, so it does not wake the stick itself.
LABEL="${1:-ORADIO}"; WAIT="${2:-45}"; LOG="${LOG:-$HOME/Oradio3/logging/usb.log}"

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

part="$(readlink -f "/dev/disk/by-label/$LABEL" 2>/dev/null)"
[ -b "$part" ] || { echo "Geen partitie met label '$LABEL' gevonden"; exit 1; }
sys="$(readlink -f "/sys/class/block/$(basename "$part")")"
if [ -f "$sys/partition" ]; then disk="$(dirname "$sys")"; else disk="$sys"; fi
usb="$sys"; while [ "$usb" != / ] && [ ! -f "$usb/idVendor" ]; do usb="$(dirname "$usb")"; done
[ -f "$usb/idVendor" ] || { echo "Geen USB-device boven '$part'"; exit 1; }
# Runtime-PM chain from disk up to the USB device (SCSI disk, target, host, ...)
chain=(); p="$(readlink -f "$disk/device")"
while [ "$p" != "$usb" ]; do [ -f "$p/power/runtime_status" ] && chain+=("$p"); p="$(dirname "$p")"; done
chain+=("$usb")

echo "Stick: $(basename "$part") op $(basename "$disk"), USB-device $(basename "$usb")" \
     "($(cat "$usb/idVendor"):$(cat "$usb/idProduct") $(cat "$usb/product" 2>/dev/null))"
echo "Log:   $(grep autosuspend "$LOG" 2>/dev/null | tail -1)"
echo
echo "Instellingen (verwacht):"
echo "  events_poll_msecs          = $(cat "$disk/events_poll_msecs")  (0)"
echo "  SCSI autosuspend_delay_ms  = $(cat "$disk/device/power/autosuspend_delay_ms")  (>= 0, bijv. 5000)"
echo "  SCSI power/control         = $(cat "$disk/device/power/control")  (auto)"
echo "  USB  autosuspend_delay_ms  = $(cat "$usb/power/autosuspend_delay_ms")  (bijv. 30000)"
echo "  USB  power/control         = $(cat "$usb/power/control")  (auto)"
echo
echo "Alle USB-apparaten (alleen stick en hubs horen op auto):"
for d in /sys/bus/usb/devices/*; do
	[ -f "$d/idVendor" ] || continue
	mark=""; [ "$(readlink -f "$d")" = "$usb" ] && mark="${GREEN}  <- stick${NC}"
	echo -e "  $(basename "$d")  $(cat "$d/product" 2>/dev/null)  control=$(cat "$d/power/control")$mark"
done

io1="$(cat "$disk/stat")"; cmd1=$(( $(cat "$disk/device/iorequest_cnt") )); t1=$(cat "$usb/power/runtime_suspended_time")
echo; echo "Wachten ${WAIT} s (Oradio stil laten)..."; sleep "$WAIT"
io2="$(cat "$disk/stat")"; cmd2=$(( $(cat "$disk/device/iorequest_cnt") )); t2=$(cat "$usb/power/runtime_suspended_time")

echo
echo "Lees/schrijf-I/O:  $([ "$io1" = "$io2" ] && echo geen || echo "JA, er was I/O")"
echo "SCSI-commando's:   $((cmd2 - cmd1))  (0 verwacht; > 0 zonder I/O = polling)"
echo "Keten:"
all=1
for p in "${chain[@]}"; do
	s="$(cat "$p/power/runtime_status")"; [ "$s" = suspended ] || all=0
	echo "  $(basename "$p"): $s"
done
echo "Tijd in suspend:   $(( (t2 - t1) / 1000 )) s van de ${WAIT} s"
echo
echo "Kernelmeldingen voor deze stick:"
dmesg 2>/dev/null | grep -E "usb $(basename "$usb")[: ]|$(basename "$disk")" | grep -iE "reset|disconnect|error" \
	|| echo "  geen (of dmesg niet leesbaar: draai dan met sudo)"
echo
[ "$all" = 1 ] && echo "Resultaat: stick slaapt" || echo "Resultaat: stick slaapt NIET"
