#!/usr/bin/bash
#
# trigger-update.sh — install a named .swu, now.
#   scp oradio3-<version>.swu pi@oradio.local:/tmp/
#   ssh pi@oradio.local 'sudo ./trigger-update.sh /tmp/oradio3-<version>.swu'
#
# All it does is name the package and start oradio3-update.service, which makes
# every decision: whether the version differs, whether it already failed a trial
# boot, whether the kernel matches. Nothing here bypasses those.
#
set -euo pipefail

MARKER="/run/swu_present"
SERVICE="oradio3-update.service"

SWU="${1:-}"

[[ -n "$SWU" ]] || {
	echo "Usage: sudo $0 /path/to/package.swu" >&2
	exit 2
}
[[ -f "$SWU" ]] || {
	echo "no such file: $SWU" >&2
	exit 1
}

# The service reads the path from the marker, so give it an absolute one:
# it runs with its own working directory and would not find a relative path.
SWU="$(readlink -f "$SWU")"

# Write then rename. A path unit watching the marker fires on the final name
# appearing, so it must never observe a half-written file.
#
# Via sudo tee, not a redirection: /run is root-owned, and `sudo cmd > file`
# would open the file as the invoking user before sudo runs. Written this way
# the script works whether or not it was itself invoked with sudo.
printf '%s\n' "$SWU" | sudo tee "${MARKER}.tmp" >/dev/null
sudo mv -f "${MARKER}.tmp" "$MARKER"

echo "queued: $SWU"

# --no-block returns immediately. The install takes minutes and ends in a reboot.
sudo systemctl start --no-block "$SERVICE"

cat <<EOF
started $SERVICE

  watch it:    tail -F /var/log/update.log

$SERVICE sends its output to that file, so
journalctl -u $SERVICE shows only systemd's own lines.

On success the Pi trial-boots the new slot and this connection drops.
Once it is back and healthy, make it permanent:

  sudo ab-boot-trial.sh commit
EOF
