Voorbereiding:
	Oradio3 met Ethernet aan je thuis-netwerk verbonden
	SD kaart met Bookworm 64bit Lite, ssh en gebruiker 'pi'
	Installeer & configureer OS (sh ./prepare.sh)

Testen:
	Login met een ssh verbinding op de Oradio
	Test usb:
		cd home/pi/usb
		python usb_utils.py:
			Check of usb aanwezigheid(1) zonder USB. naam doet er niet toe. Controleer dat status False is
			Steek USB met bekende naam in de Oradio. Check op usb aanwezigheid(1). Voer verkeerde naam in en controleer dat status False is.is wat je verwacht
			Steek USB met bekende naam in de Oradio. Check op usb aanwezigheid(1). Voer de juiste naam in (let op: hoofdlettergevoelig!) en controleer dat status True is
			Start de monitor(2). Haal de usb uit de Oradio: controleer de melding. Steek de usb weer in de Oradio: check de melding.
			Stop de monitor(3). Haal de usb uit de Oradio: controleer geen melding. Steek de usb weer in de Oradio: check geen melding.
	Test simulate:
		python simulate.py:
			Geen USB drive in Oradio: Test of USB present(1) is wat je verwacht
			Activeer monitor(2).
				Stop USB drive zonder naam in Oradio. Controleer dat meldingen correct zijn
				Stop USB drive met naam ORADIO, ZONDER bestand Wifi_invoer.json in de root, in Oradio. Controleer dat meldingen correct zijn
					==> Wifi_invoer.json met inhoud de tekst tussen quotes: '{"SSID": "testssid", "PASSWORD": "testwachtwoord"}'
				Stop USB drive met naam ORADIO, MET bestand Wifi_invoer.json in de root, in Oradio. Controleer dat meldingen correct zijn. Netwerkverbinding mislukt
			Steek de USB weer in de Oradio. Controleer dat de monitor detecteert en een verbinding probeert te maken met het test netwerk. Dit faalt
				Wijzig Wifi_invoer.json testssid en testwachtwoord in de instellingen voor je eigen netwerk
			Kies USB present(1) of haal de USB uit de Oradio en steek hem weer terug. Controleer of er nu een verbinding met de Oradio een verbinding maakt met je wifi netwerk
