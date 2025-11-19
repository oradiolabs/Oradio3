/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

/*
 * Function to get the songs for the selected playlist
 * Called:
 * - On 'Enter'-key while playlist input has focus
 * - On playlist in dropdown selected
 * - On song added
 * Does: 
 * - Get songs for playlist input
 *   - Get playlist input
 *   - Validate playlist
 *   - Fetch songs for playlist input
 *   - Show list of songs with 'Remove'-button or playlist_notification if none
 */
async function getPlaylistSongs()
{
	// Get playlist input
	const playlist = document.getElementById("autocomplete-input").value.trim();

	// Get songs list container
	const songlist = document.getElementById("playlist-songs");

	// Check for playlist
	if (!playlist)
	{
		// No playlist: hide songlist
		songlist.style.display = "none";

		// Done: no playlist to list songs for
		return;
	}

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// Check playlist exists
	if (!nonWebradioPlaylists.includes(playlist))
	{
		// playlist does not exist: Notify user
		playlist_notification.innerHTML = `<p class='error'>Speellijst '${playlist}' bestaat niet<br>Voeg toe met de <button class="save-button-tiny"></button>-knop</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		const songlist = document.getElementById("playlist-songs");
		songlist.style.display = "none";

		// Done: playlist does not exist
		return;
	}

	// Set error template
	const errorMessage = `Ophalen van de liedjes van speellijst '${playlist}' is mislukt`;

	try
	{
		// Fetch playlist songs
		const response = await fetch('/get_songs',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"source": "playlist", "pattern": playlist})
		});

		// Handle server response
		if (response.ok)
		{
			// Wait for songs to be returned from server
			const songs = await response.json();

			// Notify if no songs
			if (!Array.isArray(songs) || songs.length === 0)
			{
				// Hide songs list
				songlist.style.display = "none";

				// Notify
				playlist_notification.innerHTML = `<p class='warning'>Speellijst '${playlist}' bestaat, maar is leeg<br>Zoek liedjes en voeg toe met de <button class="save-button-tiny"></button>-knop</p>`;
				playlist_notification.style.display = "block";

				// Done: no songs found
				return;
			}

			// Get songs container
			const ul = songlist.querySelector("ul");

			// Clear songs container
			ul.innerHTML = "";

			// Add songs to container
			songs.forEach(song =>
			{
				// Create song element
				const li = document.createElement("li");

				// Song file name
				li.id = song.file;

				// Song artist and title
				li.textContent = `${song.artist} - ${song.title}`;

				// Create button element
				const button = document.createElement("button");

				// Readability
				button.title = "Verwijderen";

				// Styling
				button.classList.add("delete-button-small");

				// Add button element to song
				li.appendChild(button);

				// Add song element to songs container
				ul.appendChild(li);
			});

			// Show songs list
			songlist.style.display = "block";

			// Clear and hide playlist_notification
			playlist_notification.innerHTML = "";
			playlist_notification.style.display = "none";

		}
		else
		{
			// Wait for error to be returned from server
			const errorData = await response.json();

			// Notify
			playlist_notification.innerHTML = `<p class='error'>${errorData.message || errorMessage}</p>`;
			playlist_notification.style.display = "block";
		}
	}

	// Handle server not responding
	catch (error)
	{
		playlist_notification.innerHTML = `<p class='error'>${errorMessage}<br>Controleer of de web interface actief is</p>`;
		playlist_notification.style.display = "block";
	}
}

/*
 * Called:
 * - On 'Enter'-key while search input has focus
 * - On 'Zoeken'-button
 * - After playlist changed
 * Does: 
 * - Get songs for search input
 *   - Get search input
 *   - Validate search pattern
 *   - Fetch songs for search input
 *   - Show list of songs and 'Add'-button or search_notification if none
 */
async function getSearchSongs()
{
	// Get search input
	const search = document.getElementById("search").value.trim();

	// Get songs list container
	const songlist = document.getElementById("search-songs");

	// Check for search pattern
	if (!search)
	{
		// Hide songs list
		songlist.style.display = "none";

		// Clear and hide search_notification
		search_notification.innerHTML = "";
		search_notification.style.display = "none";

		// Done: nothing to search for
		return;
	}

	// Check for minimal search pattern length
	if ((search.length > 0) && (search.length < 3))
	{
		// Hide songs list
		songlist.style.display = "none";

		// Notify
		search_notification.innerHTML = `<span class='warning'>Gebruik een zoekopdracht met minimaal 3 karakters</span>`;
		search_notification.style.display = "block";

		// Done: search pattern too short
		return;
	}

	// Set error template
	const errorMessage = `Ophalen van de liedjes voor '${search}' is mislukt`;

	try
	{
		// Fetch search songs
		const response = await fetch('/get_songs',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"source": "search", "pattern": search})
		});

		// Handle server response
		if (response.ok)
		{
			// Wait for songs to be returned from server
			const songs = await response.json();

			// Notify if no songs
			if (!Array.isArray(songs) || songs.length === 0)
			{
				// Hide songs list
				songlist.style.display = "none";

				// Notify
				search_notification.innerHTML = `<span class='warning'>Geen liedjes gevonden met '${search}' in de naam van de artiest of in de titel<br>Gebruik een andere zoekopdracht</span>`;
				search_notification.style.display = "block";

				// Done: no songs found
				return;
			}

			// Get songs container
			const ul = songlist.querySelector("ul");

			// Clear songs container
			ul.innerHTML = "";

			// Add songs to container
			songs.forEach(song =>
			{
				// Create song element
				const li = document.createElement("li");

				// Song file name
				li.id = song.file;

				// Song artist and title
				li.textContent = `${song.artist} - ${song.title}`;

				// Create button element
				const button = document.createElement("button");

				// Readability
				button.title = "Toevoegen";

				// Styling
				button.classList.add("save-button-small");

				// Add button element to song element
				li.appendChild(button);

				// Add song element to songs container
				ul.appendChild(li);
			});

			// Set the scroll position to the top
			songlist.scrollTop = 0;

			// Show songs list
			songlist.style.display = "block";

			// Clear and hide search_notification
			search_notification.innerHTML = "";
			search_notification.style.display = "none";
		}
		else
		{
			// Wait for error to be returned from server
			const errorData = await response.json();

			// Notify
			search_notification.innerHTML = `<p class='error'>${errorData.message || errorMessage}</p>`;
			search_notification.style.display = "block";
		}
	}

	// Handle server not responding
	catch (error)
	{
		search_notification.innerHTML = `<p class='error'>${errorMessage}<br>Controleer of de web interface actief is</p>`;
		search_notification.style.display = "block";
	}
}

// Add song to playlist. Create playlist if it does not exist.
async function addSong(songfile)
{
	// Get playlist
	const playlist = document.getElementById("autocomplete-input").value.trim();

	// playlist cannot be empty
	if (!playlist)
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Kies een bestaande of typ een nieuwe speellijstnaam</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		const songlist = document.getElementById("playlist-songs");
		songlist.style.display = "none";

		// Done: no playlist given
		return;
	}

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// Ignore if no songfile to add and playlist already exist
	if (!songfile && nonWebradioPlaylists.includes(playlist))
	{
		// Done: playlist already exists
		return;
	}

	// Set error template
	const errorMessage = songfile
		? `Toevoegen van '${songfile}' aan speellijst '${playlist}' mislukt`
		: `Opslaan van speellijst '${playlist}' mislukt`;

	// Add (song to) playlist from server
	if (!await modifyPlaylist('Add', playlist, songfile, errorMessage))
		// Failed adding playlist/song
		return;

	// Add playlist if not yet known
	if (!nonWebradioPlaylists.includes(playlist))
	{
		// Add playlist, which is no webradio
		playlists.push({playlist: playlist, webradio: false});

		// Sort the array case-insensitively based on the 'playlist' property
		playlists.sort((a, b) => {
			const playlistA = a.playlist.toLowerCase();
			const playlistB = b.playlist.toLowerCase();

			if (playlistA < playlistB) {
				return -1;
			}
			if (playlistA > playlistB) {
				return 1;
			}
			return 0;
		});
	}

	if (songfile)
	{
		// Song added: show song lists
		getPlaylistSongs();
	}
	else
	{
		// Playlist added, no song
		playlist_notification.innerHTML = `<p class='success'>Speellijst '${playlist}' is toegevoegd<br>Zoek liedjes en voeg toe met de <button class="save-button-tiny"></button>-knop</p>`;
		playlist_notification.style.display = "block";

		// No playlist: hide songlist
		document.getElementById("playlist-songs").style.display = "none";
	}
}

// Remove song from playlist. Remove playlist if no song.
async function removeSong(songfile)
{
	// Get playlist
	const playlist = document.getElementById("autocomplete-input").value.trim();

	// playlist cannot be empty
	if (!playlist)
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Kies of typ een geldige speellijstnaam</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		const songlist = document.getElementById("playlist-songs");
		songlist.style.display = "none";

		// Done: no playlist given
		return;
	}

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

	// playlist must exist
	if (!nonWebradioPlaylists.includes(playlist))
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Speellijst '${playlist}' bestaat niet</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		const songlist = document.getElementById("playlist-songs");
		songlist.style.display = "none";

		// Done: playlist does not exist
		return;
	}

	// Set error template
	const errorMessage = songfile
		? `Verwijderen van '${songfile}' uit speellijst '${playlist}' mislukt`
		: `Verwijderen van speellijst '${playlist}' mislukt`;

	// Remove (song from) playlist from server
	if (!await modifyPlaylist('Remove', playlist, songfile, errorMessage))
		// Failed adding playlist/song
		return;

	// Handle removed song / removed playlist
	if (!songfile)
	{
		// Clear playlist input
		document.getElementById("autocomplete-input").value = "";

		// Remove playlist from playlists
		playlists = playlists.filter(item => item.playlist !== playlist);

		// playlist removed: Notify user
		playlist_notification.innerHTML = `<p class='success'>Speellijst '${playlist}' is verwijderd</p>`;
		playlist_notification.style.display = "block";
	}

	// Update song lists
	getPlaylistSongs();
}

// Send playlist and song to server
async function modifyPlaylist(action, playlist, songfile, errorMessage)
{
	// Get playlist_notification element
	const playlist_notification = document.getElementById("playlist_notification");

	try
	{
		// Submit action, playlist and song
		const response = await fetch('/playlist_modify',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({ "action": action, "playlist": playlist, "song": songfile ? songfile : null })
		});

		// Handle server response
		if (response.ok)
		{
			// Success
			return true;
		}
		else
		{
			// Notify
			const errorData = await response.json();
			playlist_notification.innerHTML = `<p class='error'>${errorData.message || errorMessage}</p>`;
			playlist_notification.style.display = "block";

			// Fail
			return false;
		}
	}

	// Handle server not responding
	catch (error)
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>${errorMessage}<br>Controleer of de web interface actief is</p>`;
		playlist_notification.style.display = "block";

		// Fail
		return false;
	}
}

// Function to submit the song to the server for playback
async function playSong(notification, songfile, songtitle)
{
	// Set error template
	const errorMessage = `Er is een fout opgetreden bij het indienen van het te spelen liedje '${songtitle}'`;

	document.getElementById(notification);
	try
	{
		// Submit song to play
		const response = await fetch('/play_song',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({ "song": songfile })
		});

		// Handle server response
		if (!response.ok)
		{
			const errorData = await response.json();
			notification.innerHTML = `<p class='error'>${errorData.message || errorMessage}</p>`;
			notification.style.display = "block";
		}
	}

	// Handle server not responding
	catch (error)
	{
		notification.innerHTML = `<p class='error'>${errorMessage}<br>Controleer of de web interface actief is</p>`;
		notification.style.display = "block";
	}
}

// Run when page is loaded
document.addEventListener('DOMContentLoaded', function (event)
{
	// Get notification elements
	const playlist_notification = document.getElementById("playlist_notification");
	const search_notification = document.getElementById("search_notification");

	// Remove focus
	setTimeout(() =>
	{
		document.querySelectorAll('input').forEach(input =>
		{
			input.style.pointerEvents = 'auto';
		});
	}, 0);

	// Create autocomplete dropdown
	document.getElementById('autocomplete-input').addEventListener('input', function()
	{
		// Get input for case insensitive matching
		const inputValue = this.value.toLowerCase();

		// Get dropdown element
		const autocompleteList = document.getElementById('autocomplete-list');

		// Get array with only the names of the playlists which are no webradio
		const nonWebradioPlaylists = playlists.filter(item => !item.webradio).map(item => item.playlist);

		// Initialize autocomplete list
		autocompleteList.innerHTML = '';

		// Do nothing if input is empty
		if (inputValue.length === 0) return;

		// Fill autocomplete list
		const matches = nonWebradioPlaylists.filter(option => option.toLowerCase().includes(inputValue));
		matches.forEach(match =>
		{
			const div = document.createElement('div');
			div.textContent = match;
			div.addEventListener('click', function()
			{
				// Copy the clicked option to the input
				document.getElementById('autocomplete-input').value = match;
				autocompleteList.innerHTML = '';

				// Show playlist songs
				getPlaylistSongs();
			});

			// Add option
			autocompleteList.appendChild(div);

			// Show list of matching options
			autocompleteList.style.display = 'block';

		});
	});

	// Close the autocomplete list when clicking outside of it
	document.addEventListener('click', function(event)
	{
		if (event.target !== document.getElementById('autocomplete-input'))
		{
			// Get dropdown element
			const autocompleteList = document.getElementById('autocomplete-list');

			// Clear autocomplete list
			autocompleteList.innerHTML = '';

			// Hide list of matching options
			autocompleteList.style.display = 'none';
		}
	});

	// Control what happens when the Enter-key is pressed in autocomplete-input
	document.getElementById("autocomplete-input").addEventListener("keydown", function (event)
	{
		// Check if Enter-key is pressed
		if (event.key === "Enter")
		{
			// Prevent default behavior
			event.preventDefault();

			// Show playlist songs
			getPlaylistSongs();
		}
	});

	// Click handler for adding or playing song
	document.getElementById("search-songs").querySelector("ul").addEventListener("click", function(event)
	{
		// Get validated LI
		const li = event.target.closest("li");
		if (!li) return;

		if (event.target.matches("button"))
			// Add button clicked
			addSong(li.id);
		else
			// LI clicked
			playSong(search_notification, li.id, li.textContent);
	});

	// Click handler for removing or playing song
	document.getElementById("playlist-songs").querySelector("ul").addEventListener("click", function(event)
	{
		// Get validated LI
		const li = event.target.closest("li");
		if (!li) return;

		if (event.target.matches("button"))
			// Delete button clicked
			removeSong(li.id);
		else
			// LI clicked
			playSong(playlist_notification, li.id, li.textContent);
	});

	// Control what happens when the Enter-key is pressed in search input
	document.getElementById("search").addEventListener("keydown", function (event)
	{
		// Check if Enter-key is pressed
		if (event.key === "Enter")
		{
			// Prevent default behavior
			event.preventDefault();

			// Get songs for search pattern
			getSearchSongs();
		}
	});

});
