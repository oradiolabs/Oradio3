#!/usr/bin/env bash
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
# @version:       2
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Reference:
#	https://elevenlabs.io/docs/overview/capabilities/text-to-speech/
#	https://elevenlabs.io/docs/overview/capabilities/voices
# @Purpose:
#	This script generates Oradio system prompt WAV files using ElevenLabs TTS Eleven v3, voice 'Roos'
#	- trims leading/trailing silence
#	- normalizes to -16.5 LUFS loudness (ffmpeg `loudnorm`, two pass, EBU R128)
#	- offers a simple playback menu (`aplay`, through the Oradio's own ALSA device)
#
#	Trimming and normalizing are the same two ffmpeg passes mp3_to_wav_converter.sh
#	uses, so a message generated here and one converted from a hand made recording
#	come out at the same level. Peak normalization (`sox gain -n -1`) is gone: it
#	lines up the loudest sample of each file, which is not the same as lining up
#	how loud they sound. On the previous set that left almost 7 dB between the
#	softest and the loudest message
# @Usage:
#	./tts_messages_generator.sh OUTPUT_DIR
#	./tts_messages_generator.sh --help

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
	echo -e "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Prompts ----------

declare -A PROMPTS=(
	["NewPlaylistPreset_melding.wav"]="Nieuwe afspeellijst wordt afgespeeld."
	["NewPlaylistWebradio_melding.wav"]="De gekozen webradio is ingesteld."
	["Next_melding.wav"]="Volgende nummer."
	["NoInternet_melding.wav"]="Geen internetverbinding."
	["OradioAPstarted_melding.wav"]="Oradio A P is gestart. Webinterface beschikbaar."
	["OradioAPstopped_melding.wav"]="Oradio A P is gestopt."
	["OradioPowerError_melding.wav"]="De Oradio heeft niet de juiste voeding. Gebruik de originele bijgeleverde voeding."
	["Preset1_melding.wav"]="één"
	["Preset2_melding.wav"]="twee."
	["Preset3_melding.wav"]="drie!"
	["USBAbsent_melding.wav"]="USB geheugenstick is verwijderd."
	["USBPresent_melding.wav"]="USB-geheugenstick is aanwezig."
	["WifiConnected_melding.wav"]="Verbonden met wifi."
	["WifiNotConnected_melding.wav"]="Geen WIEFIE verbinding."
)

#---------- Config ----------

# Text-to-speech definitions (ElevenLabs)
#
# The voice is looked up by name rather than pinned to an ID, so this stays
# readable and survives recreating the clone. The lookup shares the same
# /v1/voices call that validates the key, so it costs no extra request
#
# 'Roos' is a Professional Voice Clone and needs a Creator plan or above: Voice
# Library and cloned voices are not available through the API on the free tier.
# The default voices this script used before (Lily) all expire on December 31,
# 2026, so pinning to one of those is not an option any more either
# Matched case insensitively, and a partial name is enough: 'Roos' finds a
# voice the account lists as 'Roos dutch professional'. The script stops if the
# name matches more than one voice
VOICE_NAME="Roos dutch professional voice"
MODEL_ID="eleven_v3"		# Set TTS model to use
OUTPUT_FORMAT="pcm_16000"	# returns raw PCM S16LE, mono, at 16 kHz

# Voice settings, matching what the web interface was set to when the reference
# recordings were made (its file names encode them as sp100_s50_sb83)
#
# STABILITY only accepts 0, 0.5 or 1 on v3: creative, natural and robust. Robust
# is the least expressive and the most repeatable, which is what unchanging
# system messages want. Natural is what the reference recordings used
STABILITY=0.5
SIMILARITY_BOOST=0.83
SPEED=1.0

# Silence detection and loudness parameters, identical to the ones in
# mp3_to_wav_converter.sh so both scripts produce interchangeable files
#
# SILENCE_THRESHOLD: level below which audio counts as silence. Raise it (-40dB)
#                    to trim more, lower it (-60dB) to trim less
# SILENCE_DURATION:  how long the audio must stay above the threshold before
#                    trimming stops
# SILENCE_KEEP:      seconds of silence to leave at each end. Also keeps a
#                    prompt from starting on an audible click
SILENCE_THRESHOLD="-50dB"
SILENCE_DURATION=0
SILENCE_KEEP=0.1

# LOUDNESS_TARGET: integrated loudness in LUFS, the level every file is lifted
#                  or lowered to
# TRUE_PEAK_MAX:   ceiling in dBTP. Kept below 0 to leave room for intersample
#                  peaks that only appear after the DAC reconstructs the signal
# LOUDNESS_RANGE:  allowed spread between soft and loud parts, in LU
#
# -16.5 is the average loudness of the older system_sounds set. Those files were
# peak normalized to -1.0 dBFS rather than loudness normalized, so they do not
# sit at one level at all: the fourteen spoken ones run from -20.2 to -13.4
# LUFS, a spread of 6.8 LU, with mean and median both -16.6. Matching that
# average is what makes a message generated here sound as loud as the ones
# already on the device
#
# The earlier -19 was chosen because at -16 the target and the peak ceiling
# fought each other and the set spread out again. That is still true, but far
# less so once the ceiling moves from -1.5 to -1.0 dBTP. Verified end to end on
# the fifteen ElevenLabs 'Roos' MP3s, decoded and trimmed by this same chain:
#
#   target/ceiling   mean      spread   worst file
#   -19   / -1.5     -19.23    1.12 LU  -20.14   (the old setting)
#   -17   / -1.0     -17.37    1.09 LU  -18.11
#   -16.5 / -1.0     -16.99    1.38 LU  -17.90   (this setting)
#   -16.5 / -1.5     -17.20    1.88 LU  -18.40
#   -16   / -1.0     -16.70    1.88 LU  -17.90
#
# At -16.5/-1.0 ten of the fifteen land on target and five give back gain to
# stay under the ceiling, three of them by about 1.3 LU. That leaves the set
# 0.42 LU below the reference average, well under the roughly 1 LU that is
# audible on program material, and holds the spread to 1.4 LU against the 6.8 LU
# of the set being matched. Going to -16 closes that last 0.4 LU but widens the
# spread to 1.9, which is the wrong trade: an even set slightly below the
# reference beats an uneven set centred on it
#
# The five that fall short are the ones with the highest crest factor, up to
# 19.6 dB between integrated loudness and true peak - long messages with one
# loud plosive. Reaching -16.5 on those needs about 4 dB of limiting, which was
# tried (constant gain plus alimiter instead of the loudnorm second pass) and
# bought only 0.2 LU of level and 0.3 LU of spread in exchange for compressing
# the dynamics of two prompts. Not worth it
#
# TRUE_PEAK_MAX at -1.0 rather than -1.5 is worth about 0.2 LU of level and
# 0.5 LU of spread, and is no more daring than the reference set itself, whose
# files sit at -1.0 dBFS sample peak and measure up to -0.67 dBTP once
# intersample peaks are counted. Move it back to -1.5 if a device ever distorts
# on the loudest prompts, and expect the numbers in the table above
#
# If the whole set sounds too loud or too soft on the Oradio speaker, change the
# playback volume first. Only change this number to track the reference set
LOUDNESS_TARGET="-16.5"
TRUE_PEAK_MAX="-1.0"
LOUDNESS_RANGE="11"

# ALSA device the Oradio itself uses to play these files. system_sounds.py calls
# aplay with -D SysSound_in, while a bare "aplay file.wav" goes to the default
# device instead. Those are two different playback paths and a named PCM can
# carry its own gain - a softvol plugin, a route with attenuation, a dmix slave
# with its own mixer element - so the same WAV can come out at two different
# levels depending on which one you audition through. Comparing a file played
# here against a sound played by the Oradio is only meaningful when both go
# through this device
#
# Left empty, or set to a device that does not exist, playback falls back to the
# default device and says so
PLAYBACK_DEVICE="SysSound_in"


# Trim both ends, then measure or correct the loudness of what is left. Built
# once and shared by both passes, so the second pass cannot normalize different
# audio than the first pass measured
#
# areverse buffers the whole stream, which is why the trailing silence is
# handled by reversing twice rather than by stop_periods: it keeps the pauses
# inside a sentence intact, and system prompts are short enough that buffering
# them costs nothing
TRIM_FILTER="silenceremove=start_periods=1:start_duration=${SILENCE_DURATION}:start_silence=${SILENCE_KEEP}:start_threshold=${SILENCE_THRESHOLD}:detection=rms"
TRIM_FILTER="${TRIM_FILTER},areverse,${TRIM_FILTER},areverse"

# Raw PCM has no header, so the reader has to be told what it is receiving.
# Must agree with OUTPUT_FORMAT above
PCM_INPUT=(-f s16le -ar 16000 -ac 1)

#---------- Usage ----------

usage() {
	cat <<EOF
Usage: ${0##*/} OUTPUT_DIR
       ${0##*/} -h | --help

Generates the ${#PROMPTS[@]} Oradio system prompt WAV files with ElevenLabs text to speech,
using the voice '${VOICE_NAME}' and model ${MODEL_ID}.

Arguments:
  OUTPUT_DIR   Folder to write the WAV files to. Created when it does not exist.
               Existing WAV files are kept unless you confirm overwriting

Options:
  -h, --help   Show this text and exit

The spoken texts live in the PROMPTS table near the top of this script, which
also fixes the file names. Each file is fetched as ${OUTPUT_FORMAT/pcm_/} Hz mono PCM,
trimmed at both ends and normalized to -16.5 LUFS, then offered in a playback menu.

The ElevenLabs key is read from ELEVENLABS_API_KEY when that is set. Without it
the script asks for the password of the encrypted key stored in this file.

Example:
  ${0##*/} \$HOME/Oradio3/tts_sounds
EOF
}

#---------- Parse arguments ----------

# Handled first, before the package check, the server check and the password
# prompt. Asking for a password and installing packages to then find out the
# output folder is missing wastes the run, and --help has to stay instant
case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

if [ "$#" -ne 1 ]; then
	echo -e "${RED}Expected one argument: the output folder${NC}" >&2
	echo "" >&2
	usage >&2
	exit 1
fi

# Location of generated wav files
OUTPUT_DIR="$1"

if ! mkdir -p "$OUTPUT_DIR"; then
	echo -e "${RED}Cannot create output folder: $OUTPUT_DIR${NC}" >&2
	exit 1
fi

#---------- Locate the helper scripts ----------

# pkg-helper.sh sits next to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

#---------- Ensure required packages are installed and up to date ----------

# Before the ElevenLabs check, not after it: curl is one of the packages below,
# and on a Lite image it need not be there. Testing the server first would fail
# with 'command not found' and report it as "no access to ElevenLabs".
#
# The same helper oradio_install.sh and USB_sync.sh use, so the stale-list
# stamp, the apt lock and the definition of "installed and current" are shared
# instead of reimplemented here. It makes no network call when the packages are
# already installed and the lists are fresh, and exits non-zero if any package
# is missing afterwards - which 'set -e' turns into an exit, so there is nothing
# to check here
#
# ffmpeg replaces sox: it wraps the raw PCM, trims and normalizes in one filter
# chain, and sox cannot do the loudness part at all
#
# alsa-utils is for aplay, used by the playback menu at the end. Requested here
# with the rest rather than just before the menu: the menu is the last thing
# that runs, and finding out then that the package is missing wastes the whole
# generation run
bash "$SCRIPT_DIR/pkg-helper.sh" jq ffmpeg curl openssl alsa-utils

#---------- Ensure access to ElevenLabs ----------

if ! curl -s --head https://api.elevenlabs.io >/dev/null 2>&1; then
	echo -e "${RED}No access to ElevenLabs server${NC}"
	exit 1
fi

#---------- ElevenLabs API key ----------

# Encrypted Oradio ElevenLabs API key (base64)
ELEVENLABS_API_KEY_ENC_B64="$(cat <<'EOF'
U2FsdGVkX18TGnkyvrjPOYTc9ZSXRs4e/HJH4niXEinWqMM/xIdwMKu2em1OroUT
KcD0Pq9AJVolRUzEOPBMZ9GqKE0johTPWt21OZ0bEP0=
EOF
)"

# Skip decryption if ELEVENLABS_API_KEY is already set as environment variable
if [[ -n "${ELEVENLABS_API_KEY:-}" ]]; then
	echo -e "${YELLOW}Using ELEVENLABS_API_KEY from environment${NC}"
else
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
fi

if [ -z "${ELEVENLABS_API_KEY:-}" ]; then
	echo -e "${RED}Invalid ElevenLabs API key: wrong password?${NC}"
	exit 1
fi

#---------- Validate the key and resolve the voice ----------

# One request does both jobs: the status code says whether the key is accepted,
# and the body holds the voices the account can reach, which is where the ID for
# VOICE_NAME comes from. Splitting this into a validation call and a lookup call
# would ask the API the same question twice
VOICES_RESPONSE="$(curl -s -w '\n%{http_code}' \
	-H "xi-api-key: $ELEVENLABS_API_KEY" \
	https://api.elevenlabs.io/v1/voices || true)"

# The status is on the last line, the JSON body is everything before it
HTTP_STATUS="${VOICES_RESPONSE##*$'\n'}"
VOICES_JSON="${VOICES_RESPONSE%$'\n'*}"

if [ "$HTTP_STATUS" != "200" ]; then
	echo -e "${RED}Invalid ElevenLabs API key: wrong password? (HTTP $HTTP_STATUS)${NC}"
	exit 1
fi

# Exact match wins. Failing that, a case insensitive substring match, so
# VOICE_NAME can stay a short label like 'Roos' while the account calls the
# voice something longer such as 'Roos dutch professional'. A trailing space in
# the name, which is invisible in the dashboard, breaks an exact match too
mapfile -t VOICE_MATCHES < <(jq -r --arg name "$VOICE_NAME" '
	( [ .voices[] | select(.name == $name) ] ) as $exact
	| ( if ($exact | length) > 0 then $exact
		else [ .voices[]
			   | select(.name | ascii_downcase | contains($name | ascii_downcase)) ]
		end )
	| .[] | "\(.voice_id)\t\(.name)\t\(.category)"' <<<"$VOICES_JSON")

if [ "${#VOICE_MATCHES[@]}" -eq 0 ]; then
	echo -e "${RED}Voice '$VOICE_NAME' is not available on this account${NC}"
	echo "Voices this key can reach, names in brackets to expose stray spaces:"
	jq -r '.voices[] | "  [\(.name)]  (\(.category))"' <<<"$VOICES_JSON" || true
	echo -e "${YELLOW}A cloned or Voice Library voice needs a Creator plan or above${NC}"
	exit 1
fi

# Rather than silently taking the first hit: picking the wrong clone would only
# show up when someone listens to the result
if [ "${#VOICE_MATCHES[@]}" -gt 1 ]; then
	echo -e "${RED}'$VOICE_NAME' matches more than one voice:${NC}"
	printf '  %s\n' "${VOICE_MATCHES[@]#*$'\t'}"
	echo -e "${YELLOW}Make VOICE_NAME more specific${NC}"
	exit 1
fi

IFS=$'\t' read -r VOICE_ID VOICE_FULL_NAME VOICE_CATEGORY <<<"${VOICE_MATCHES[0]}"

# A voice showing up in /v1/voices does not mean the API will speak with it.
# On the free tier only 'premade' voices can be synthesized: everything from the
# Voice Library, and every clone, comes back as HTTP 402. That failure arrives
# per request, so without this check the run burns through every prompt before
# reporting the same thing fifteen times
#
# The endpoint is advisory: if it cannot be read the run continues, and a 402
# from the first prompt stops it anyway
SUBSCRIPTION_TIER="$(curl -s -H "xi-api-key: $ELEVENLABS_API_KEY" \
	https://api.elevenlabs.io/v1/user/subscription 2>/dev/null \
	| jq -r '.tier // empty' 2>/dev/null || true)"

if [ "$SUBSCRIPTION_TIER" = "free" ] && [ "$VOICE_CATEGORY" != "premade" ]; then
	echo -e "${RED}Voice '$VOICE_FULL_NAME' is a '$VOICE_CATEGORY' voice and this account is on the free plan${NC}"
	echo -e "${RED}The API refuses those with HTTP 402, even though the voice is listed and the web interface can use it${NC}"
	echo -e "${YELLOW}Either generate in the web interface and run mp3_to_wav_converter.sh on the downloads,${NC}"
	echo -e "${YELLOW}or move to a paid plan, e.g. Starter plan (voor 1 maand)${NC}"
	exit 1
fi

echo -e "${GREEN}Using voice '$VOICE_FULL_NAME' ($VOICE_ID, $VOICE_CATEGORY) with model $MODEL_ID${NC}"

#---------- Overwrite or check only ----------

# By default, existing WAV files are skipped. To regenerate everything:
# To regenerate only one file, delete it: rm -f OUTPUT_DIR/<file>.wav
FORCE_REGENERATE=false

# Prompt for overwrite or check only
read -r -p "Overwrite existing sound files[y/N]: " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
	FORCE_REGENERATE=true
	echo -e "${YELLOW}Existing system sound files will be overwritten${NC}"
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
	local seed="$2"

	local esc
	esc="$(json_escape "$text")"

	# voice_settings reproduces what the web interface was set to. If the API
	# answers 422, the first thing to drop is "language_code": v3 infers the
	# language from the text and does not accept the hint on every endpoint
	printf '{"text":"%s","model_id":"%s","language_code":"nl","seed":%d,"voice_settings":{"stability":%s,"similarity_boost":%s,"speed":%s}}' \
		"$esc" "$MODEL_ID" "$seed" "$STABILITY" "$SIMILARITY_BOOST" "$SPEED"
}

synthesize() {
	# Downloads one prompt as raw PCM. Wrapping, trimming and normalizing are
	# left to normalize(), which does all three in the same filter chain
	local text="$1"
	local outpcm="$2"
	local seed="$3"
	local label="${outpcm##*/}"
	label="${label%.tmp.pcm}"
	local endpoint="https://api.elevenlabs.io/v1/text-to-speech/${VOICE_ID}?output_format=${OUTPUT_FORMAT}"

	local payload
	payload=$(build_payload "$text" "$seed")
	echo "Generate ${label} with payload: \"${payload}\""

	# The body goes to the file, so on an error that file holds the API's JSON
	# explanation and the status comes back on stdout
	local http_status
	http_status=$(curl -s -w "%{http_code}" -X POST "$endpoint" \
		-H "xi-api-key: $ELEVENLABS_API_KEY" \
		-H "Content-Type: application/json" \
		-d "$payload" \
		--output "$outpcm" || true)

	if [ "$http_status" = "200" ]; then
		return 0
	fi

	# Any non-200 leaves a JSON error document in the file. 401 is a rejected
	# key, 422 an unacceptable payload, 429 a rate limit
	local error_msg
	error_msg="$(jq -r '.detail.message // .detail[0].msg // .detail // empty' "$outpcm" 2>/dev/null || true)"
	if [ -n "$error_msg" ]; then
		echo -e "${RED}Error generating ${label} (HTTP $http_status): $error_msg${NC}"
	else
		echo -e "${RED}Error generating ${label} (HTTP $http_status)${NC}"
	fi
	rm -f "$outpcm"

	# 401 wrong key, 402 plan does not allow this voice, 403 forbidden: the next
	# prompt will fail identically, so stop instead of repeating it
	case "$http_status" in
		401|402|403) return 2 ;;
	esac
	return 1
}

normalize() {
	# Turns the downloaded raw PCM into the finished WAV: trims both ends and
	# loudness normalizes, in two ffmpeg passes
	#
	# Two passes rather than one because single pass loudnorm adapts while it
	# runs, which changes the dynamics of the recording. Fed the measurements up
	# front with linear=true it applies one constant gain instead, leaving the
	# recording intact and only moving its level
	local src="$1"
	local dst="$2"

	local settings="I=${LOUDNESS_TARGET}:TP=${TRUE_PEAK_MAX}:LRA=${LOUDNESS_RANGE}"

	# Pass 1: trim and measure, discarding the audio. Measuring after the trim
	# matters: a long lead-in would drag the integrated loudness down and the
	# correction with it
	#
	# apad is in this pass only. EBU R128 measures in blocks of 400 ms, so a
	# prompt shorter than one block has no measurable loudness at all and
	# loudnorm reports -inf - which is exactly what happens to 'een' and 'drie'.
	# Padding to a second lets the gate open. It does not skew the result,
	# because the gate discards silence anyway: measured against an unpadded
	# run, a longer prompt comes out at the same value to the decimal
	local in_i="" in_tp="" in_lra="" in_thresh="" offset=""
	local line key value

	# loudnorm prints its JSON on stderr, mixed in with the ffmpeg log. Rather
	# than isolating the document and handing it to jq, pick the five fields out
	# of the stream directly - a JSON parser is a lot of machinery for five
	# numbers on five predictable lines
	while IFS= read -r line; do
		[[ "$line" =~ \"([a-z_]+)\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] || continue
		key="${BASH_REMATCH[1]}"
		value="${BASH_REMATCH[2]}"
		case "$key" in
			input_i)       in_i="$value" ;;
			input_tp)      in_tp="$value" ;;
			input_lra)     in_lra="$value" ;;
			input_thresh)  in_thresh="$value" ;;
			target_offset) offset="$value" ;;
		esac
	done < <(ffmpeg -hide_banner -nostdin "${PCM_INPUT[@]}" -i "$src" \
		-af "${TRIM_FILTER},apad=whole_dur=1.0,loudnorm=${settings}:print_format=json" \
		-f null - 2>&1)

	if [ -z "$in_i" ] || [ "$in_i" = "-inf" ]; then
		echo -e "${RED}Failed to measure loudness of ${dst##*/}${NC}"
		return 1
	fi

	# Pass 2: trim and correct, writing the finished WAV
	# The explicit -ar matters: loudnorm resamples to 192 kHz internally, and
	# without it that is also what ends up in the output file
	if ! ffmpeg -v error -nostdin -y "${PCM_INPUT[@]}" -i "$src" \
			-af "${TRIM_FILTER},loudnorm=${settings}:measured_I=${in_i}:measured_TP=${in_tp}:measured_LRA=${in_lra}:measured_thresh=${in_thresh}:offset=${offset}:linear=true" \
			-ar 16000 -ac 1 -c:a pcm_s16le \
			"$dst"; then
		echo -e "${RED}Failed to normalize ${dst##*/}${NC}"
		rm -f "$dst"
		return 1
	fi

	echo "Loudness: ${in_i} LUFS → ${LOUDNESS_TARGET} LUFS (peak was ${in_tp} dBTP)"
	return 0
}

menu_playback() {
	local files=("$@")

	# Audition through the same device system_sounds.py plays through, so what is
	# heard here is what the Oradio will produce. aplay -L lists every PCM ALSA
	# knows about, including the ones defined in /etc/asound.conf
	local -a device=()
	if [ -n "${PLAYBACK_DEVICE:-}" ]; then
		if aplay -L 2>/dev/null | grep -q "^${PLAYBACK_DEVICE}$"; then
			device=(-D "$PLAYBACK_DEVICE")
			echo -e "${GREEN}Playing through ALSA device '${PLAYBACK_DEVICE}'${NC}"
		else
			echo -e "${YELLOW}ALSA device '${PLAYBACK_DEVICE}' not found, using the default device."
			echo -e "Levels heard here may not match what the Oradio plays${NC}"
		fi
	fi

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
			aplay -q ${device[@]+"${device[@]}"} "${files[$((choice-1))]}"
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
normalized=()
seed=100

for fname in "${!PROMPTS[@]}"; do
	path="$OUTPUT_DIR/$fname"
	if [[ -f "$path" && -s "$path" && "$FORCE_REGENERATE" = "false" ]]; then
		echo "Already exists, skip: $fname"
		skipped+=("$path")
		continue
	fi

	# The raw PCM is a scratch file: ffmpeg reads it twice, then it is dropped
	tmp_pcm="${path}.tmp.pcm"

	# Generate
	synthesize "${PROMPTS[$fname]}" "$tmp_pcm" "$seed" && rc=0 || rc=$?
	if [ "$rc" -eq 2 ]; then
		echo -e "${RED}Aborting: this failure applies to every prompt${NC}"
		break
	fi
	if [ "$rc" -eq 0 ]; then
		generated+=("$path")
		# Trim and normalize
		if normalize "$tmp_pcm" "$path"; then
			normalized+=("$path")
		fi
		rm -f "$tmp_pcm"
	fi
	seed=$((seed + 100))
done

echo ""
echo -e "${GREEN}${#generated[@]} file(s) generated.${NC}"
echo -e "${GREEN}${#normalized[@]} file(s) trimmed and normalized.${NC}"
echo -e "${YELLOW}${#skipped[@]} file(s) skipped.${NC}"

echo ""
echo -e "${GREEN}Done. message audio files saved in $OUTPUT_DIR/${NC}"

# Playback menu (sorted)
all_files=("${generated[@]}" "${skipped[@]}")
sort_array all_files
if [[ ${#all_files[@]} -gt 1 ]]; then
	menu_playback "${all_files[@]}"
fi
