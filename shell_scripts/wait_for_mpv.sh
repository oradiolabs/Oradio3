#!/bin/bash

USER_ID=1000  # replace with the actual UID running mpv: check with==> id -u pi
SERVICE_NAME=mpv.service

while true; do
    state=$(machinectl show-user $USER_ID | grep -q 'State=running')
    systemctl --user --machine=$USER_ID@ show "$SERVICE_NAME" > /dev/null 2>&1 && \
    systemctl --user --machine=$USER_ID@ is-active "$SERVICE_NAME" &> /dev/null && break
    sleep 1
done