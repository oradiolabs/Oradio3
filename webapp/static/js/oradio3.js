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

document.addEventListener('DOMContentLoaded', () =>
{
	// Assign action to stop button
	document.querySelector('img.shutdown-button').addEventListener("click", shutdownWebApp);

	// Observe initial page
	observeActivePage();
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

	// Hide empty scrollbox
	if (rows.length === 0)
	{
		hideScrollbox(scrollbox);
		return;
	}

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

	// Clicked outside any custom-select → close all
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

		// Change action whn icon is clicked
		onScrollboxSelect('modify', row);
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
	onScrollboxSelect(row.dataset.action, row);
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
function onScrollboxSelect(action, row)
{
	switch (action)
	{
		case "network":
			// Get selected network ssid
			const ssid = row.querySelector(".scrollbox-row-text").textContent.trim();
			// Show password input only if network requires it
			showPassword(ssid);
			break;

		case "preset1":
		case "preset2":
		case "preset3":
			// Update preset button playlist
			savePreset(action, row.querySelector(".scrollbox-row-text").textContent.trim());
			break;

		case "playlist":
			// Show scrollbox with custom playlist songs
			showSongs(
				row.dataset.input,
				row.dataset.target,
				row.querySelector(".scrollbox-row-text").textContent.trim()
			);
			break;

		case "play":
			// Play selected song
			playSong(
				row.dataset.notify,
				row.dataset.songfile,
				row.querySelector(".scrollbox-row-text").textContent.trim()
			);
			break;

		case "modify":
			// Save/remove song from playlist
			if (row.querySelector(".delete-button-small"))
				delSongFromPlaylist(row);
			else if (row.querySelector(".save-button-small"))
				addSongFromPlaylist(row);
			else
				console.error("Undefined modify request for row:", row);
			break;

		default:
			console.error("Unexpected action for row:", row);
	}
}

// CALLBACK entry point: Show playlist songs in scrollbox
async function showSongs(input, target, playlist)
{
	// Show waiting indicator
	showWaiting();

	// Show scrollbox with playlist songs, hide if empty
	const songs = await getPlaylistSongs(playlist);
	const scrollbox = document.getElementById(target);
	if (songs.length)
		populateSongsScrollbox(input, scrollbox, songs);
	else
		hideScrollbox(scrollbox);

	// Show waiting indicator
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
