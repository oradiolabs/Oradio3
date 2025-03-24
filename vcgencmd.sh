#!/bin/bash

case "$1" in
    measure_temp)
        echo "temp=42.0'C"
        ;;
    get_throttled)
        echo "throttled=0x0"
        ;;
    *)
        echo "Unsupported vcgencmd command: $1"
        exit 1
        ;;
esac
