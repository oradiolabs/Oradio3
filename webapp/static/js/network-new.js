/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

// Initialize
let networks = [];
let networksPromise;
let network_input;
let password_block;
let password_input;
let password_icon;
let spotify_input;
let notification_oldssid;
let notification_network;
let notification_spotify;

// Setup network page
document.addEventListener('DOMContentLoaded', () =>
{
	// Get network elements
	network_input = document.getElementById('SSIDs');

	// Get password elements
	password_block = document.getElementById('password');
	password_input = document.getElementById('pswd');
	password_icon = document.getElementById('pswd-icon');

	// Get spotify elements
	spotify_input = document.getElementById("spotify");

	// Get notifications
	notification_oldssid = document.getElementById('notification_oldssid');
	notification_network = document.getElementById('notification_network');
	notification_spotify = document.getElementById('notification_spotify');

	// Notify which network the Oradio was connected to before starting the web interface
	if (oldssid?.length)		// ?. operator means test if variable exists  AND length > 0
		showNotification(notification_oldssid, `Oradio was verbonden met '${oldssid}'`);
	else
		showNotification(notification_oldssid, `Oradio was niet verbonden met wifi`);

	// Fetch active networks from server
	networksPromise = getNetworks();

	// On icon click toggle password hidden or readable
	password_icon.addEventListener("click", function ()
	{
		if (password_input.type === "password")
		{
			password_input.type = "text";
			password_icon.classList.remove("fa-eye");
			password_icon.classList.add("fa-eye-slash");
		}
		else
		{
			password_input.type = "password";
			password_icon.classList.remove("fa-eye-slash");
			password_icon.classList.add("fa-eye");
		}
	});

	// Assign action to network button
	document.getElementById("submitCredentialsButton").addEventListener("click", submitCredentials);

	// Assign action to Spotify button
	document.getElementById("submitSpotifyButton").addEventListener("click", submitSpotify);

//REVIEW Onno: verplaatsen naar oradio3-new.js bij selectie ?
	// Clear network notification when selecting input
	network_input.addEventListener("focus", function()
	{
		hideNotification(notification_network);
	});

//REVIEW Onno: verplaatsen naar oradio3-new.js bij selectie ?
	// Clear spotify notification when selecting input
	spotify_input.addEventListener("focus", function()
	{
		hideNotification(notification_spotify);
	});
});

// Helper function converting networks into options
async function fillNetworkDropdown(dropdown)
{
	// Wait for networks to load
	const networks = await networksPromise;

	// Clear existing content
	dropdown.innerHTML = '';

	networks.forEach((network, index) =>
	{
		const div = createRow(network.ssid, index);
		div.classList.add('network-row');
		div.dataset.ssid = network.ssid;
		dropdown.appendChild(div);
	});
}

// Helper function to control password visibility
function showPassword(ssid)
{
	// Clear password input
	password_input.value = "";

	// Show password input only if network requires it
	const network = networks.find(n => n.ssid === ssid);
	password_block.style.display = (!network || network.type === "closed") ? "block" : "none";
}

// Function to get the active wifi networks
async function getNetworks()
{
	// Set error template
	const errorMessage = `Ophalen van de active wifi netwerken is mislukt`;

	try {
		// Request networks form server
		const response = await fetch('/get_networks', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'}
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notification_network, `<span class="error">${errorData.message || errorMessage}</span>`);

			// Failed to fetch networks
			return [];
		}

		// Get the networks provided by the fetch (ensure array for empty fetch)
		networks = (await response.json()) || [];

		// Warn if no networks found
		if (networks.length === 0)
		{
			// Inform user what went wrong
			showNotification(notification_network, `<span class="warning">No wifi networks found</span>`);

			// Failed to fetch networks
			return [];
		}

		// Sort networks alphabetically by ssid (case-insensitive)
		networks.sort((a, b) => a.ssid.localeCompare(b.ssid, undefined, { sensitivity: 'base' }));

        return networks;

	} catch (err) {
		showNotification(notification_network, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);
		return [];
	}
}

// Function to submit the network and password
async function submitCredentials()
{
	// Clear notification
	hideNotification(notification_network);

	// Get selected network info
	const ssid = network_input.value;
	if (ssid === "")
	{
		// Show network error
		showNotification(notification_network, `<span class="error">Kies een wifi netwerk</span>`);
		return;
	}

	// Get password and check
	const pswd = password_input.value;
	const ignorePswd = (password_block.style.display === "none");
	if (!ignorePswd && pswd.length < 8)
	{
		// Show password error
		showNotification(notification_network, `<span class="error">Wachtwoord moet minimaal 8 karakters zijn</span>`);
		return;
	}

	// Set error template
	const errorMessage = `Verbinding maken met netwerk '${ssid}' is mislukt`;

	try {
		// Send wifi credentials to server
		const response = await fetch('/wifi_connect',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"ssid": ssid, "pswd": pswd})
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notification_network, `<span class="error">${errorData.message || errorMessage}</span>`);

			// Failed to submit credentials
			return;
		}

		// Show waiting indicator as it will take a few seconds before the connection is lost
		show_waiting();

		// Inform user
		showNotification(notification_network, `De webapp wordt afgesloten<br>Oradio probeert te verbinden met '${ssid}'`);
	} catch (err) {
		showNotification(notification_network, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);
	}
}

// Function to submit the Spotify name
async function submitSpotify()
{
	// Clear notification
	hideNotification(notification_spotify);

	// Get selected network info
	let name = spotify_input.value;
	if (name === "")
	{
		// Show Spotify error
		showNotification(notification_spotify, `<span class="error">Kies een naam voor in de Spotify app</span>`);
		return;
	}

	// Warn if name is already set
	if (name === spotify)
	{
		// Show Spotify warning
		showNotification(notification_spotify, `<span class="warning">'${name}' is al actief</span>`);
		return;
	}

	// Show waiting indicator
	show_waiting();

	// Set error template
	const errorMessage = `Instellen van Spotify naam '${name}' is mislukt`;

	try {
		// Send Spotify name to server
		const response = await fetch('/spotify',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({ "name": name })
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notification_spotify, `<span class="error">${errorData.message || errorMessage}</span>`);

			// Hide waiting indicator
			hide_waiting();

			// Failed to submit credentials
			return;
		}

		// Get and show the changed Spotify name
		name = await response.json();
		spotify_input.value = name;
		showNotification(notification_spotify, `<span class="success">De Spotify naam is gewijzigd in '${name}'</span>`);

		// Save new Spotify name
		spotify = name;
	} catch (err) {
		showNotification(notification_spotify, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);
	}

	// Hide waiting indicator
	hide_waiting();
}
