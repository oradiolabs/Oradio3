# Oradio3
Stichting Oradio Oradio3 sources
This repo contains an install script to load and configure the required to prepare an SD card.
This SD card is then to be used in an Oradio3, available from https://stichtingoradio.nl

**Installation**

Create an SD card using the Bookworm 64bit Lite image:
- Enable SSH with password authentication
- Configure ssh username and password (e.g. pi and oradio)

Load the SD card in Raspberry Pi 3A+ (e.g. Oradio3 with USB hub and network dongle)

Connect the Raspberry Pi to your network with internet access

Start the Raspberry pi with the SD card

When the Raspberry Pi has started ssh into the raspberry Pi and run command:

    source <(curl https://oradiolabs.nl/Oradio3/install)

The script will install and configure required packages and services.

Activating some changes requires rebooting, so when the script informs a reboot is required:

    sudo reboot

The reboot will finalize the installation and start the Oradio3 application.

**Update**

Connect the Raspberry Pi to your network with internet access and ssh into the raspberry Pi and run command:

    source <(curl https://oradiolabs.nl/Oradio3/update)

The script will install and configure required packages and services.

Activating some changes requires rebooting, so when the script informs a reboot is required:

    sudo reboot

The reboot will finalize the installation and start the Oradio3 application.
