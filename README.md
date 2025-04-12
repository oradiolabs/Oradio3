# Oradio3
Stichting Oradio Oradio3 sources
This repo contains an install script to load and configure the required to prepare an SD card.
This SD card is then to be used in an Oradio3, available from https://stichtingoradio.nl

## Installation

Create an SD card using the Bookworm 64bit Lite image:
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

At the prompt, to _install the latest release_, execute command:

    source <(curl https://oradiolabs.nl/Oradio3/install)

Or, at the prompt, to _install the main branch_, execute command:

    source <(curl https://oradiolabs.nl/Oradio3/install) main

Or, at the prompt, to _install your branch_, execute command:

    source <(curl https://oradiolabs.nl/Oradio3/install) <branch name>

The script will install and configure required packages and services.

Wait for the installation to finish. <ins>Note</ins>: this can take up to half an hour.

> **Important note for release 0.2.0:**<br>
> The installation script does not automatically reboot to complete the installation.<br>
> So keep an eye on the SSH console output and run <code>sudo reboot</code> when prompted.<br>
> Then SSH back into the Oradio3 and run <code>cd Oradio3; source oradio_install.sh</code><br>
> Again, when prompted, <code>sudo reboot</code> when prompted.

## Update

Connect the Oradio3 to your network with internet access and SSH into the Oradio3 and run command:

    source <(curl https://oradiolabs.nl/Oradio3/update)

Wait for the installation to finish. <ins>Note</ins>: this can take up to 10 minutes.

> **Important note for release 0.2.0:**<br>
> The installation script does not automatically reboot to complete the installation.<br>
> So keep an eye on the SSH console output and run <code>sudo reboot</code> when prompted.<br>

## Finish

The Oradio3 is ready for use when you hear the startup tune (harp).
