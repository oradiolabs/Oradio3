#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Juli 12`, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: To generate the system tts voices in .wav file

Update for 0.4.0: OradioAP mode

-------------------------
Genereert WAV-meldingen voor Oradio met Azure-TTS (nl-NL-FennaNeural)
en biedt een simpel menu om ze af te spelen.

De Fenna-stem ondersteunt géén speaking-styles; we gebruiken alleen
pitch en spreeksnelheid (prosody). 
"""

from __future__ import annotations
import logging
import os
import subprocess
import sys

import requests
from requests.exceptions import RequestException

# --------- AUDIO & STEMINSTELLINGEN ---------------------------------

VOICE_NAME  = "nl-NL-FennaNeural"
#VOICE_NAME  = "nl-NL-ColetteNeural"
#VOICE_NAME  = "nl-NL-MaartenNeural"
SPEECH_RATE = "-10%"   # langzamer = duidelijker
PITCH       = "-5%"    # iets lager = minder schel

# --------- TE SPREKEN MELDINGEN ------------------------------------

PROMPTS: dict[str, str] = {
    "Preset1_melding.wav": "één.",
    "Preset2_melding.wav": "twee.",
    "Preset3_melding.wav": "drie.",

    "Next_melding.wav":                "Volgende nummer.",
    "Spotify_melding.wav":             "Spotify afspelen.",
    "NoInternet_melding.wav":          "Geen internetverbinding.",
    "NoUSB_melding.wav":               "USB-geheugenstick verwijderd.",
    "OradioAPstarted_melding.wav":     "Oradio A-P is gestart. Webinterface beschikbaar.",
    "OradioAPstopped_melding.wav":     "Oradio A-P is gestopt.",
    "WifiConnected_melding.wav":       "Verbonden met wifi.",
    "WifiNotConnected_melding.wav":    "Geen wifi-verbinding.",
    "NewPlaylistPreset_melding.wav":   "Nieuwe afspeellijst wordt afgespeeld.",
    "NewPlaylistWebradio_melding.wav": "De gekozen webradio is ingesteld.",
    "USBPresent_melding.wav":          "USB-geheugenstick is aanwezig.",
}

# --------- DIRECTORIES & KEYS --------------------------------------

SCRIPT_DIR   = "/home/pi/Oradio3/install_resources"
OUTPUT_DIR   = "/home/pi/Oradio3/system_sounds"
AZURE_KEY    = os.getenv("AZURE_SPEECH_KEY")
AZURE_REGION = os.getenv("AZURE_REGION", "westeurope")

# --------- HELPERFUNCTIES ------------------------------------------

def build_ssml(text: str) -> str:
    """SSML met alleen prosody (pitch & rate)."""
    return (
        '<speak version="1.0" xml:lang="nl-NL" '
        'xmlns:mstts="http://www.w3.org/2001/mstts">'
        f'<voice name="{VOICE_NAME}">'
        f'<prosody rate="{SPEECH_RATE}" pitch="{PITCH}">{text}</prosody>'
        '</voice></speak>'
    )


def synthesize(text: str) -> bytes:
    """Vraag Azure TTS en krijg WAV-bytes terug."""
    endpoint = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
        "User-Agent": "OradioTTS/1.0",
    }

    response = requests.post(
        endpoint,
        headers=headers,
        data=build_ssml(text).encode("utf-8"),
        timeout=15,
    )
    response.raise_for_status()
    return response.content


def menu_playback(wav_paths: list[str]) -> None:
    """Tekstmenu om WAV’s af te spelen met aplay."""
    if not wav_paths:
        return
    print("\n--- Afspelen --- (nummer of 'q')")
    while True:
        for i, path in enumerate(wav_paths, 1):
            print(f"[{i}] {os.path.basename(path)}")
        choice = input("Keuze: ").strip().lower()
        if choice == "q":
            break
        try:
            idx = int(choice) - 1
            subprocess.run(["aplay", "-q", wav_paths[idx]], check=False)
        except (ValueError, IndexError):
            print("❌ Ongeldige keuze")


# ------------------- MAIN ------------------------------------------

def main() -> None:

    """Stand-alone interactive loop"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not AZURE_KEY:
        sys.exit("❌  AZURE_SPEECH_KEY ontbreekt in environment.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generated: list[str] = []

    for fname, text in PROMPTS.items():
        path = os.path.join(OUTPUT_DIR, fname)
        logging.info("Genereer %-31s → %s", fname, text)
        try:
            audio = synthesize(text)
            with open(path, "wb") as file:
                file.write(audio)
            generated.append(path)
        except RequestException as ex_err:
            logging.error("HTTP-fout: %s", ex_err)
        except OSError as os_err:
            logging.error("Schrijffout: %s", os_err)

    logging.info("%d bestanden gegenereerd\n", len(generated))

    # Trim trailing silence
    cmd = " bash /home/pi/Oradio3/install_resources/trim_system_sounds.sh"
    proc = subprocess.run(cmd, shell = True, capture_output = True, text = True, check = False)
    if proc.returncode != 0:
        logging.error("scriptfout: %s", proc.stderr.strip())
    logging.info("Trim script resultaat:\n%s\n", proc.stdout.strip())

    logging.info("✅  %d bestanden klaar voor gebruik in %s", len(generated), OUTPUT_DIR)

    if generated:
        menu_playback(generated)


if __name__ == "__main__":
    main()
