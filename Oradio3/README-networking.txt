Voorbereiding:
	Oradio3 met Ethernet aan je thuis-netwerk verbonden
	SD kaart met Bookworm 64bit Lite, ssh en gebruiker 'pi'
	Installeer & configureer OS (sh ./prepare.sh)

Testen:
	Login met een ssh verbinding op de Oradio
	Test de basis server:
		cd home/pi/wifi/webapp
		python fastapi_server.py: Start een web server, toegankelijk via http://oradio:8000
		ctrl-c to quit
	Test de web service:
		cd home/pi/wifi
		python web_service.py:
			Start de web server(1). Controleer of je met je browser met http://oradio de web interface te zien krijgt.
	Test wifi:
		python wifi_utils.py:
			Check of lijst met beschikbare netwerken(1) is wat je verwacht dat jij en je buren aan wifi netwerken actief hebben.
			Registreer je eigen wifi netwerk(3), check(4), maak een verbinding(5), check(2). Verbreek de LAN verbinding. Controleer of je met je browser met http://oradio de web interface te zien krijgt.
			Start een access point(8). Controleer of je met een mobiel | tablet | laptop een verbinding kan maken met OradioAP en een IP adres krijgt. LET OP: verder gebeurt er niet op het apparaat waarmee je met OradioAP verbindt!
	Als je én een access point start én de web server start, dan heb je een captive portal opgezet
	Test simulate:
		python simulate.py:
			Kies long-press AAN (1). Controleer of je met een mobiel | tablet | laptop een verbinding kan maken met OradioAP en een IP adres krijgt. Check of op Apple devices de browser vanzelf opent. Check of je met een web browser met http://oradio de web interface ziet.
			kies in de web interface je eigen netwerk en klik op de 'verbind' knop. Check of je op een device wat met hetzelfde netwerk verbonden is je met een web browser met http://oradio de web interface ziet.
			Kies long-press AAN (1). Controleer of je met een mobiel | tablet | laptop . Check of je op een device wat met hetzelfde netwerk verbonden is je met een web browser met http://oradio de web interface ziet.
			Kies extra-long-press AAN (2). Controleer of je met een mobiel | tablet | laptop een verbinding kan maken met OradioAP en een IP adres krijgt. Check of op Apple devices de browser vanzelf opent. Check of je met een web browser met http://oradio de web interface ziet.
			Kies any-press UIT (3). Controleer of je met een mobiel | tablet | laptop dat er geen access point is. Check of je met een web browser met http://oradio een foutmelding krijgt, geen web interface ziet.
