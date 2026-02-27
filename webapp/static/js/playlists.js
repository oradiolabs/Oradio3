/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

let notificationCustom, notificationSearch;
let customSongs, searchSongs;
let customPlaylists;

// DOMContentLoaded setup for playlists page
document.addEventListener('DOMContentLoaded', () =>
{
	// Get notification elements
	notificationCustom = document.getElementById("notification_custom");
	notificationSearch = document.getElementById("notification_search");

	// Get songs scrollbox elements
	customSongs = document.getElementById('custom-songs');
	searchSongs = document.getElementById('search-songs');

	// Get array with only the names of the playlists which are no webradio
	customPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// Get input with custom playlist name
	const input = document.getElementById("custom");

	function populateAutocompleteList(input)
	{
		// Hide notification and songs when selecting a new playlist
		hideNotification(notificationCustom);
		hideScrollbox(customSongs);

		const listbox = document.getElementById('custom-list');

		// Get input for case insensitive matching
		const playlist = input.value.toLowerCase();

		// Show items matching input, empty shows all
		const matches = playlist.length
			? customPlaylists.filter(item => item.toLowerCase().includes(playlist))
			: customPlaylists;

		// Fill and show autocomplete list if it has entries, hide otherwise
		populateCustomDropdown(matches);
		showScrollbox(listbox, input);

		// Show/hide save buttons
		updateAddButtons();
	}

	// Update list when clicked or while typing
	input.addEventListener("focus", (event) => populateAutocompleteList(input));
	input.addEventListener("input", (event) => populateAutocompleteList(input));

	// Show/hide add and del buttons
	input.addEventListener('inputValueChanged', () => updateAddButtons());
	customSongs.addEventListener('scrollboxPopulated', () => updateDelButtons());

	// Buttons
	document.getElementById("addButton").addEventListener("click", () => addCustomPlaylist(input));
	document.getElementById("delButton").addEventListener("click", () => delCustomPlaylist(input));
	document.getElementById("submitSearchButton").addEventListener("click", submitSearch);

	// Clear notifications and hide songs scrollbox on focus
	document.getElementById('search').addEventListener("focus", () =>
	{
		hideNotification(notificationSearch);
		hideScrollbox(searchSongs);
	});
});

// Add/remove save buttons to add songs to playlist
function updateAddButtons()
{
	const playlist = document.getElementById("custom").value.trim();
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
	// Get dropdown element
	const dropdown = document.getElementById('custom-list');

	// Add playlists to dropdown
	const fragment = document.createDocumentFragment();
	playlists.forEach(playlist =>
	{
		const row = createRow(playlist);
		row.dataset.action = "playlist";
		row.dataset.input = "custom";
		row.dataset.target = "custom-songs";
		fragment.appendChild(row);
	});
	dropdown.replaceChildren(fragment);

	// Mark populated
	dropdown.dataset.populated = true;
}

// Create custom playlist if it does not exist
async function addCustomPlaylist(input)
{
	// Hide notification and songs when adding a new playlist
	hideNotification(notificationCustom);
	hideScrollbox(customSongs);

	// Get input value or empty string
	const playlist = input.value.trim() || "";

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
async function delCustomPlaylist(input)
{
	// Hide notification and songs when removing a playlist
	hideNotification(notificationCustom);
	hideScrollbox(customSongs);

	// Get input value or empty string
	const playlist = input.value.trim() || "";

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
		showNotification(notificationCustom, `<span class='warning'> 'Speellijst ${playlist}' is niet verwijderd, want gekoppeld aan voorkeursknop(pen)</span>`);
		return;
	}

	// Set error template
	const errorMessage = `Verwijderen van speellijst '${playlist}' mislukt`;

	// Remove (song from) playlist from server
	if (await modifyPlaylist('Remove', playlist, null, errorMessage))
		// playlist is gone, so also clear the songs
		customSongs.innerHTML = '';

	// Clear custom playlist input
	input.value = "";

	// Inform user
	showNotification(notificationCustom, `<span class='success'>Speellijst '${playlist}' is verwijderd</span>`);

	// Show/hide save buttons
	updateAddButtons();

	// On uttons page, update dropdowns
	populatePresetLists();

	// On buttons page, remove playlist from and songs list
	if (playlist === document.getElementById("playlist").value.trim())
	{
		document.getElementById("playlist").value = "";
		hideScrollbox(document.getElementById("playlist-songs"));
	}
}

// CALLBACK entry point: Add song to playlist
async function addSongFromPlaylist(row)
{
	// Get songfile to add
	const songfile = row.dataset.songfile;

	// Get playlist to add song to
	const playlist = document.getElementById("custom").value.trim();

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
	showScrollbox(customSongs, document.getElementById("custom"));
}

// CALLBACK entry point: Add song to playlist
async function delSongFromPlaylist(row)
{
	// Get songfile to remove
	const songfile = row.dataset.songfile;

	// Get playlist to remove song from
	const playlist = document.getElementById("custom").value.trim();

	// Set error template
	const errorMessage = `Toevoegen van '${songfile}' aan speellijst '${playlist}' mislukt`;

	// Remove (song to) playlist from server
    if (await modifyPlaylist('Remove', playlist, songfile, errorMessage))
		// Also remove song from scrollbox - faster than reloading
		row.remove();

	// Cleanup
	showScrollbox(customSongs, document.getElementById("custom"));
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

// Show playlist songs in scrollbox
async function submitSearch()
{
	hideNotification(notificationSearch);

	// Get search input
	const pattern = document.getElementById("search").value.trim();

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
		populateSongsScrollbox('search', searchSongs, songs);

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
