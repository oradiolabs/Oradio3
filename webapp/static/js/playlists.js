/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

let notificationCustom, notificationSearch;

// DOMContentLoaded setup for playlists page
document.addEventListener('DOMContentLoaded', () =>
{
	// Get notification elements
	notificationCustom = document.getElementById("notification_playlists");
	notificationSearch = document.getElementById("notification_search");

	const input = document.getElementById("custom");

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

/*
TODO:
- X iconen toevoegen
*/
	function populateAutocompleteList(input)
	{
		// Get input for case insensitive matching
		const inputValue = input.value.toLowerCase();

		// Show items matching input, empty shows all
		const matches = inputValue.length
			? nonWebradioPlaylists.filter(item => item.toLowerCase().includes(inputValue))
			: nonWebradioPlaylists;

		// Hide aongs when selecting a new playlist
		hideScrollbox(document.getElementById('custom-songs'));

		// Fill and show autocomplete list if it has entries, hide otherwise
		if (matches.length)
		{
			populateCustomDropdown(matches);
			showScrollbox(document.getElementById('custom-list'), input);
		}
		else
			hideScrollbox(document.getElementById('custom-list'));
	}

	// Update list when clicked or while typing
	input.addEventListener("focus", () => populateAutocompleteList(input));
	input.addEventListener("input", () => populateAutocompleteList(input));

	// Button
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

	dropdown.dataset.populated = true;
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
