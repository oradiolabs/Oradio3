	function disable_local_host()
	{
		# disable the 'local' setting  for local host naming 
		sudo sed -i "s/^publish-addresses=.*/publish-addresses=no/g" /etc/avahi/avahi-daemon.conf
		sudo sed -i "s/^publish-domain=.*/publish-domain=no/g" /etc/avahi/avahi-daemon.conf
		# reload the service daemon
		sudo systemctl daemon-reload
		# flushes all DNS resource records
		sudo resolvectl flush-caches
		# and restart the avahi-daemon service
		sudo systemctl restart avahi-daemon.service
	}

	function enable_local_host()
	{
		# disable the 'local' setting  for local host naming 
		sudo sed -i "s/^publish-addresses=.*/publish-addresses=yes/g" /etc/avahi/avahi-daemon.conf
		sudo sed -i "s/^publish-domain=.*/publish-domain=yes/g" /etc/avahi/avahi-daemon.conf
		# reload the service daemon
		sudo systemctl daemon-reload
		# and restart the avahi-daemon service
		sudo systemctl restart avahi-daemon.service		
	}
