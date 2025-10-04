#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on October 4, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1.2
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Purpose:
#   Checks for missing tools
#   Robust handling if no .wav files exist
#   In-place trimming (tmpfile → overwrite original)
#   Each file trimmed only once

# ============================================
# Color definitions
# ============================================
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# ============================================
# Ensure Bash is used
# ============================================
if [ -z "$BASH" ]; then
	echo -e "${RED}This script requires bash${NC}"
	exit 1
fi

# ============================================
# Check required tools
# ============================================
REQUIRED_PACKAGES=("ffmpeg" "bc")

for package in "${REQUIRED_PACKAGES[@]}"; do
	if ! command -v "$package" >/dev/null 2>&1; then
		echo -e "${RED}Required package not installed: $package${NC}"
		exit 1
	fi
done

# ============================================
# Script paths
# ============================================
SCRIPT_NAME=$(basename "$BASH_SOURCE")
SCRIPT_PATH=$( cd -- "$( dirname -- "${BASH_SOURCE}" )" &> /dev/null && pwd )

SYSTEM_SOUNDS_PATH="$SCRIPT_PATH/../system_sounds"

if [[ ! -d "$SYSTEM_SOUNDS_PATH" ]]; then
	echo -e "${RED}Directory not found: $SYSTEM_SOUNDS_PATH${NC}"
	exit 1
fi

# ============================================
# Silence detection parameters
# ============================================
EXTRA_SILENCE=0.0			# Extra seconds to keep at the end
SILENCE_NOISE_LEVEL="-40dB"	# Noise threshold for silence detection
MIN_SILENCE_DURATION=0.3	# Minimum duration to consider silence

# ============================================
# Function: process one file in-place
# ============================================
trim_file() {
	local infile="$1"
	local marker="${infile}.trimmed"
	local tmpfile="${infile}.tmp.wav"

	# Skip if already trimmed
	if [[ -f "$marker" ]]; then
		echo "Skipping $(basename "$infile"): already trimmed"
		return
	fi

	echo "processing: "$(basename "$infile")

	# Total duration in seconds
	local duration
	duration=$(ffprobe -v error -show_entries format=duration \
				-of default=noprint_wrappers=1:nokey=1 "$infile") || {
		echo -e "${YELLOW}Warning: Could not get duration for $(basename "$infile"), skipping${NC}"
		touch "$marker"
		return
	}
	echo "duration="$duration

	# Detect last silence end time
	# Detect last silence start time
	local last_silence_start
	last_silence_start=$(ffmpeg -i "$infile" -af "silencedetect=noise=$SILENCE_NOISE_LEVEL:d=$MIN_SILENCE_DURATION" \
						-f null - 2>&1 | grep "silence_end" | tail -n 1 | awk '{print $NF}')
	echo "last_silence_start="$last_silence_start

	if [[ -n "$last_silence_start" ]]; then
		local new_duration
		new_duration=$(echo "$duration - $last_silence_start + $EXTRA_SILENCE" | bc -l)
		echo "new_duration="$new_duration

		if (( $(echo "$new_duration > 0 && $new_duration < $duration" | bc -l) )); then
			printf -v new_duration "%f" "$new_duration"
			echo "Trimming $(basename "$infile") to ${new_duration}s (original: ${duration}s)"
			ffmpeg -y -hide_banner -loglevel error -i "$infile" -t "$new_duration" "$tmpfile"
			if [ -f "$tmpfile" ]; then
				mv "$tmpfile" "$infile"
			else
				echo -e "${RED}Failed to create temporary file; keeping original $(basename "$infile")${NC}"
			fi
		else
			echo "No useful silence detected: keeping original $(basename "$infile")"
		fi
	else
		echo "No silence found: keeping original $(basename "$infile")"
	fi

	# Mark file as processed
	touch "$marker"
}

# ============================================
# Main loop over .wav files
# ============================================
shopt -s nullglob
files=("$SYSTEM_SOUNDS_PATH"/*.wav)
shopt -u nullglob

if [ ${#files[@]} -eq 0 ]; then
	echo -e "${RED}No .wav files found in $SYSTEM_SOUNDS_PATH${NC}"
	exit 1
fi

for f in "${files[@]}"; do
	trim_file "$f"
done
