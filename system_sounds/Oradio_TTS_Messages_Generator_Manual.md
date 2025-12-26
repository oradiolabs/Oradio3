# Oradio TTS Messages Generator Manual

## Purpose
This script generates Oradio system prompt **WAV** files using **ElevenLabs TTS** (voice **Roos**), and saves them in:

`/home/pi/Oradio3/system_sounds`

It also:
- normalizes audio to **-1 dBFS** (`sox ... gain -n -1`)
- trims leading/trailing silence (SoX `silence` effect)
- offers a simple playback menu (`aplay`)

## Requirements
Installed tools:
- `sox` + `soxi`
- `curl`
- `openssl`
- `aplay` (alsa-utils)

## Running the script
Run:
```bash
bash ./tts_messages_generator.sh
```

If `ELEVENLABS_API_KEY` is **not** set in your environment, the script asks for the **team password** and decrypts the embedded encrypted API key.

### Force the password prompt
```bash
unset ELEVENLABS_API_KEY
bash ./tts_messages_generator.sh
```

## Adding or changing messages
Edit the `PROMPTS` dictionary:
```bash
declare -A PROMPTS=(
  ["FileName.wav"]="[warmly] Your text here."
)
```

Notes:
- Filenames must end in `.wav`
- Text may include **Eleven v3 audio tags** in brackets, e.g. `[cheerful]`, `[warmly]`, `[brightly]`, `[gently]`

## ElevenLabs settings
Defaults:
- Model: `eleven_v3`
- Voice: Roos (voice id in script)

Optional overrides:
```bash
export ELEVENLABS_SPEED="0.90"      # slower
export ELEVENLABS_MODEL_ID="eleven_v3"
export ELEVENLABS_VOICE_ID="...."
```

## Silence trimming settings
These control trimming sensitivity:
- `MIN_SILENCE_BLOCKS=1`
- `MIN_SILENCE_DURATION=0.3`  *(seconds; use a float)*
- `SILENCE_THRESHOLD="0.1%"`  *(lower = less trimming)*

Tip: If a file starts too abruptly (click), reduce trimming by using a **higher** threshold (e.g. `0.3%` or `1%`) and/or shorter duration.

The script prints how much was trimmed per file.

## Regenerating files
By default, existing WAV files are skipped. To regenerate everything:
```bash
export FORCE_REGENERATE=1
bash ./tts_messages_generator.sh
```

To regenerate only one file, delete it and run again:
```bash
rm -f /home/pi/Oradio3/system_sounds/Preset3_melding.wav
bash ./tts_messages_generator.sh
```

## Output
All WAVs are written to:
`/home/pi/Oradio3/system_sounds`

After generation, the script shows a menu to play files.
