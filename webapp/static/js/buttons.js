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
			row.dataset.input = "playlist";
			row.dataset.target = "playlist-songs";
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
		const cmd = "preset";
		const args = { "preset": preset, "playlist": playlist };
		postJSON(cmd, args);
	}
	catch (err)
	{
		showNotification(notificationPresets, `<span class="error">${errorMessage}<br>${err.message || 'Onbekende fout'}</span>`);
		console.error(err);
	}

	// Hide waiting indicator
	hideWaiting();
}
