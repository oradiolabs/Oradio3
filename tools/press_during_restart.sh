#!/usr/bin/env bash
#
# Show what happened around a preset press during the start-up blink.
#
# Prints the lines that answer, in order:
#   1. did the press reach the state machine
#   2. did it commit to the preset state
#   3. was the StartupToIdle timer refused (it should be)
#   4. did MPD actually play, or did the preset look invalid
#   5. did anything transition back to Idle afterwards
#
# Usage: press_during_startup.sh [path/to/oradio.log]
#
# Without an argument it reads this Oradio's application log. Pass a path to
# read a rotated generation (oradio.log.1), or a log from an RMS upload or
# another Oradio, for instance on a laptop.

# The application log, by default as constants.env names it
CONSTANTS="$(dirname "${BASH_SOURCE[0]}")/../constants.env"
LOG="${1:-$(dirname "$CONSTANTS")/$(sed -n 's/^LOG_DIR=//p' "$CONSTANTS")/$(sed -n 's/^LOG_ORADIO=//p' "$CONSTANTS")}"

# Colour escapes precede the timestamp, so strip them before matching.
sed 's/\x1b\[[0-9;]*m//g' "$LOG" \
  | grep -aE "Playing SOUND_START|blinking started|Send TouchButton|Request Transitioning|State changed|Not arming|not found in playlists|Playing current playlist|Resuming current playlist|Play first song|Timeout waiting for MPD lock|Connected to MPD|blinking stopped" \
  | tail -40
