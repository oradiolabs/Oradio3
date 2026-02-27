/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

/* ========== Helpers ========== */

// Keep-alive ping
setInterval(() => {
	fetch("/keep_alive", { method:"POST" }).catch(()=>{});
}, 2000);

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
	postJSON("shutdown").catch(err => console.error(err));
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

// Global variables
let networks = [], networksPromise, networkInput, passwordBlock, passwordInput, notificationNetwork;	// Network
let spotifyInput, notificationSpotify;																	// Spotify
let notificationPresets;																				// Presets
let playlistInput, playlistSongs, notificationPlaylist;													// Playlist songs
let customPlaylists, customList, customInput, customSongs, notificationCustom;							// Custom playlists
let searchInput, searchSongs, notificationSearch;														// Search songs

// Execute when page is loaded
document.addEventListener('DOMContentLoaded', () =>
{
// ===== Initialize: global =====

	// Buttons
	document.querySelector('img.shutdown-button').addEventListener("click", shutdownWebApp);

	// Navigation: Observe initial page
	observeActivePage();

// ===== Initialize: Network =====

	// Network page
	networkInput = document.getElementById('ssid-input');
	passwordBlock = document.getElementById('password-block');
	passwordInput = document.getElementById('password-input');
	const passwordIcon = document.getElementById('password-icon');
	notificationNetwork = document.getElementById('notification_network');
	spotifyInput = document.getElementById('spotify-input');
	notificationSpotify = document.getElementById('notification_spotify');
	// Buttons page
	playlistInput = document.getElementById('playlist-input'); 
	playlistSongs = document.getElementById('playlist-songs'); 
	notificationPlaylist = document.getElementById('notification_playlist');
	notificationPresets = document.getElementById('notification_presets');
	// Playlists page
	customInput = document.getElementById("custom-input");
	customList = document.getElementById('custom-list');
	customSongs = document.getElementById('custom-songs');
	notificationCustom = document.getElementById("notification_custom");
	searchInput = document.getElementById('search-input');
	searchSongs = document.getElementById('search-songs');
	notificationSearch = document.getElementById("notification_search");

	// Buttons
	document.getElementById("submitCredentialsButton").addEventListener("click", submitCredentials);
	document.getElementById("submitSpotifyButton").addEventListener("click", submitSpotify);
	document.getElementById("addButton").addEventListener("click", () => addCustomPlaylist());
	document.getElementById("delButton").addEventListener("click", () => delCustomPlaylist());
	document.getElementById("submitSearchButton").addEventListener("click", submitSearch);

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

	// Clear Spotify notification when input gets focus
	spotifyInput.addEventListener("focus", () => hideNotification(notificationSpotify));

	// Populate the preset dropdowns with avaialble directories and playlists
	populatePresetLists();

	// Clear notifications and hide songs scrollbox on focus
	playlistInput.addEventListener("focus", () =>
	{
		hideNotification(notificationPlaylist);
		hideScrollbox(playlistSongs);
	});

	// Get array with only the names of the playlists which are no webradio
	customPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// Track 
	function populateAutocompleteList()
	{
		// Hide notification and songs when selecting a new playlist
		hideNotification(notificationCustom);
		hideScrollbox(customSongs);

		// Get input for case insensitive matching
		const playlist = customInput.value.toLowerCase();

		// Show items matching input, empty shows all
		const matches = playlist.length
			? customPlaylists.filter(item => item.toLowerCase().includes(playlist))
			: customPlaylists;

		// Fill and show autocomplete list if it has entries, hide otherwise
		populateCustomDropdown(matches);

		// Do not show empty dropdown
		if (matches.length > 0)
			showScrollbox(customList, customInput);
		else
			hideScrollbox(customList);

		// Show/hide save buttons
		updateAddButtons();
	}

	// Update list when clicked or while typing
	customInput.addEventListener("focus", (event) => populateAutocompleteList());
	customInput.addEventListener("input", (event) => populateAutocompleteList());

	// Show/hide add and del buttons
	customInput.addEventListener('inputValueChanged', () => updateAddButtons());
	customSongs.addEventListener('scrollboxPopulated', () => updateDelButtons());

	// Clear notifications and hide search songs scrollbox on focus
	searchInput.addEventListener("focus", () =>
	{
		hideNotification(notificationSearch);
		hideScrollbox(searchSongs);
	});

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

		case "playlist-input":
			// Get input
			const input = document.getElementById(row.dataset.input);
			// Get scrollbox
			const scrollbox = document.getElementById(row.dataset.target);
			// Get playlist
//REVIEW: is playlist niet gelijk aan input.value?
			var playlist = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Get notification
			const notification = document.getElementById(row.dataset.notify);
			// Show scrollbox with custom playlist songs
			showSongs(input, scrollbox, playlist, notification);
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

/* ========== Network page - Network ========== */

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
	// Avoid undefined
	return [];
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
	const ignorePassword = window.getComputedStyle(passwordBlock).display === "none";
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
		await postJSON(cmd, args);

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

/* ========== Buttons page - Presets ========== */

// Convert directories and playlists into scrollbox rows
function populatePresetLists()
{
	// Get alphabetically sorted array with directories and playlist names
	const playlistNames = playlists.map(item => item.playlist);
	let options = directories.concat(playlistNames);
	options.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));

	// Create dropdown lists with options and actions
	document.querySelectorAll('.presets').forEach(container =>
	{
		const input = container.querySelector('input');
		const dropdown = container.querySelector('.scrollbox.dropdown');

		const fragment = document.createDocumentFragment();
		options.forEach(option =>
		{
			const row = createRow(option);
			row.dataset.action = input.id;
			row.dataset.input = playlistInput.id;
			row.dataset.target = playlistSongs.id;
			row.dataset.notify = notificationPlaylist.id;
			fragment.appendChild(row);
		});
		dropdown.replaceChildren(fragment);

		dropdown.dataset.populated = "true";
		hideWaiting();
	});
}

// CALLBACK entry point: Submit the changed preset
async function savePreset(preset, playlist)
{
	// Clear notification
	hideNotification(notificationPresets);

	// Show waiting indicator
	showWaiting();

	// Set error template
	const errorMessage = `Koppelen van '${preset}' is mislukt`;

	try
	{
		const cmd = "preset";
		const args = { "preset": preset, "playlist": playlist };
		await postJSON(cmd, args);
	}
	catch (err)
	{
		showNotification(notificationPresets, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}

	// Hide waiting indicator
	hideWaiting();
}

// CALLBACK entry point: Show playlist songs in scrollbox
async function showSongs(input, scrollbox, playlist, notification)
{
	// Show waiting indicator
	showWaiting();

	// Show scrollbox with playlist songs, hide if empty
	const songs = await getPlaylistSongs(playlist);
	if (songs.length)
		populateSongsScrollbox(input, scrollbox, songs, notification);
	else
		hideScrollbox(scrollbox);

	// Hide waiting indicator
	hideWaiting();
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
	// Avoid Undefined
	return [];
}

// Convert songs into scrollbox rows
function populateSongsScrollbox(input, scrollbox, songs, notification)
{
	// Create fragment
	const fragment = document.createDocumentFragment();

	// Populate fragment
	songs.forEach(song =>
	{
		const row = createRow(`${song.artist} - ${song.title}`);
		row.dataset.action = "play";
		row.dataset.songfile = song.file;
		row.dataset.notify = notification.id;
		fragment.appendChild(row);
	});

	// Replace old rows efficiently
	scrollbox.replaceChildren(fragment);

	// mark scrollbox as populated
	scrollbox.dataset.populated = "true";

	// Show the scrollbox if input exists
	if (input)
		showScrollbox(scrollbox, input);

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

/* ========== Playlist page - custom ========== */

// Add/remove save buttons to add songs to playlist
function updateAddButtons()
{
	const playlist = customInput.value.trim();
	const existsInCustom = customPlaylists.some(n => n.toLowerCase() === playlist.toLowerCase());

	// Add save buttons to each row
	const rows = document.querySelectorAll('#search-songs .scrollbox-row');

	rows.forEach((row, index) =>
	{
        const existingButton = row.querySelector('.save-button-small');

		if (playlist && existsInCustom)
		{
			// Add if missing
			if (!existingButton)
			{
				const icon = document.createElement('span');
				icon.className = 'icon-button save-button-small';
				row.appendChild(icon);
			}
		}
		else
		{
            // Remove if it exists
            if (existingButton)
                existingButton.remove();
		}
	});
}

// Add delete buttons to remove songs from playlist
function updateDelButtons()
{
	// Add remove buttons to each row
	const rows = document.querySelectorAll('#custom-songs .scrollbox-row');

	rows.forEach((row, index) =>
	{
		// Add if missing
		if (!row.querySelector('.delete-button-small'))
		{
			const icon = document.createElement('span');
			icon.className = 'icon-button delete-button-small';
			row.appendChild(icon);
		}
	});
}

// Populate dropdown with custom playlists
async function populateCustomDropdown(playlists)
{
	// Add playlists to dropdown
	const fragment = document.createDocumentFragment();
	playlists.forEach(playlist =>
	{
		const row = createRow(playlist);
		row.dataset.action = "playlist-input";
		row.dataset.input = customInput.id;
		row.dataset.target = customSongs.id;
		row.dataset.notify = notificationCustom.id;
		fragment.appendChild(row);
	});
	customList.replaceChildren(fragment);

	// Mark populated
	customList.dataset.populated = true;
}

// Create custom playlist if it does not exist
async function addCustomPlaylist()
{
	// Hide notification, autocomplete list and songs when adding a new playlist
	hideNotification(notificationCustom);
	hideScrollbox(customSongs);
	hideScrollbox(customList);

	// Get input value or empty string
	const playlist = customInput.value.trim() || "";

	const existsInCustom = customPlaylists.some(n => n.toLowerCase() === playlist.toLowerCase());
	const existsInDirectory = directories.some(n => n.toLowerCase() === playlist.toLowerCase());

	// Warn if empty or exists, as custom playlist or directory
	if (!playlist || existsInCustom || existsInDirectory)
	{
		showNotification(notificationCustom, `<span class='warning'>Typ een <em>nieuwe</em> speellijstnaam</span>`);
		return;
	}

	// Set error template
	const errorMessage = `Opslaan van speellijst '${playlist}' mislukt`;

	// Add playlist on server
	if (await modifyPlaylist('Add', playlist, null, errorMessage))
		// playlist is gone, so also clear the songs
		customSongs.innerHTML = '';

	// Inform user
	showNotification(notificationCustom, `<span class='success'>Speellijst '${playlist}' is toegevoegd<br>Zoek liedjes en voeg toe met de <span class="icon-button save-button-tiny"></span>-knop</span>`);

	// Show/hide save buttons
	updateAddButtons();

	// Update dropdowns on buttons page
	populatePresetLists();
}

// Remove custom playlist if it exists
async function delCustomPlaylist()
{
	// Hide notification, autocomplete list and songs when removing a playlist
	hideNotification(notificationCustom);
	hideScrollbox(customSongs);
	hideScrollbox(customList);

	// Get input value or empty string
	const playlist = customInput.value.trim() || "";

	const existsInCustom = customPlaylists.some(n => n.toLowerCase() === playlist.toLowerCase());
	const existsInDirectory = directories.some(n => n.toLowerCase() === playlist.toLowerCase());

	// Warn if empty or not exists or is a directory
	if (!playlist || !existsInCustom || existsInDirectory)
	{
		showNotification(notificationCustom, `<span class='warning'>Kies of typ een <em>bestaande</em> speellijstnaam</span>`);
		return;
	}

	// Warn if playlist is in use by preset
	var inUse = false;
	document.querySelectorAll('.presets').forEach(container =>
	{
		if (playlist === container.querySelector('input').value.trim())
			inUse = true;
	});
	if (inUse)
	{
		showNotification(notificationCustom, `<span class='warning'> 'Speellijst ${playlist}' niet verwijderd, want gekoppeld aan voorkeursknop(pen)</span>`);
		return;
	}

	// Set error template
	const errorMessage = `Verwijderen van speellijst '${playlist}' mislukt`;

	// Remove (song from) playlist from server
	if (await modifyPlaylist('Remove', playlist, null, errorMessage))
		// playlist is gone, so also clear the songs
		customSongs.innerHTML = '';

	// Clear custom playlist input
	customInput.value = "";

	// Inform user
	showNotification(notificationCustom, `<span class='success'>Speellijst '${playlist}' is verwijderd</span>`);

	// Show/hide save buttons
	updateAddButtons();

	// On Buttons page, update dropdowns
	populatePresetLists();

	// On buttons page, remove playlist from and songs list
	if (playlist === playlistInput.value.trim())
	{
		playlistInput.value = "";
		hideScrollbox(playlistSongs);
	}
}

// CALLBACK entry point: Add song to playlist
async function addSongFromPlaylist(row)
{
	hideNotification(notificationCustom);

	// Get songfile to add
	const songfile = row.dataset.songfile;

	// Get playlist to add song to
	const playlist = customInput.value.trim();

	// Set error template
	const errorMessage = `Toevoegen van '${songfile}' aan speellijst '${playlist}' mislukt`;

	// Add (song to) playlist from server
	if (await modifyPlaylist('Add', playlist, songfile, errorMessage))
	{
		// Also add song to scrollbox - faster than reloading
		const copy = row.cloneNode(true);	// true = deep clone (includes children)
		const icon = copy.querySelector('.save-button-small');
		icon.classList.remove('save-button-small');
		icon.classList.add('delete-button-small');
		customSongs.appendChild(copy);
	}

	// Cleanup
	showScrollbox(customSongs, customInput);
}

// CALLBACK entry point: Add song to playlist
async function delSongFromPlaylist(row)
{
	hideNotification(notificationCustom);

	// Get songfile to remove
	const songfile = row.dataset.songfile;

	// Get playlist to remove song from
	const playlist = customInput.value.trim();

	// Set error template
	const errorMessage = `Verwijderen van '${songfile}' uit speellijst '${playlist}' mislukt`;

	// Remove (song to) playlist from server
    if (await modifyPlaylist('Remove', playlist, songfile, errorMessage))
		// Also remove song from scrollbox - faster than reloading
		row.remove();

	// Cleanup
	showScrollbox(customSongs, customInput);
}

// Send playlist and song to server
async function modifyPlaylist(action, playlist, songfile, errorMessage)
{
	showWaiting();
	try
	{
		const cmd = "modify";
		const args = { "action": action, "playlist": playlist, "song": songfile };
		playlists = (await postJSON(cmd, args)) || [];
		customPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);
		hideWaiting();
		return true;
	}
	catch (err)
	{
		showNotification(notificationCustom, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
	hideWaiting();
	return false;
}

/* ========== Playlist page - Search ========== */

// Show playlist songs in scrollbox
async function submitSearch()
{
	hideNotification(notificationSearch);

	// Get search input
	const pattern = searchInput.value.trim();

	// Check for minimal search pattern length
	if (pattern.length < 3)
	{
		showNotification(notificationSearch, `<span class='warning'>Gebruik een zoekopdracht met minimaal 3 karakters</span>`);
		return;
	}

	// Show waiting indicator
	showWaiting();

	// Get songs from server
	const songs = await getSearchSongs(pattern);
	if (songs.length)
		populateSongsScrollbox(searchInput, searchSongs, songs, notificationSearch);

	// Show/hide save buttons
	updateAddButtons();

	// Show waiting indicator
	hideWaiting();
}

// Function to get the songs for the given search pattern
async function getSearchSongs(pattern)
{
	// Set error template
	const errorMessage = `Ophalen van de liedjes voor '${pattern}' is mislukt`;

	try
	{
		const cmd = "search";
		const args = { "pattern": pattern };

		// Wait for songs to be returned by the server
		const songs = (await postJSON(cmd, args)) || [];

		// Warn if no songs found
		if (songs.length === 0)
		{
			// Inform user playlist is empty
			showNotification(notificationSearch, `<span class='warning'>Geen liedjes gevonden met '${pattern}' in de naam van de artiest of in de titel<br>Gebruik een andere zoekopdracht</span>`);

			// Failed to fetch songs
			return [];
		}

		// Return songs matching search pattern
		return songs;
	}
	catch (err)
	{
		showNotification(notificationSearch, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
}

/* ========== Status page ========== */

// Prevent auto-detect linking datetime strings
document.addEventListener("DOMContentLoaded", () =>
{
	const zeroWidth = "\u200B"; // zero-width space

	// Select all table cells (adjust selector if needed)
	document.querySelectorAll("td").forEach(td =>
	{
		// Only process if it has text content
		if (td.textContent.trim().length > 0)
		{
			// Insert zero-width space before every digit
			td.innerHTML = td.textContent.replace(/(\d)/g, zeroWidth + "$1");
		}
	});
});
