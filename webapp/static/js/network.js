/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

let networks = [];
let networksPromise;
let networkInput, passwordBlock, passwordInput, spotifyInput;
let notificationNetwork, notificationSpotify;

// DOMContentLoaded setup for network page
document.addEventListener('DOMContentLoaded', () =>
{
	// Inputs
	networkInput = document.getElementById('ssid-input');
	passwordBlock = document.getElementById('password-block');
	passwordInput = document.getElementById('password-input');
	const passwordIcon = document.getElementById('password-icon');
	spotifyInput = document.getElementById('spotify');

	// Notifications
	const notificationOldSSID = document.getElementById('notification_oldssid');
	notificationNetwork = document.getElementById('notification_network');
	notificationSpotify = document.getElementById('notification_spotify');

	// Show previous network if available
	if (oldssid?.length)
		showNotification(notificationOldSSID, `Oradio was verbonden met '${oldssid}'`);
	else
		showNotification(notificationOldSSID, `Oradio was niet verbonden met wifi`);

	showWaiting();
	
	// Fetch networks
	networksPromise = getNetworks();
	populateNetworkDropdown();

	hideWaiting();

	// Buttons
	document.getElementById("submitCredentialsButton").addEventListener("click", submitCredentials);
	document.getElementById("submitSpotifyButton").addEventListener("click", submitSpotify);

	// Password toggle
	passwordIcon.addEventListener("click", () =>
	{
		const isHidden = passwordInput.type === "password";
		passwordInput.type = isHidden ? "text" : "password";
		passwordIcon.classList.toggle("fa-eye", !isHidden);
		passwordIcon.classList.toggle("fa-eye-slash", isHidden);
	});

	// Clear notifications on focus using focusin (delegation)
	document.addEventListener("focusin", event =>
	{
		if (event.target.matches("#ssid-input, #password-input"))
			hideNotification(notificationNetwork);
		if (event.target.matches("#spotify"))
			hideNotification(notificationSpotify);
	});
});

// Fetch networks from server
async function getNetworks()
{
	const errorMessage = "Ophalen van de actieve wifi netwerken is mislukt";

	try
	{
		const cmd = "networks";
		const networks = (await postJSON(cmd)) || [];
		if (networks.length === 0)
		{
			showNotification(notificationNetwork, `<span class="warning">No wifi networks found</span>`);
			return [];
		}

		// Sort alphabetically
		return networks.sort((a, b) =>
			a.ssid.localeCompare(b.ssid, undefined, { sensitivity: 'base' })
		);
	}
	catch (err)
	{
		showNotification(notificationNetwork, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
}

// Populate dropdown with networks
async function populateNetworkDropdown()
{
	// IMPORTANT: start waiting for networks
	const networks = await networksPromise;

	const dropdown = document.querySelector('.network.custom-select .scrollbox.dropdown');

	const fragment = document.createDocumentFragment();
	networks.forEach(network =>
	{
		const row = createRow(network.ssid);
		row.dataset.action = "network";
		fragment.appendChild(row);
	});
	dropdown.replaceChildren(fragment);

	dropdown.dataset.populated = true;
}

// CALLBACK entry point: Show/hide password input based on network type
async function showPassword(ssid)
{
	passwordInput.value = "";
	const networks = await networksPromise;
	const network = networks.find(n => n.ssid === ssid);
	passwordBlock.style.display = (!network || network.type === "closed") ? "block" : "none";
}

// Submit network credentials
async function submitCredentials()
{
	hideNotification(notificationNetwork);

	const ssid = networkInput.value;
	if (!ssid)
	{
		showNotification(notificationNetwork, `<span class="error">Kies een wifi netwerk</span>`);
		return;
	}

	const pswd = passwordInput.value;
	const ignorePassword = passwordBlock.style.display === "none";
	if (!ignorePassword && pswd.length < 8)
	{
		showNotification(notificationNetwork, `<span class="error">Wachtwoord moet minimaal 8 karakters zijn</span>`);
		return;
	}

	const errorMessage = `Verbinding maken met netwerk '${ssid}' is mislukt`;

	try
	{
		const cmd = "connect";
		const args = { "ssid": ssid, "pswd": pswd };
		showWaiting();
		postJSON(cmd, args);
		showNotification(notificationNetwork,`De webapp wordt afgesloten<br>Oradio probeert te verbinden met '${ssid}'`);
	}
	catch (err)
	{
		showNotification(notificationNetwork, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
}

// Modify Spotify name
async function submitSpotify()
{
	hideNotification(notificationSpotify);

	let name = spotifyInput.value.trim();
	if (!name)
	{
		showNotification(notificationSpotify, `<span class="error">Kies een naam voor in de Spotify app</span>`);
		return;
	}

	if (name === spotify)
	{
		showNotification(notificationSpotify, `<span class="warning">'${name}' is al actief</span>`);
		return;
	}

	const errorMessage = `Instellen van Spotify naam '${name}' is mislukt`;

	showWaiting();

	try
	{
		const cmd = "spotify";
		const args = { "name": name };
		name = await postJSON(cmd, args);
		spotifyInput.value = name;
		showNotification(notificationSpotify, `<span class="success">De Spotify naam is gewijzigd in '${name}'</span>`);
		spotify = name;
	}
	catch (err)
	{
		showNotification(notificationSpotify, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}

	hideWaiting();
}
