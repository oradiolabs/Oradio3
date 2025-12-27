#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on October 6, 2025
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Purpose:       Generates WAV system prompts using ElevenLabs TTS (Roos)

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
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Ensure connected to internet ----------

if ! ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
	echo -e "${RED}No internet connection${NC}"
	exit 1
else
	echo "Connected to Internet"
fi

#---------- Ensure required packages are installed and up to date ----------

STAMP_FILE="/var/lib/apt/last_update_stamp"
MAX_AGE=$((6 * 3600))	# 6 hours in seconds

# Get last time the list was updated, 0 if never
if [[ -f "$STAMP_FILE" ]]; then
	last_update=$(cat "$STAMP_FILE")
else
	last_update=0
fi

# Get time since last update
current_time=$(date +%s)
age=$((current_time - last_update))

# Update lists if to old
if (( age > MAX_AGE )); then
	sudo apt-get update
	# Save time lists were updated
	date +%s | sudo tee "$STAMP_FILE" >/dev/null
	echo "Package lists updated"
else
	echo "Package lists are up to date"
fi
# NOTE: We do not upgrade: https://forums.raspberrypi.com/viewtopic.php?p=2310861&hilit=oradio#p2310861

# Fetch list of upgradable packages
UPGRADABLE=$(apt list --upgradable 2>/dev/null | cut -d/ -f1)

# Create associative array for fast lookup
declare -A UPGRADABLE_MAP
for package in $UPGRADABLE; do
	UPGRADABLE_MAP["$package"]=1
done

# Required packages
REQUIRED_PACKAGES=(
	sox
	curl
	openssl
)

# Ensure packages are installed and up-to-date
for package in "${REQUIRED_PACKAGES[@]}"; do
	if dpkg -s "$package" &>/dev/null; then
		# Check if installed package can be upgraded
		if [[ ${UPGRADABLE_MAP["$package"]+_} ]]; then
			echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
			sudo apt-get install -y "$package"
		else
			echo "$package is up-to-date"
		fi
	else
		echo -e "${YELLOW}$package is missing: installing...${NC}"
		sudo apt-get install -y "$package"
	fi
done

#---------- Prompts ----------

declare -A PROMPTS=(
	["Preset1_melding.wav"]="[cheerful] één."
	["Preset2_melding.wav"]="[cheerful] twee."
	["Preset3_melding.wav"]="[cheerful] drie."
	["Next_melding.wav"]="[brightly] Volgende nummer."
	["Spotify_melding.wav"]="[cheerful] Spotify afspelen."
	["WifiConnected_melding.wav"]="[warmly] Verbonden met wifi."
	["USBPresent_melding.wav"]="[warmly] USB-geheugenstick is aanwezig."
	["NewPlaylistPreset_melding.wav"]="[warmly] Nieuwe afspeellijst wordt afgespeeld."
	["NewPlaylistWebradio_melding.wav"]="[warmly] De gekozen webradio is ingesteld."

	# “negative” states: keep it gentle/warm, not alarming
	["NoInternet_melding.wav"]="[gently] Geen internetverbinding."
	["WifiNotConnected_melding.wav"]="[gently] Geen wifi-verbinding."
	["NoUSB_melding.wav"]="[gently] USB-geheugenstick verwijderd."

	["OradioAPstarted_melding.wav"]="[warmly] Oradio A P is gestart. Webinterface beschikbaar."
	["OradioAPstopped_melding.wav"]="[warmly] Oradio A P is gestopt."
)
#  Emontional expressions in ELEVENLABS_MODEL_ID:-eleven_v3
#  [happily], [cheerful], [brightly], [warmly]

#---------- Config ----------

# Text-to-speech definitions (ElevenLabs)
# Roos voice_id (your account): 7qdUFMklKPaaAVMsBTBt
# You can override with: export ELEVENLABS_VOICE_ID="..."
VOICE_NAME="Roos"
VOICE_ID="${ELEVENLABS_VOICE_ID:-7qdUFMklKPaaAVMsBTBt}"
MODEL_ID="${ELEVENLABS_MODEL_ID:-eleven_v3}"
OUTPUT_FORMAT="${ELEVENLABS_OUTPUT_FORMAT:-pcm_16000}"   # returns raw PCM S16LE, mono, at 16 kHz

# Encrypted ElevenLabs API key (base64)
ELEVENLABS_API_KEY_ENC_B64="$(cat <<'EOF'
U2FsdGVkX18TGnkyvrjPOYTc9ZSXRs4e/HJH4niXEinWqMM/xIdwMKu2em1OroUT
KcD0Pq9AJVolRUzEOPBMZ9GqKE0johTPWt21OZ0bEP0=
EOF
)"

# Prompt for password, show * for entered charracters, supporting backspace
PW=""
echo -n "Enter ElevenLabs key password: "
while IFS= read -r -s -n1 char; do

	# Break on Enter (newline or carriage return)
	[[ -z "$char" || $char == $'\n' || $char == $'\r' ]] && break

	if [[ $char == $'\177' ]]; then
		# Handle backspace
		if [ -n "$PW" ]; then
			PW=${PW%?}
			echo -ne "\b \b"
		fi
	else
		PW+="$char"
		echo -n "*"
	fi
done
echo

# Decrypt API key with given password
ELEVENLABS_API_KEY="$(
	printf '%s' "$ELEVENLABS_API_KEY_ENC_B64" | \
	openssl enc -aes-256-cbc -pbkdf2 -d -a -pass pass:"$PW" 2>/dev/null
)" || true

# Remove password
unset PW

if [ -z "${ELEVENLABS_API_KEY:-}" ] || \
		! HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H \
			"xi-api-key: $ELEVENLABS_API_KEY" https://api.elevenlabs.io/v1/voices) || \
		[ "$HTTP_STATUS" -ne 200 ]; then
	echo -e "${RED}Invalid ElevenLabs API key: wrong password?${NC}"
	exit 1
fi

# Silence detection parameters
MIN_SILENCE_BLOCKS=1		# Nr of "silence blocks" to detect at beginning
MIN_SILENCE_DURATION=0.3	# Minimum duration in seconds to consider silence, need a float for s
SILENCE_THRESHOLD="0.1%"	# to prevent that low levels are seen as silence

# Speech / voice settings:
# - ELEVENLABS_SPEED is passed directly to ElevenLabs voice_settings.speed.
#   Typical usable range is roughly 0.7..1.2 (experiment).
ELEVENLABS_SPEED="${ELEVENLABS_SPEED:-1.0}"
SPEED="$ELEVENLABS_SPEED"

# Location of generated wav files
OUTPUT_DIR="$HOME/Oradio3/system_sounds"
mkdir -p "$OUTPUT_DIR"

# Default: overwrite
FORCE_REGENERATE=true

# Prompt for overwrite or check only
read -r -p "Overwrite existing sound files[Y/n]: " answer
if [[ "$answer" =~ ^[Nn]$ ]]; then
	FORCE_REGENERATE=false
	echo -e "${YELLOW}Existing system sound files will not be overwritten${NC}"
fi

#---------- Functions ----------

json_escape() {
	# Escapes a string for safe inclusion in JSON string value
	local s="$1"
	s="${s//\\/\\\\}"
	s="${s//\"/\\\"}"
	s="${s//$'\n'/\\n}"
	s="${s//$'\r'/\\r}"
	s="${s//$'\t'/\\t}"
	printf '%s' "$s"
}

build_payload() {
	local text="$1"

	# Validate SPEED: must look like a number. Fallback to 1.0 if not
	if [[ ! "$SPEED" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
		SPEED="1.0"
	fi

	local esc
	esc="$(json_escape "$text")"

	# Note: speed is sent as JSON number (not quoted)
	printf '{"text":"%s","model_id":"%s","voice_settings":{"speed":%s}}' "$esc" "$MODEL_ID" "$SPEED"
}

synthesize() {
	local text="$1"
	local outfile="$2"
	local endpoint="https://api.elevenlabs.io/v1/text-to-speech/${VOICE_ID}?output_format=${OUTPUT_FORMAT}"

	echo "Generate: $(basename "$outfile") → \"$text\""

	local payload
	payload=$(build_payload "$text")

	# ElevenLabs returns raw PCM (no WAV header) for output_format=pcm_16000
	# We first download to a temp .pcm, then wrap to a real WAV using sox
	local tmp_pcm="${outfile}.tmp.pcm"
	local tmp_wav="${outfile}.tmp.wav"

    # Make POST request, capture both body and HTTP status
    local response http_status body
    response=$(curl -s -w "\n%{http_code}" -X POST "$endpoint" \
        -H "xi-api-key: $ELEVENLABS_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --output "$tmp_pcm" || true)

    http_status=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')

    # Make POST request, capture both body and HTTP status
    local response http_status body
    response=$(curl -s -w "\n%{http_code}" -X POST "$endpoint" \
        -H "xi-api-key: $ELEVENLABS_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --output "$tmp_pcm" || true)

    local http_status=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')

    if [ "$http_status" -eq 200 ]; then
		# Wrap PCM into WAV: 16 kHz, signed 16-bit little-endian, mono
		if ! sox -t raw -r 16000 -e signed-integer -b 16 -c 1 "$tmp_pcm" "$tmp_wav" gain -n -1 >/dev/null 2>&1; then
			echo -e "${RED}Failed to convert PCM to WAV for $(basename "$outfile")${NC}"
			rm -f "$tmp_pcm" "$tmp_wav"
			return 1
		fi

		mv "$tmp_wav" "$outfile"
		rm -f "$tmp_pcm"

    elif [ "$http_status" -eq 401 ]; then
		# Attempt to extract API error message from tmp.pcm
		if jq -e '.detail.message' "$tmp_pcm" >/dev/null 2>&1; then
			error_msg=$(jq -r '.detail.message' "$tmp_pcm")
			echo -e "${RED}Error generating $(basename "$outfile"): $error_msg${NC}"
		else
			echo -e "${RED}Error generating $(basename "$outfile")${NC}"
		fi
		rm -f "$tmp_pcm"
		return 1

	else
		echo -e "${RED}Unexpeced error from ElevenLabs API:\nHTTP status='$http_status'\nbody= '$body'\nCheck system_sounds/*.tmp.pcm for details"
		return 1
	fi
}

trim_silence() {
	local file="$1"
	echo "Trim leading and trailing silence from: $file"

	local tmpfile="${file}.tmp.wav"

	# Duration before (seconds, float)
	local dur_before dur_after delta_ms
	dur_before="$(soxi -D "$file" 2>/dev/null || echo 0)"

	if sox "$file" "$tmpfile" \
		silence $MIN_SILENCE_BLOCKS $MIN_SILENCE_DURATION $SILENCE_THRESHOLD \
		reverse silence $MIN_SILENCE_BLOCKS $MIN_SILENCE_DURATION $SILENCE_THRESHOLD \
		reverse >/dev/null 2>&1; then

		dur_after="$(soxi -D "$tmpfile" 2>/dev/null || echo 0)"
		delta_ms="$(awk -v b="$dur_before" -v a="$dur_after" 'BEGIN { printf "%.0f", (b-a)*1000 }')"

		mv "$tmpfile" "$file"
		echo "Trimmed: ${delta_ms} ms (before ${dur_before}s → after ${dur_after}s)"
		return 0
	else
		echo -e "${RED}Failed to trim silence from $file${NC}"
		rm -f "$tmpfile" >/dev/null 2>&1 || true
		return 1
	fi
}

menu_playback() {
	local files=("$@")
	echo ""
	echo "--- Play --- (number or '0' to quit)"
	while true; do
		local i=1
		for f in "${files[@]}"; do
			echo "[$i] $(basename "$f")"
			((i++))
		done
		read -rp "Choice: " choice
		[[ "$choice" == "0" ]] && break
		if [[ "$choice" =~ ^[0-9]+$ && "$choice" -ge 1 && "$choice" -le "${#files[@]}" ]]; then
			aplay -q "${files[$((choice-1))]}"
		else
			echo -e "${YELLOW}Invalid choice${NC}"
		fi
	done
}

sort_array() {
	local -n _arr=$1
	mapfile -t _arr < <(printf '%s\n' "${_arr[@]}" | sort)
}

#---------- Main ----------

generated=()
skipped=()
trimmed=()

for fname in "${!PROMPTS[@]}"; do
	path="$OUTPUT_DIR/$fname"
	if [[ -f "$path" && -s "$path" && "$FORCE_REGENERATE" != "true" ]]; then
		echo "Already exists, skip: $fname"
		skipped+=("$path")
		continue
	fi
	# Generate
	if synthesize "${PROMPTS[$fname]}" "$path"; then
		generated+=("$path")
		# Trim
		if trim_silence "$path"; then
			trimmed+=("$path")
		fi
	fi
done

echo ""
echo -e "${GREEN}${#generated[@]} file(s) generated.${NC}"
echo -e "${GREEN}${#trimmed[@]} file(s) trimmed.${NC}"
echo -e "${YELLOW}${#skipped[@]} file(s) skipped.${NC}"

echo ""
echo -e "${GREEN}Done. message audio files saved in $OUTPUT_DIR/${NC}"

# Playback menu (sorted)
all_files=("${generated[@]}" "${skipped[@]}")
sort_array all_files
if [[ ${#all_files[@]} -gt 1 ]]; then
	menu_playback "${all_files[@]}"
fi
