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

/*
TODO:
- auto-complete list when typing custom-input
- populate custom-list songs box als op item uit custom-input wordt gekozen
- X iconen toevoegen
*/
	// Create autocomplete dropdown
	document.getElementById("custom-input").addEventListener('input', function()
	{
console.log("playlist input used");
	});

	// Button
	document.getElementById("submitSearchButton").addEventListener("click", submitSearch);

	// Clear notifications and hide songs scrollbox on focus
	document.getElementById('search').addEventListener("focus", () =>
	{
		hideNotification(notificationSearch);
		hideScrollbox(document.getElementById('search-songs'));
	});
});

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
// populateSongsScrollbox is nu de versie van buttons.js. playlists versie maken waar ook + iconen in getoond worden, inclusief callbacks
		populateSongsScrollbox(document.getElementById('search-songs'), songs);

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
		// Request songs matching search pattern from server
		const response = await fetch('/get_songs',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"source": "search", "pattern": pattern})
		});

		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notificationSearch, `<span class="error">${errorData.message || errorMessage}</span>`);

			// Failed to fetch playlist songs
			return [];
		}

		// Wait for songs to be returned by the server
		const songs = (await response.json()) || [];

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

	// Handle server not responding
	catch (err)
	{
		showNotification(notificationSearch, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);

		// Failed to fetch songs
		return [];
	}
}
