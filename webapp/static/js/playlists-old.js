/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

// Add song to playlist. Create playlist if it does not exist.
async function addSong(songfile)
{
	// Get playlist
	const playlist = document.getElementById("autocomplete-input").value.trim();

	// Get songs list container
	const songlist = document.getElementById("playlist-songs");

	// playlist cannot be empty
	if (!playlist)
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Kies een bestaande of typ een nieuwe speellijstnaam</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		songlist.style.display = "none";

		// Done: no playlist given
		return;
	}

	// Get array with only the names of the lowercase playlists which are webradio
	const webradios = playlists
		.filter(item => item.webradio)
		.map(item => item.playlist.toLowerCase());	// convert all to lowercase

	// Combine the webradios and directories arrays and convert all names to lowercase
	const blocked = [...webradios, ...directories.map(name => name.toLowerCase())];

	// playlist cannot be webradio or directory
	if (blocked.includes(playlist.toLowerCase()))	// compare in lowercase
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>'${playlist}' kan niet gewijzigd worden</p>`;
		playlist_notification.style.display = "block";

		// Done: protected playlist given
		return;
	}

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists
		.filter(item => !item.webradio)
		.map(item => item.playlist);

	// Find a match ignoring case
	const match = nonWebradioPlaylists.find(n => n.toLowerCase() === playlist.toLowerCase());
	if (match && (playlist != match))
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>'${playlist}' bestaat al als '${match}'</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		songlist.style.display = "none";

		// Done: protected playlist given
		return;
	}

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
		songlist.style.display = "none";
	}
}

// Remove song from playlist. Remove playlist if no song.
async function removeSong(songfile)
{
	// Get playlist
	const playlist = document.getElementById("autocomplete-input").value.trim();

	// Get songs list container
	const songlist = document.getElementById("playlist-songs");

	// playlist cannot be empty
	if (!playlist)
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Kies of typ een geldige speellijstnaam</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
		songlist.style.display = "none";

		// Done: no playlist given
		return;
	}

	// Get array with only the names of the lowercase playlists which are webradio
	const webradios = playlists
		.filter(item => item.webradio)
		.map(item => item.playlist.toLowerCase());	// convert all to lowercase

	// Combine the webradios and directories arrays and convert all names to lowercase
	const blocked = [...webradios, ...directories.map(name => name.toLowerCase())];

	// playlist cannot be webradio or directory
	if (blocked.includes(playlist.toLowerCase()))	// compare in lowercase
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>'${playlist}' kan niet verwijderd worden</p>`;
		playlist_notification.style.display = "block";

		// Done: protected playlist given
		return;
	}

	// Get array with only the names of the playlists which are no webradio
	const nonWebradioPlaylists = playlists
		.filter(item => !item.webradio)
		.map(item => item.playlist);

	// playlist must exist
	if (!nonWebradioPlaylists.includes(playlist))
	{
		// Notify
		playlist_notification.innerHTML = `<p class='error'>Speellijst '${playlist}' bestaat niet</p>`;
		playlist_notification.style.display = "block";

		// Hide songs list
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

// Run when page is loaded
document.addEventListener('DOMContentLoaded', function (event)
{
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
});
