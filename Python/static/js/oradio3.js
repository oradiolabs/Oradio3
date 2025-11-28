/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

// Ping to indicate the client is active
setInterval(() => {
	fetch("/keep_alive", { method:"POST"});
}, 2000);

// Close the web interface
function closeWebInterface() {
	// Show a message about closing the web interface
	const container = document.querySelector(".container");
	container.innerHTML = '<div class="closing">' + 
		'<div>De web interface wordt afgesloten...</div>' +
	'</div>';

	// Remove menu
	document.querySelector('nav').remove();

	// Show waiting indicator
	show_waiting();

	// Send close command
	fetch("/close", {method: "POST"});
}

