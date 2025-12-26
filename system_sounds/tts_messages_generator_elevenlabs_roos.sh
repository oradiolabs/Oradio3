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

set -euo pipefail

# ----------- PROMPTS --------------------
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

# ---------------- CONFIG ----------------

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# Ensure Bash is used
if [ -z "$BASH" ]; then
	echo -e "${RED}This script requires bash${NC}"
	exit 1
fi

# Check required tools
REQUIRED_PACKAGES=("sox" "curl" "openssl")
for package in "${REQUIRED_PACKAGES[@]}"; do
	if ! command -v "$package" >/dev/null 2>&1; then
		echo -e "${RED}Required package not installed: $package${NC}"
		exit 1
	fi
done

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

ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:-}"

# If not provided via env var, decrypt embedded encrypted key using a team password
if [[ -z "$ELEVENLABS_API_KEY" ]]; then
  read -rsp "ElevenLabs key password: " _EL_PASS; echo

  ELEVENLABS_API_KEY="$(
    printf '%s' "$ELEVENLABS_API_KEY_ENC_B64" | \
      openssl enc -aes-256-cbc -pbkdf2 -d -a -pass pass:"$_EL_PASS" 2>/dev/null
  )" || true

  unset _EL_PASS

  if [[ -z "$ELEVENLABS_API_KEY" ]]; then
    echo -e "${RED}❌ Decryption failed (wrong password?)${NC}"
    exit 1
  fi
fi


# Silence detection parameters
MIN_SILENCE_BLOCKS=1		# Nr of "silence blocks" to detect at beginning
MIN_SILENCE_DURATION=0.3		# Minimum duration in seconds to consider silence, need a float for s
SILENCE_THRESHOLD="0.1%"			# to prevent that low levels are seen as silence

# Speech / voice settings:
# - ELEVENLABS_SPEED is passed directly to ElevenLabs voice_settings.speed.
#   Typical usable range is roughly 0.7..1.2 (experiment).
ELEVENLABS_SPEED="${ELEVENLABS_SPEED:-1.0}"
SPEED="$ELEVENLABS_SPEED"

# Location of generated wav files
OUTPUT_DIR="/home/pi/Oradio3/system_sounds"
mkdir -p "$OUTPUT_DIR"

# Set to 1 to regenerate even if output files already exist
FORCE_REGENERATE="${FORCE_REGENERATE:-0}"

# ----------- FUNCTIONS ------------------

json_escape() {
  # Escapes a string for safe inclusion in JSON string value.
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

	# Validate SPEED: must look like a number. Fallback to 1.0 if not.
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

	echo "🗣️  Generate: $(basename "$outfile") → \"$text\""

	local payload
	payload=$(build_payload "$text")

	# ElevenLabs returns raw PCM (no WAV header) for output_format=pcm_16000.
	# We first download to a temp .pcm, then wrap to a real WAV using sox.
	local tmp_pcm="${outfile}.tmp.pcm"
	local tmp_wav="${outfile}.tmp.wav"

	if ! curl -s -f -X POST "$endpoint" \
		-H "xi-api-key: $ELEVENLABS_API_KEY" \
		-H "Content-Type: application/json" \
		-d "$payload" \
		--output "$tmp_pcm"; then
		echo -e "${RED}❌ Error generating $(basename "$outfile")${NC}"
		rm -f "$tmp_pcm" >/dev/null 2>&1 || true
		return 1
	fi

	# Wrap PCM into WAV: 16 kHz, signed 16-bit little-endian, mono
	if ! sox -t raw -r 16000 -e signed-integer -b 16 -c 1 "$tmp_pcm" "$tmp_wav" gain -n -1 >/dev/null 2>&1; then
		echo -e "${RED}❌ Failed to convert PCM to WAV for $(basename "$outfile")${NC}"
		rm -f "$tmp_pcm" "$tmp_wav" >/dev/null 2>&1 || true
		return 1
	fi

	mv "$tmp_wav" "$outfile"
	rm -f "$tmp_pcm" >/dev/null 2>&1 || true
}

trim_silence() {
  local file="$1"
  echo "🗣️  Trim leading and trailing silence from: $file"

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
    echo "   ⤷ trimmed: ${delta_ms} ms (before ${dur_before}s → after ${dur_after}s)"
    return 0
  else
    echo -e "${RED}❌ Failed to trim silence from $file${NC}"
    rm -f "$tmpfile" >/dev/null 2>&1 || true
    return 1
  fi
}

menu_playback() {
	local files=("$@")
	echo ""
	echo "--- Play --- (number or 'q')"
	while true; do
		local i=1
		for f in "${files[@]}"; do
			echo "[$i] $(basename "$f")"
			((i++))
		done
		read -rp "Choice: " choice
		[[ "$choice" == "q" ]] && break
		if [[ "$choice" =~ ^[0-9]+$ && "$choice" -ge 1 && "$choice" -le "${#files[@]}" ]]; then
			aplay -q "${files[$((choice-1))]}"
		else
			echo -e "${YELLOW}❌ Invalid choice${NC}"
		fi
	done
}

# --------------- MAIN -------------------

generated=()
skipped=()
trimmed=()

for fname in "${!PROMPTS[@]}"; do
	path="$OUTPUT_DIR/$fname"
	if [[ -f "$path" && -s "$path" && "$FORCE_REGENERATE" != "1" ]]; then
		echo "⚡ Already exists, skip: $fname"
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
echo "✅ ${#generated[@]} file(s) generated."
echo "✅ ${#trimmed[@]} file(s) trimmed."
echo "⚡ ${#skipped[@]} file(s) skipped."

echo ""
echo "✅ Done. message audio files saved in $OUTPUT_DIR/"

# Playback menu
all_files=("${generated[@]}" "${skipped[@]}")
if [[ ${#all_files[@]} -gt 0 ]]; then
	menu_playback "${all_files[@]}"
fi
