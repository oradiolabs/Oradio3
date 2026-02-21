/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

let notificationCustom, notificationSearch;
let customPlaylists;

// DOMContentLoaded setup for playlists page
document.addEventListener('DOMContentLoaded', () =>
{
	// Get notification elements
	notificationCustom = document.getElementById("notification_custom");
	notificationSearch = document.getElementById("notification_search");

	// Get array with only the names of the playlists which are no webradio
	customPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// Get input with custom playlist name
	const input = document.getElementById("custom");

/*
TODO:
- add/del playlists toevoegen
- +/X iconen met acties toevoegen
*/
	function populateAutocompleteList(input)
	{
		// Hide notification and songs when selecting a new playlist
		hideNotification(notificationCustom);
		hideScrollbox(document.getElementById('custom-songs'));

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
	}

	// Update list when clicked or while typing
	input.addEventListener("focus", (event) => populateAutocompleteList(input));
	input.addEventListener("input", (event) => populateAutocompleteList(input));

	// Buttons
	document.getElementById("addButton").addEventListener("click", () => addCustomPlaylist(input));
	document.getElementById("delButton").addEventListener("click", () => delCustomPlaylist(input));
	document.getElementById("submitSearchButton").addEventListener("click", submitSearch);

	// Clear notifications and hide songs scrollbox on focus
	document.getElementById('search').addEventListener("focus", () =>
	{
		hideNotification(notificationSearch);
		hideScrollbox(document.getElementById('search-songs'));
	});
});

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
	hideScrollbox(document.getElementById('custom-songs'));

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
	await modifyPlaylist('Add', playlist, null, errorMessage);

	// Inform user
	showNotification(notificationCustom, `<span class='success'>Speellijst '${playlist}' is toegevoegd<br>Zoek liedjes en voeg toe met de <span class="save-button-tiny"></span>-knop</span>`);
}

// Remove custom playlist if it exists
async function delCustomPlaylist(input)
{
	// Hide notification and songs when removing a playlist
	hideNotification(notificationCustom);
	hideScrollbox(document.getElementById('custom-songs'));

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

	// Set error template
	const errorMessage = `Verwijderen van speellijst '${playlist}' mislukt`;

	// Remove (song from) playlist from server
	await modifyPlaylist('Remove', playlist, null, errorMessage);

	// Clear custom playlist input
	input.value = "";

	// Inform user
	showNotification(notificationCustom, `<span class='success'>Speellijst '${playlist}' is verwijderd</span>`);
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
	}
	catch (err)
	{
		showNotification(notificationCustom, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}
	hideWaiting();
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
		populateSongsScrollbox('search', document.getElementById('search-songs'), songs);

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
