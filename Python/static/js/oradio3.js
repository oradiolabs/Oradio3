/*!
 * @package:      Oradio3
 * @author url:   https://oradiolabs.nl
 * @author email: info at oradiolabs dot nl
 * @copyright:    Stichting Oradio, All rights reserved.
 * @license:      GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

/*
 * Use WebSocket to allow server to track site closing using cookie
 */

// Retrieve the value of a cookie by its name
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Get the 'client_token' cookie
const client_token = getCookie('client_token');

// Open a WebSocket connection with the client token as a query parameter
let ws = new WebSocket(`/ws?client_token=${client_token}`);

// Log any WebSocket errors and close the connection
ws.onerror = (err) => {
    console.log("WebSocket error:", err);
    ws.close();
};
