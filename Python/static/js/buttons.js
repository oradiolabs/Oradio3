/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

/*
 * Function to submit the changed preset
 * Called:
 * - On playlist in dropdown selected
 * Does: 
 * - Submits changed preset
 * - Notifies user on error
 */
async function savePreset(preset)
{
	// Get notification element
	const notification = document.getElementById("presets_notification");

	// Set error template
	const errorMessage = `Koppelen van '${preset}' is mislukt`

	try
	{
		// Send presets to server
		const response = await fetch('/save_preset',
		{
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({"preset": preset, "playlist": document.getElementById(preset).value})
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

/*
 * Function to get the songs for the selected playlist
 * Called:
 * - On 'Enter'-key while playlist input has focus
 * - On playlist in dropdown selected
 * Does: 
 * - Get songs for playlist input
 *   - Get playlist input
 *   - Validate playlist
 *   - Clear notification
 *   - Fetch songs for playlist input
 *   - Show list of songs or notification if none
 */
async function getPlaylistSongs()
{
	// Get notification element
	const notification = document.getElementById("playlist_notification");

	// Get playlist input
	const playlist = document.getElementById("playlist").value.trim();

	// Get songs list container
	const songlist = document.getElementById("playlist-songs");

	// Check for playlist
	if (!playlist)
	{
		// Hide songs list
		songlist.style.display = "none";

		// Clear and hide notification
		notification.innerHTML = "";
		notification.style.display = "none";

		// Done: nothing to search for
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
				notification.innerHTML = `<span class='warning'>Speellijst '${playlist}' is leeg</span>`;
				notification.style.display = "block";

				// Done: no songs found
				return;
			}

			// Check if playlist is web radio
			if ((/^https?:/.test(songs[0]['file'])))
			{
				// Hide songs list
				songlist.style.display = "none";

				// Notify
				notification.innerHTML = `<span class='warning'>Speellijst '${playlist}' is een webradio</span>`;
				notification.style.display = "block";

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

				// Add song element to songs container
				ul.appendChild(li);
			});

			// Show songs list
			songlist.style.display = "block";

			// Clear and hide notification
			notification.innerHTML = "";
			notification.style.display = "none";

		}
		else
		{
			// Wait for error to be returned from server
			const errorData = await response.json();

			// Notify
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

// Function to submit the song to the server for playback
async function playSong(notification, songfile, songtitle)
{
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
	// Remove focus
	setTimeout(() =>
	{
		document.querySelectorAll('input').forEach(input =>
		{
			input.style.pointerEvents = 'auto';
		});
	}, 0);

	// Create custom select elements dropdown lists with options and actions
	document.querySelectorAll('.custom-select').forEach(container =>
	{
		const input = container.querySelector('input');
		const dropdown = container.querySelector('.options');

		function showOptions()
		{
			// Get alphabetically sorted array with directories and playlist names
			const playlistNames = playlists.map(item => item.playlist);	// Result is typeof object
			let options = directories.concat(playlistNames);			// Treat object as array
			options.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));

			dropdown.innerHTML = '';
			options.forEach(option =>
			{
				const div = document.createElement('div');
				div.textContent = option;

				// Add click event handler
				div.addEventListener('click', (event) =>
				{
					// Cancel the default action that belongs to the event
					event.preventDefault();

					// Ignore if nothing changed
					if (input.value !== option)
					{
						// Update input with option
						input.value = option;

						// Hide dropdown with options
						dropdown.style.display = 'none';

						// Process changed preset
						if (input.id.includes("preset"))
							savePreset(input.id)

						// Process changed playlist
						if (input.id.includes("playlist"))
						{
							// Get playlist songs
							getPlaylistSongs();
						}
					}
				});

				// Add option to list
				dropdown.appendChild(div);
			});

			// Show dropdown
			dropdown.style.display = 'block';
		}

		// Show options when input is clicked
		input.addEventListener("click", (event) =>
		{
			// Prevent click from propagating to document
			event.stopPropagation();

			// Show playlist dropdown
			showOptions();
		});

		// Hide options when input loses focus
		input.addEventListener("focusout", function (event)
		{
			// Cancel the default action that belongs to the event
			event.preventDefault();

			// Delay to allow click on option to be processed
			setTimeout(() =>
			{
				// Hide dropdown
				dropdown.style.display = "none";
			}, 300);
		});

		// Hide options when clicking outside the input or outside the options list
		document.addEventListener("click", function (event)
		{
			if (!container.contains(event.target) && dropdown.style.display == "block")
				dropdown.style.display = "none";
		});

		// Control what happens when the Enter-key is pressed in playlist input
		input.addEventListener("keydown", function (event)
		{
			// Check if Enter-key is pressed
			if (event.key === "Enter")
			{
				// Prevent default behavior
				event.preventDefault();

				// Hide dropdown
				dropdown.style.display = "none";

				// Get playlist input
				const playlist = input.value.trim();

				// Get playlist songs
				getPlaylistSongs();
			}
		});
	});

	// Click handler for playing song
	document.getElementById("playlist-songs").querySelector("ul").addEventListener("click", function(event)
	{
		// Get validated LI
		const li = event.target.closest("li");
		if (!li) return;

		// LI clicked
		playSong(playlist_notification, li.id, li.textContent);
	});

});
