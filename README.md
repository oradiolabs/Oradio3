# Oradio3
Stichting Oradio Oradio3 sources
This repo contains an install script to load and configure the required to prepare an SD card.
This SD card is then to be used in an Oradio3, available from https://stichtingoradio.nl

Installation
- Create an SD card using the Bookworm 64bit Lite image;
  - In General tab: set username and password (e.g. pi and oradio);
  - In Services tab: enable SSH with password authentication
- Load the SD card in Raspberry Pi 3A+ (e.g. Oradio3 with USB hub and network dongle)
- Connect the Raspberry Pi to your network with internet access
- Start the Raspberry pi with the SD card
- When the Raspberry Pi has started ssh into the raspberry Pi
- #> sudo apt-get install git -y
- #> git clone https://github.com/oradiolabs/Oradio3.git
- #> cd Oradio3
- #> source ./oradio_install.sh    <== Note the 'source' command. Required as the scripts changes environment settings

The script will install and configure required packages and services
Activating some changes requires rebooting, so when the script informs a reboot is required:
- #> sudo reboot
- When the Raspberry Pi has restarted ssh into the raspberry Pi
- #> cd Oradio3
- #> source ./oradio_install.sh    <== Note the 'source' command. Required as the scripts changes environment settings

When the script is finished it asks you to reboot. This will start the Oradio3 application
