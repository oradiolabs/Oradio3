# How to generate the prompts

Use ElevenLabs: https://elevenlabs.io/app/speech-synthesis/text-to-speech
Create / login with free account: 10000 credits (you need ~1000 credits for all Oradio prompts)

Voice: Roos Dutch professional
Model: Eleven v3
Stability: Natural
Language override: off
Output format: MP3 44.1 kHz (128kbps)

Prompts all start with [ warmly] <prompt>

**Generate using the website:**
1. Generate and tweak until happy with generated speech-synthesis/text-to-speech
2. Download to folder, naming the file <prompt>.mp3
3. Convert to <prompt>.wav with `bash ./tts_mp3_to_wav_converter.sh`

**Generate using the API:**
1. Get a Starter account on https://elevenlabs.io
2. Create prompts with `bash ./tts_prompts_to_wav_generator.sh`
3. Cancel the Starter account

**Pros/cons:**
* Generating via the website gives more control and is free, but takes more steps
* Generating via the API is no control and USD 6 per month (Starter)
