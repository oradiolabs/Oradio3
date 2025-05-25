#!/bin/bash
#!/bin/bash
sudo hostnamectl hostname mijnOradio
# kill the "oradio.local" mDNS service
kill "$(cat /tmp/oradio_webserver.pid)"
