/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

// Prevent auto-detect linking datetime strings
document.addEventListener("DOMContentLoaded", () => {
	const zeroWidth = "\u200B"; // zero-width space

	// Select all table cells (adjust selector if needed)
	document.querySelectorAll("td").forEach(td => {
		// Only process if it has text content
		if (td.textContent.trim().length > 0) {
			// Insert zero-width space before every digit
			td.innerHTML = td.textContent.replace(/(\d)/g, zeroWidth + "$1");
		}
	});
});
