#!/bin/bash

# File paths for flags
ACTIVE_FLAG_FILE="/home/pi/Oradio3/Spotify/spotactive.flag"
PLAYING_FLAG_FILE="/home/pi/Oradio3/Spotify/spotplaying.flag"
LOG_FILE="/home/pi/Oradio3/logging/spotify_event.log"

# Function to log events
log_event() {
    local message="$1"
    echo "$(date +'%Y-%m-%d %H:%M:%S') $message" >> $LOG_FILE
}

# Function to set a flag
set_flag() {
    local flag_file="$1"
    echo "1" > $flag_file
    log_event "Flag set: $flag_file = 1"
}

# Function to reset a flag
reset_flag() {
    local flag_file="$1"
    echo "0" > $flag_file
    log_event "Flag reset: $flag_file = 0"
}

# Handle Spotify Connect events
case "$PLAYER_EVENT" in
    session_connected)
        log_event "Event: session_connected"
        set_flag "$ACTIVE_FLAG_FILE"
        ;;
    session_disconnected)
        log_event "Event: session_disconnected"
        reset_flag "$ACTIVE_FLAG_FILE"
        ;;
    playing)
        log_event "Event: playing"
        set_flag "$PLAYING_FLAG_FILE"
        ;;
    paused)
        log_event "Event: paused"
        reset_flag "$PLAYING_FLAG_FILE"
        ;;
    *)
        log_event "Unhandled event: $PLAYER_EVENT"
        ;;
esac
