/*!
 * @package:		Oradio3
 * @author url:		https://oradiolabs.nl
 * @author email:	info at oradiolabs dot nl
 * @copyright:		Stichting Oradio, All rights reserved.
 * @license:		GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

// Ping to indicate the client is active
setInterval(async () => {
	fetch("/keep_alive", { method:"POST"});
}, 2000);

// Initialize SPA
document.addEventListener('DOMContentLoaded', () =>
{
	// Observe initial page
	observeActivePage();

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

			// Hide all pages
			document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));

			// Show new active page
			page.classList.add('active');

			// Hide all notifications
			document.querySelectorAll('.notification').forEach(element => { element.style.display = 'none'; });

			// Observe new active page
			observeActivePage();

			// Reset scroll to top
			page.scrollTop = 0;
		});
	});

    // Open scrollbox on input or icon click
    document.addEventListener("click", async (event) =>
	{
		// IMPORTANT: closest identifies icon and scrollbox identification
        const customSelect = event.target.closest(".custom-select");

        // Close all scrollboxes when clicked outside
        if (!customSelect)
		{
            closeAllScrollboxes();
            return;
        }

		// Get closest input, icon and scrollbox elements
        const input = customSelect.querySelector("input");
		const icon = customSelect.querySelector(".custom-icon");
        const scrollbox = customSelect.querySelector(".scrollbox");

        // Click on input or dropdown icon
        if (event.target === input || event.target === icon)
		{
			// Prevent click from propagating to document
            event.stopPropagation();

			// Close any open scrollboxes
            closeAllScrollboxes();

			if (input.id === "SSIDs")
			{
				if (!scrollbox.dataset.populated)
				{
					// Scanning the networks may take a few seconds, so show the waiting indicator
					show_waiting();

					// populate scrollbox
					await fillNetworkDropdown(scrollbox);

					// mark scrollbox as populated
	                scrollbox.dataset.populated = "true";

					// Hide waiting indicator
					hide_waiting();
				}
			}

			// Show the scrollbox for the clicked 
			showScrollbox(scrollbox, input, icon)

			// Reset scrollbox to top
			scrollbox.scrollTop = 0;
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

		const text =
			row.querySelector(".scrollbox-row-text")?.textContent.trim()
			|| row.textContent.trim();

		// Set input value
		input.value = text;

		// Highlight selected row
		scrollbox.querySelectorAll(".scrollbox-row")
			.forEach(r => r.classList.remove("selected"));
		row.classList.add("selected");

		// Close dropdown
		hideScrollbox(scrollbox, icon);

		// CALLBACK: pass both input and scrollbox
		onScrollboxSelect(text, input, scrollbox);
	});

});

// Close the web interface
function closeWebInterface()
{
	// Find the currently visible page
	const currentPage = document.querySelector('.page.active');
	if (!currentPage) return;

	// Clear all content
	currentPage.innerHTML = '<div class="closing">' + 
		'De web interface wordt afgesloten...' +
	'</div>';

	// Remove navigation buttons
	document.querySelector('nav').remove();

	// Show waiting indicator
	show_waiting();

	// Send close command
	fetch("/close", {method: "POST"});
}

/* ---------- Navigation helpers ---------- */

// Check if page needs scrolling
function updatePageScrollState(page)
{
	const images = document.querySelectorAll('nav button span');
	if (page.scrollHeight > page.clientHeight)
		// Hide all images in nav
		images.forEach(img => img.style.display = 'none');
	else
		// Show all images in nav
		images.forEach(img => img.style.display = '');
}

// MutationObserver to track content changes
const contentObserver = new MutationObserver((mutationsList) =>
{
	// We only care about the active page
	const activePage = document.querySelector('.page.active');
	if (activePage)
		updatePageScrollState(activePage);
});

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

/* ---------- Notification helpers ---------- */

// Show waiting indicator
function show_waiting()
{
	document.getElementById('waiting').style.display = "block";
}

// Hide waiting indicator
function hide_waiting()
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

// Hide all notifications
function hideNotifications()
{
	// Select all elements with class "notification"
	const notifications = document.querySelectorAll('.notification');

	// Loop through each element and hide it
	notifications.forEach(element => { element.style.display = 'none'; });
}

/* ---------- Scrollbox helpers ---------- */

// Close all scrollboxes
function closeAllScrollboxes()
{
	document.querySelectorAll(".custom-select").forEach(select =>
	{
        const input = select.querySelector("input");
		const icon = select.querySelector(".custom-icon");
        const scrollbox = select.querySelector(".scrollbox");

		hideScrollbox(scrollbox, input,  icon)
	});
}

// Show scrollbox and rotate icon
function showScrollbox(scrollbox, input, icon)
{
	// Show scrollbox
	scrollbox.style.display = 'block';

	// Rotate if icon is given
	if (icon)
		icon.classList.add('open');

	// Get text from given input
	const inputValue = input.value.trim();

	// Accessability
	input.setAttribute("aria-expanded", "true");

	// Get rows inside the given scrollbox
	const rows = scrollbox.querySelectorAll('.scrollbox-row');

	// Highlight row matching input
	rows.forEach(row =>
	{
		const rowText = row.querySelector('.scrollbox-row-text')?.textContent.trim() || '';
		if (rowText === inputValue)
			row.classList.add('selected');
		else
			row.classList.remove('selected');
	});
}

// Hide scrollbox and rotate icon
function hideScrollbox(scrollbox, input, icon)
{
	scrollbox.style.display = 'none';
	if (icon)
		icon.classList.remove('open');

	// Accessability
	input.setAttribute("aria-expanded", "false");
}

// Create scrollbox row
function createRow(option)
{
	// Create the outer div
	const row = document.createElement("div");
	row.className = "scrollbox-row";

	// Create the inner div
	const rowText = document.createElement("div");
	rowText.className = "scrollbox-row-text";

	// Accessibility: mark each row as an option
	row.setAttribute('role', 'option');		// ARIA role

	// Add the text
	rowText.textContent = option;

	// Put the inner div inside the outer div
	row.appendChild(rowText);

	return row;
}

function onScrollboxSelect(value, input, scrollbox)
{
	console.log("Selected value:", value);
	console.log("Input ID:", input.id);
	console.log("Scrollbox ID", scrollbox.id);
	console.log("Scrollbox class:", scrollbox.className);

    switch (input.id)
	{
        case "SSIDs":
			// Update network input
			input.value = value;

			// Show password input only if network requires it
			showPassword(value)
            break;

        case "playlist":
            break;

        default:
    }

}
