#!/bin/bash

# Controleer of ffmpeg/ffprobe beschikbaar zijn
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1  || ! command -v bc >/dev/null 2>&1; then
	sudo apt-get update
	sudo apt-get install -y ffmpeg bc
fi

# Bewaar de originele system sounds
if ! [ -d "system_sounds_orig" ]; then
    mv system_sounds system_sounds_orig
fi

# Vaste stilte aan eind
silence=0.1

# Maak output directory
mkdir -p system_sounds

# Trim sound files
for f in system_sounds_orig/*.wav; do
	echo "Verwerk: $f"

	# Totale duur in seconden (met decimalen)
	duration=$(ffprobe -v error -show_entries format=duration \
			   -of default=noprint_wrappers=1:nokey=1 "$f")

	# Detecteer laatste stilte (-30dB, min. 0.5s)
	last_silence_start=$(ffmpeg -i "$f" -af "silencedetect=noise=-40dB:d=0.3" \
					-f null - 2>&1 | grep "silence_end" | tail -n 1 | awk '{print $8}')

	if [ -n "$last_silence_start" ]; then
		# Bereken nieuwe lengte tot start van stilte
		new_duration=$(echo "$duration - $last_silence_start + $silence" | bc -l)

		# Zorg dat er altijd een leidende 0 staat
		new_duration=$(printf "%f" "$new_duration")

		# Alleen trimmen als er nog iets overblijft (>0.5s)
		if (( $(echo "$new_duration < $duration" | bc -l) )); then
			out="system_sounds/$(basename "$f")"
			echo "  Stilte vanaf ${new_duration}s → knippen tot $duration → $out"
			ffmpeg -y -i "$f" -t "$new_duration" "$out"
		else
			echo "  Bestand $f heeft geen of te korte stilte aan het eind, overslaan."
		fi
	else
		echo "  Geen stilte gevonden → geen nieuwe file."
	fi
done
