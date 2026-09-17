# Stichting Oradio - Oradio3 sources

This repo contains an install script to load and configure the required to prepare an SD card.
This SD card is then to be used in an Oradio3, available from https://stichtingoradio.nl

## Installation

Create an SD card using the Trixie 64bit Lite image:
- Set hostname (e.g. oradio)
- Enable SSH with password authentication
- Configure SSH username and password (e.g. pi and oradio)
- Optionally you can provide wifi SSID and password for your network with internet access, and Wifi country: NL

Load the SD card in your Oradio3 (Raspberry Pi 3A+).

Connect the Oradio3 to your network with internet access using a USB hub with Ethernet dongle.<br>
<ins>Note</ins>: This is optional if you provided wifi SSID and password when creating the SD card.

Start the Oradio3 with the SD card inserted.

When the Oradio3 has configured itself after first boot SSH into the Oradio3 with your login and password.<br>
<ins>Note</ins>: If you provided a hostname when creating the SD card you can ssh to &lt;hostname&gt;.local

At the prompt, to install the _latest release_, execute command:

    bash <(curl https://oradiolabs.nl/Oradio3/install)

Or, at the prompt, to install the _main branch_, execute command:

    bash <(curl https://oradiolabs.nl/Oradio3/install) main

Or, at the prompt, to install _your branch_, execute command:

    bash <(curl https://oradiolabs.nl/Oradio3/install) <branch name>

The script will install and configure required packages and services.<br>
<ins>Note:</ins> The script may prompt you for the Oradio password.

Wait for the installation to finish.<br>
<ins>Note</ins>: this can take up to half an hour.

## Finish

The Oradio3 is ready for use when you hear the startup tune (harp).

## Thresholds waiting on field data

Three constants decide when the Oradio stops trying and does something drastic.
All three were chosen without measurements, and all three log every event with
its running count, so the logs and the RMS incidents say whether they are set
right. Adjust them on what the fleet reports, not on a fresh guess.

### Under-voltage

`UNDERVOLTAGE_EVENTS = 3` within `UNDERVOLTAGE_WINDOW = 60.0`, in
`Main/rpi_monitor.py`.

Past that, the Oradio plays the power-supply announcement, goes to StateError
and stays there: replacing the supply cuts the power anyway, so playing on only
prolongs the risk to the SD card.

What to look for: `Under-voltage event N of 3` in the log, and
`POWER_UNDERVOLTAGE` in RMS. Every dip is logged with the raw `get_throttled`
word, including the ones that never reach the threshold, so a supply that dips
occasionally shows up long before it trips anything.

Raise the count or shorten the window if working Oradios stop for supplies that
are merely marginal. Lower it if a failing supply takes too long to be caught.
Bit 0 may never appear at all, in which case nothing needs changing.

### Volume control

`SET_FAILURE_LIMIT = 20` within `SET_FAILURE_WINDOW = 60.0`, in
`Main/volume_control.py`.

Past that, the process ends and systemd restarts the service, which re-runs
`oradio-prestart.sh` and its conditional `alsactl restore` -- the one thing that
recreates softvol controls that are missing.

What to look for: `failures=N` in the details of every `VOLUME_SET_FAILED`
incident. One knob turn is ten to fifteen `amixer` calls and a successful call
clears the count, so 20 is only reachable when nothing is working at all. A
control that is simply not there produces a full turn's worth at once and trips
this on the second turn.

Lower it once the logs show how often `amixer` misses in normal use. If
`failures=` never rises above 1, the limit can come down a long way.

### Captive portal

`PORTAL_ATTEMPT_LIMIT = 2` within `PORTAL_ATTEMPT_WINDOW = 300.0`, in
`Main/web_service.py`.

Past that, the process ends and the Oradio restarts, which also frees a uvicorn
thread that would not stop -- something no code inside the process can do.

What to look for: `Captive portal did not start (attempt N of 2 ...)` in the
log. Unlike the other two this one is measured in user actions rather than in
internal events: two long presses that produce no network, close together.

This is the one least likely to need changing, because the number comes from the
user experience rather than from a rate: by the second failed long press they
are standing in front of the Oradio knowing something is wrong, and an Oradio
visibly starting over is an answer rather than a surprise.
