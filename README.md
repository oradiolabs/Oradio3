# Oradio3
Stichting Oradio Oradio3 sources
This repo contains an install script to load and configure the required to prepare an SD card.
This SD card is then to be used in an Oradio3, available from https://stichtingoradio.nl

Installation
- Create an SD card using the Bookworm 64bit Lite image; enable SSH
- Load the SD card in Raspberry Pi 3A+ or 4
- Connect the Raspberry Pi to your network
- Start the Raspberry pi with the SD card
- ssh into the raspberry Pi
- #> sudo apt-get install git -y
- #> git clone https://github.com/oradiolabs/Oradio3.git
- #> cd Oradio3
- #> source ./oradio_install.sh    <== Note the 'source' command. Required as the scripts changes environment settings
