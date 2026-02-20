/*!
 * @package:		Oradio3
 * @author url:		https://oradiolabs.nl
 * @author email:	info at oradiolabs dot nl
 * @copyright:		Stichting Oradio, All rights reserved.
 * @license:		GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
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
function closeWebInterface()
{
	// Remove header and navigation
	document.querySelectorAll('header, nav').forEach(el => el.remove());

	// Replace main content with message
	document.querySelector('main').innerHTML = '<div class="closing">' + 
		'De web interface wordt afgesloten...' +
	'</div>';

    // Show waiting indicator
    showWaiting();

	// Send close command
	fetch("/close", {method: "POST"});
}

/* ========== Initialize Single Page Application (SPA) ========== */

document.addEventListener('DOMContentLoaded', () =>
{
	// Assign action to stop button
	document.querySelector('img.stop-btn').addEventListener("click", closeWebInterface);

	// Observe initial page
	observeActivePage();
});

/* ========== Navigation ========== */

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
	// Show scrollbox
	scrollbox.style.display = 'block';

	// Get text from given input
	const inputText = input.value.trim();

	// Get rows inside the given scrollbox
	const rows = scrollbox.querySelectorAll('.scrollbox-row');

	// Highlight row matching input
	rows.forEach(row =>
	{
		const rowText = row.querySelector('.scrollbox-row-text').textContent.trim() || '';
		if (rowText === inputText)
			row.classList.add('selected');
		else
			row.classList.remove('selected');
	});
}

// Hide scrollbox (added for maintainability)
function hideScrollbox(scrollbox)
{
	scrollbox.style.display = 'none';
}

/* ========== Dropdown ========== */

// Open dropdown scrollbox on input or icon click
document.addEventListener("click", async (event) =>
{
	// IMPORTANT: closest identifies icon and scrollbox identification
	const customSelect = event.target.closest(".custom-select");

	// Close all dropdowns when clicked outside
	if (!customSelect)
	{
		closeDropdowns();
		return;
	}

	// Get closest input, icon and scrollbox elements
	const input = customSelect.querySelector("input");
	const icon = customSelect.querySelector(".custom-icon");
	const dropdown = customSelect.querySelector(".scrollbox.dropdown");

	// Click on input or dropdown icon
	if (event.target === input || event.target === icon)
	{
		// Prevent click from propagating to document
		event.stopPropagation();

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
});

// Select row
document.addEventListener("click", (event) =>
{
	// IMPORTANT: closest identifies row clicked
	const row = event.target.closest(".scrollbox-row");

	// Ignore if no row clicked
	if (!row) return;

	// Get closest scrollbox, input and icon elements
	const scrollbox = row.closest(".scrollbox");
	const customSelect = row.closest(".custom-select");
	const input = customSelect.querySelector("input");
	const icon = customSelect.querySelector(".custom-icon");

	if (input)
	{
		// Set input value with sanitized row text
		input.value = row.querySelector(".scrollbox-row-text").textContent.trim();

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
	onScrollboxSelect(row);
});

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

// CALLBACK: action determined by selected row
function onScrollboxSelect(row)
{
	const action = row.dataset.action;
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
			// Show scrollbox with playlist songs
			showSongs(row.querySelector(".scrollbox-row-text").textContent.trim())
			break;

//TODO: buttons en playlists apart afhandelen
        case "play":
			// Play selected song
			playSong(row.dataset.file, row.querySelector(".scrollbox-row-text").textContent.trim())
			break;
/*
TODO:
 playlists
 search songs

        case "playlist":
			// Get selected preset button
			// Get selected playlist
			// Show scrollbox with playlist songs
			showSongs(input.value)
            break;
*/
        default:
			console.log("ERROR: Unexpected action for row:", row);
    }
}
