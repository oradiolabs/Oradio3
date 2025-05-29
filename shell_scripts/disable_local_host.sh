#!/bin/bash
sudo avahi-set-host-name $(hostnamectl --static) 2>/dev/null
exit 0
