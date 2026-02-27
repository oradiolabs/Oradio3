/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

/* ========== Helpers ========== */

// Keep-alive ping
setInterval(async () => { fetch("/keep_alive", { method:"POST"}); }, 2000);

// Show waiting indicator
function showWaiting()
{
	document.getElementById('waiting').style.display = "block";
}

// Hide waiting indicator
function hideWaiting()
{
	document.getElementById('waiting').style.display = "none";
}

// Show notification
function showNotification(notification, message)
{
	notification.innerHTML = message;
	notification.style.display = 'block';
}

// Hide notification
function hideNotification(notification)
{
	notification.style.display = 'none';
}

// Close the web interface
function shutdownWebApp()
{
	// Remove header and navigation
	document.querySelectorAll('header, nav').forEach(el => el.remove());

	// Replace main content with message
	document.querySelector('main').innerHTML = '<div class="shuttingdown">' + 
		'De web interface wordt afgesloten...' +
	'</div>';

	// Show waiting indicator
	showWaiting();

	// Send shutdown command
	postJSON("shutdown")
}

// Server access wrapper
async function postJSON(cmd, args = {})
{
	const retries = 2;		// Automatic retry
	const timeout = 5000;	// ms

	// Retries
    for (let attempt = 0; attempt <= retries; attempt++)
	{
		try
		{
			// Timeout
			const controller = new AbortController();
			const id = setTimeout(() => controller.abort(), timeout);

			// Submit the request
			const response = await fetch('/execute',
			{
				method: 'POST',
				headers: {'Content-Type': 'application/json'},
				body: JSON.stringify({cmd, args}),
				signal: controller.signal
			});

			clearTimeout(id);

			// Throw for HTTP errors
			if (!response.ok)
                throw new Error(`HTTP ${response.status}`);

			// Parse JSON safely
			const data = await response.json().catch(() => ({}));

			return data;
		}
		catch (err)
		{
			if (attempt < retries)
			{
				// Timeout -> retry
				console.warn(`Retrying /execute, attempt ${attempt+1}`, err);
				await new Promise(r => setTimeout(r, 500)); // short backoff
			}
			else
			{
				throw new Error(`${err.message}. Controleer of de webapp actief is`);
			}
		}
	}
}

/* ========== Initialize Single Page Application (SPA) ========== */

// Global element identifiers
let networksPromise, networks = [];
let networkInput, passwordBlock, passwordInput, notificationNetwork;
let spotifyInput, notificationSpotify;

// Execute when page is loaded
document.addEventListener('DOMContentLoaded', () =>
{
// ===== Initialize: global =====

	// Buttons
	document.querySelector('img.shutdown-button').addEventListener("click", shutdownWebApp);

	// Navigation: Observe initial page
	observeActivePage();

// ===== Initialize: Network =====

	// Element identifiers
	networkInput = document.getElementById('ssid-input');
	passwordBlock = document.getElementById('password-block');
	passwordInput = document.getElementById('password-input');
	const passwordIcon = document.getElementById('password-icon');
	notificationNetwork = document.getElementById('notification_network');

	// Buttons
	document.getElementById("submitCredentialsButton").addEventListener("click", submitCredentials);
	document.getElementById("submitSpotifyButton").addEventListener("click", submitSpotify);

	// Show previous network if available
	const notificationOldSSID = document.getElementById('notification_oldssid');
	if (oldssid?.length)
		showNotification(notificationOldSSID, `Oradio was verbonden met '${oldssid}'`);
	else
		showNotification(notificationOldSSID, `Oradio was niet verbonden met wifi`);

	// Load dropdown with available WiFi networks
	networksPromise = getNetworks();
	populateNetworkDropdown();

	// Clear Network notification when input gets focus
	networkInput.addEventListener("focus", () => hideNotification(notificationNetwork));
	passwordInput.addEventListener("focus", () => hideNotification(notificationNetwork));

	// Password toggle
	passwordIcon.addEventListener("click", () =>
	{
		const isHidden = passwordInput.type === "password";
		passwordInput.type = isHidden ? "text" : "password";
		passwordIcon.classList.toggle("fa-eye", !isHidden);
		passwordIcon.classList.toggle("fa-eye-slash", isHidden);
	});

// ===== Initialize: Spotify =====

	// Element identifiers
	spotifyInput = document.getElementById('spotify-input');
	notificationSpotify = document.getElementById('notification_spotify');

	// Clear Spotify notification when input gets focus
	spotifyInput.addEventListener("focus", () => hideNotification(notificationSpotify));

// ===== Initialize: Spotify =====

});

/* ========== Navigation ========== */

// Switch active page
document.querySelectorAll('nav button').forEach(button =>
{
	button.addEventListener('click', () =>
	{
		// Get new target page
		const page = document.getElementById(button.dataset.page);

		// Only switch if page exists and is not already active
		if (!page || page.classList.contains('active'))
			return; // Do nothing if same page

		// Hide waiting indicator
		hideWaiting();

		// Hide all pages
		document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));

		// Hide notifications, except persistent
		document.querySelectorAll('.notification:not(.persistent)').forEach(element => { element.style.display = 'none'; });

		// Show new active page
		page.classList.add('active');

		// Observe new active page
		observeActivePage();

		// Reset scroll to top
		page.scrollTop = 0;
	});
});

// Start observing active page
function observeActivePage()
{
	// Unobserve previous pages
	contentObserver.disconnect();

	const activePage = document.querySelector('.page.active');
	if (activePage)
	{
		// Observe child additions/deletions anywhere in the page
		contentObserver.observe(activePage, { childList: true, subtree: true });

		// Initial check
		updatePageScrollState(activePage);
	}
}

// MutationObserver to track content changes
const contentObserver = new MutationObserver((mutationsList) =>
{
	// We only care about the active page
	const activePage = document.querySelector('.page.active');
	if (activePage)
		updatePageScrollState(activePage);
});

// Check if page needs scrolling
function updatePageScrollState(page)
{
	const navImages = document.querySelectorAll('nav button span');
	const hidden = page.scrollHeight > page.clientHeight;
	navImages.forEach(img => img.style.display = hidden ? 'none' : '');
}

/* ========== Scrollbox ========== */

// Create scrollbox row
function createRow(option)
{
	// Create the outer div
	const row = document.createElement("div");
	row.className = "scrollbox-row";

	// Create the inner div
	const rowText = document.createElement("div");
	rowText.className = "scrollbox-row-text";

	// Add the text
	rowText.textContent = option;

	// Put the inner div inside the outer div
	row.appendChild(rowText);

	return row;
}

// Show scrollbox, selecting row matching input value
function showScrollbox(scrollbox, input)
{
	// Get text from given input
	const inputText = input.value.trim();

	// Get rows inside the given scrollbox
	const rows = scrollbox.querySelectorAll('.scrollbox-row');

	// Highlight row matching input
	rows.forEach(row =>
	{
		const rowText = row.querySelector('.scrollbox-row-text').textContent.trim() || '';
		if (rowText === inputText)
			row.classList.add('selected');
		else
			row.classList.remove('selected');
	});

	// Show scrollbox
	scrollbox.style.display = 'block';
}

// Hide scrollbox (added for maintainability)
function hideScrollbox(scrollbox)
{
	scrollbox.style.display = 'none';
}

/* ========== Dropdown ========== */

document.addEventListener("click", (event) =>
{
    const target = event.target;

	// First: Icon click (only if the clicked element or its ancestor is an icon)
	const icon = target.closest(".icon-button");
	if (icon)
	{
		const row = icon.closest(".scrollbox-row");
		handleIconClick(row);
//REVIEW: Moet dit niet aan begin van handler? I toch altijd geval?
		event.stopPropagation(); // prevent row click handler
		return;
	}

	// Second: Row click (only if clicked inside a row but NOT on an icon)
	const row = target.closest(".scrollbox-row");
	if (row)
	{
		handleRowClick(row);
		event.stopPropagation(); // prevent custom select click handler
		return;
	}

	// Third: Click inside custom-select
	const customSelect = target.closest(".custom-select");
	if (customSelect)
	{
		handleSelectClick(target, customSelect);
		return;
	}

	// Clicked outside any custom-select: close all
	closeDropdowns();
});

// Select row and trigger CALLBACK handler
function handleIconClick(row)
{
	// Only modify icons in a row
	if (row)
	{
		// Highlight selected row
		row.parentElement.querySelectorAll(".scrollbox-row").forEach(r => r.classList.remove("selected"));
		row.classList.add("selected");

		// Save/remove song from playlist
		if (row.querySelector(".delete-button-small"))
//REVIEW: Alleen nodige info doorgeven!
			delSongFromPlaylist(row);
		else if (row.querySelector(".save-button-small"))
//REVIEW: Alleen nodige info doorgeven!
			addSongFromPlaylist(row);
		else
			console.error("Undefined modify request for row:", row);
	}
}

// Select row and trigger CALLBACK handler
function handleRowClick(row)
{
	// Get closest scrollbox, input and icon elements
	const scrollbox = row.closest(".scrollbox");
	const customSelect = row.closest(".custom-select");
	const input = customSelect.querySelector("input");
	const icon = customSelect.querySelector(".custom-icon");

	if (input)
	{
		// Set input value with sanitized row text
		input.value = row.querySelector(".scrollbox-row-text").textContent.trim();

		// Dispatch a custom input value changed event
		input.dispatchEvent(new Event('inputValueChanged'));

		// Hide scrollbox
		hideScrollbox(scrollbox);
	}

	if (icon)
		// Rotate icon to 'closed'
		icon.classList.remove('open');

	// Highlight selected row
	scrollbox.querySelectorAll(".scrollbox-row").forEach(r => r.classList.remove("selected"));
	row.classList.add("selected");

	// CALLBACK: pass row for follow-up actions
	onRowSelect(row.dataset.action, row);
}

// Open dropdown scrollbox on input or icon click
function handleSelectClick(target, customSelect)
{
	// Get closest input, icon and scrollbox elements
	const input = customSelect.querySelector("input");
	const icon = customSelect.querySelector(".custom-icon");
	const dropdown = customSelect.querySelector(".scrollbox.dropdown");

	// Click on input or dropdown icon
	if (target === input || target === icon)
	{
		// Close any open dropdown scrollboxes
		closeDropdowns();

		// Show waiting indicator if scrollbox is not populated
		if (dropdown.dataset.populated === "false")
			showWaiting();

		// Show the dropdown scrollbox
		showScrollbox(dropdown, input)

		if (icon)
			// Rotate icon to 'open'
			icon.classList.add('open');

		// Reset scrollbox to top
		dropdown.scrollTop = 0;
	}
}

// Close dropdown scrollboxes
function closeDropdowns()
{
	document.querySelectorAll(".custom-select .scrollbox.dropdown").forEach(scrollbox =>
	{
		hideScrollbox(scrollbox);

		// If present, rotate icon to 'closed'
		const icon = scrollbox.closest(".custom-select")?.querySelector(".custom-icon");
		if (icon) icon.classList.remove('open');
	});
}

/* ========== CALLBACK ========== */

// CALLBACK: action for selected row
function onRowSelect(action, row)
{
	switch (action)
	{
		case "network":
			// Get network ssid
			const ssid = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Show password input only if network requires it
			showPassword(ssid);
			break;

		case "preset1":
		case "preset2":
		case "preset3":
			// Get playlist
			var playlist = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Update preset button playlist
			savePreset(action, playlist);
			break;

		case "playlist":
			// Get input
			const input = row.dataset.input;
			// Get scrollbox
			const scrollbox = row.dataset.target;
			// Get playlist
//REVIEW: is playlist niet gelijk aan input.value?
			var playlist = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Show scrollbox with custom playlist songs
			showSongs(input, scrollbox, playlist);
			break;

		case "play":
			// Get related notification
			const notify = row.dataset.notify;
			// Get filename of song to play
			const songfile = row.dataset.songfile;
			// Get song description
//REVIEW: is songtext nodig?
			const songtext = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Play selected song
			playSong(notify, songfile, songtext);
			break;

		default:
			console.error("Unexpected action for row:", row);
	}
}

/* ========== Network ========== */

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
	// Show waiting indicator
	showWaiting();

	// Wait for networks
	const networks = await networksPromise;

	// Populate dropdown with wifi network id's
	const dropdown = document.querySelector('.network.custom-select .scrollbox.dropdown');
	const fragment = document.createDocumentFragment();
	networks.forEach(network =>
	{
		const row = createRow(network.ssid);
		row.dataset.action = "network";
		fragment.appendChild(row);
	});
	dropdown.replaceChildren(fragment);

	// Mark dropdown as ready
	dropdown.dataset.populated = true;

	// Hide waiting indicator
	hideWaiting();
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

		// Show waiting indicator
		showWaiting();

		// Submit credentials to server
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

	// Show waiting indicator
	showWaiting();

	try
	{
		const cmd = "spotify";
		const args = { "name": name };

		// Submit Spotify name to server
		name = await postJSON(cmd, args);

		// Server returns Spotify name set
		spotifyInput.value = name;
		spotify = name;

		showNotification(notificationSpotify, `<span class="success">De Spotify naam is gewijzigd in '${name}'</span>`);
	}
	catch (err)
	{
		showNotification(notificationSpotify, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}

	// Hide waiting indicator
	hideWaiting();
}

















// CALLBACK entry point: Show playlist songs in scrollbox
async function showSongs(input, target, playlist)
{
	// Show scrollbox with playlist songs, hide if empty
	const songs = await getPlaylistSongs(playlist);
	const scrollbox = document.getElementById(target);
	if (songs.length)
		populateSongsScrollbox(input, scrollbox, songs);
	else
		hideScrollbox(scrollbox);
}

// Get the songs for the given playlist
async function getPlaylistSongs(playlist)
{
	// Clear notification
	hideNotification(notificationPlaylist);

	// Set error template
	const errorMessage = `Ophalen van de liedjes van speellijst '${playlist}' is mislukt`;

	try
	{
		const cmd = "playlist";
		const args = { "playlist": playlist };

		// Wait for songs to be returned by the server
		const songs = (await postJSON(cmd, args)) || [];

		// Warn if no songs found
		if (songs.length === 0)
		{
			// Inform user playlist is empty
			showNotification(notificationPlaylist, `<span class="warning">Speellijst '${playlist}' is leeg</span>`);

			// Failed to fetch songs
			return [];
		}

		// Check if playlist is web radio
		if ((/^https?:/.test(songs[0]['file'])))
		{
			// Inform user playlist is webradio
			showNotification(notificationPlaylist, `<span class="warning">Speellijst '${playlist}' is webradio</span>`);

			// Failed to fetch songs
			return [];
		}

		// Return playlist songs
		return songs;
	}
	catch (err)
	{
		showNotification(notificationPlaylist, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
}

// Convert songs into scrollbox rows
function populateSongsScrollbox(type, scrollbox, songs)
{
	// Create fragment
	const fragment = document.createDocumentFragment();

	// Populate fragment
	songs.forEach(song =>
	{
		const row = createRow(`${song.artist} - ${song.title}`);
		row.dataset.action = "play";
		row.dataset.songfile = song.file;
		row.dataset.notify = `notification_${type}`;
		fragment.appendChild(row);
	});

	// Replace old rows efficiently
	scrollbox.replaceChildren(fragment);

	// mark scrollbox as populated
	scrollbox.dataset.populated = "true";

	// Show the scrollbox if input exists
	const input = document.getElementById(type);
	if (input) showScrollbox(scrollbox, input);

	// Reset scrollbox to top
	scrollbox.scrollTop = 0;

	// Dispatch a custom scrollbox populated event
	scrollbox.dispatchEvent(new Event('scrollboxPopulated'));
}

// Submit the song to the server for playback
async function playSong(notification, songfile, songtitle)
{
	const notify = document.getElementById(notification);
	if (!notify)
	{
		console.warn(`playSong(): notification element '${notification}' not found`);
		return;
	}

	hideNotification(notify);

	const errorMessage = `Er is een fout opgetreden bij het indienen van het te spelen liedje '${songtitle}'`;

	try
	{
		const cmd = "play";
		const args = { "song": songfile };
		await postJSON(cmd, args);
	}
	catch (err)
	{
		showNotification(notify, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
}
