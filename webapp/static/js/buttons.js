/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

let notificationPresets, notificationPlaylist;

// DOMContentLoaded setup for buttons page
document.addEventListener('DOMContentLoaded', () =>
{
	// Notifications
	notificationPresets = document.getElementById('notification_presets');
	notificationPlaylist = document.getElementById('notification_playlist');

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
			fragment.appendChild(row);
		});
		dropdown.replaceChildren(fragment);

		dropdown.dataset.populated = "true";
		hideWaiting();
	});

	// Clear notifications and hide songs scrollbox on focus
	document.getElementById('playlist').addEventListener("focus", () =>
	{
		hideNotification(notificationPlaylist);
		hideScrollbox(document.getElementById('playlist-songs'));
	});
});

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
		// Send preset and playlist to server
		const response = await fetch('/save_preset',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"preset": preset, "playlist": playlist})
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notificationPresets, `<span class="error">${errorData.message || errorMessage}</span>`);
		}
	}

	// Handle server not responding
	catch (err)
	{
		showNotification(notificationPresets, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);
	}

	// Show waiting indicator
	hideWaiting();
}

// CALLBACK entry point: Show playlist songs in scrollbox
async function showSongs(playlist)
{
	// Show waiting indicator
	showWaiting();

	// Show scrollbox with playlist songs
	const songs = await getPlaylistSongs(playlist);

	if (songs.length)
		populateSongsScrollbox(document.getElementById('playlist-songs'), songs);

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
		// Request songs from server
		const response = await fetch('/get_songs',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"source": "playlist", "pattern": playlist})
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notificationPlaylist, `<span class="error">${errorData.message || errorMessage}</span>`);

			// Failed to fetch playlist songs
			return [];
		}

		// Wait for songs to be returned by the server
		const songs = (await response.json()) || [];

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

	// Handle server not responding
	catch (err)
	{
		showNotification(notificationPlaylist, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);

		// Failed to fetch songs
		return [];
	}
}

// Convert songs into options
function populateSongsScrollbox(scrollbox, songs)
{
	const fragment = document.createDocumentFragment();
	songs.forEach(song =>
	{
		const row = createRow(`${song.artist} - ${song.title}`);
		row.dataset.file = song.file;
		row.dataset.action = "play";
		fragment.appendChild(row);
	});
	scrollbox.replaceChildren(fragment);

	// mark scrollbox as populated
	scrollbox.dataset.populated = "true";

	// Show the scrollbox for the clicked 
	showScrollbox(scrollbox, document.getElementById('playlist'))

	// Reset scrollbox to top
	scrollbox.scrollTop = 0;
}

// CALLBACK entry point: Submit the song to the server for playback
async function playSong(songfile, songtitle)
{
	// Clear notification
	hideNotification(notificationPlaylist);

	// Set error template
	const errorMessage = `Er is een fout opgetreden bij het indienen van het te spelen liedje '${songtitle}'`;

	try
	{
		// Submit song to play
		const response = await fetch('/play_song',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({ "song": songfile })
		});

		// Check for errors
		if (!response.ok)
		{
			// Get fetch error message
			const errorData = await response.json().catch(() => ({}));

			// Inform user what went wrong
			showNotification(notificationPlaylist, `<span class="error">${errorData.message || errorMessage}</span>`);
		}
	}

	// Handle server not responding
	catch (err)
	{
		showNotification(notificationPlaylist, `<span class="error">${errorMessage}<br>Controleer of de webapp actief is</span>`);
		console.log(err);
	}
}
