#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on December 23, 2024
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary: Class for web interface and web server
    :Note
    :Install
    :Documentation
        https://fastapi.tiangolo.com/
"""
from os import path
from re import match
from uuid import uuid4
from typing import Optional
from json import load, JSONDecodeError
from asyncio import sleep, Task, create_task, current_task
from pydantic import BaseModel
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect
from fastapi.websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

#### oradio modules ######################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script, safe_put, load_presets, store_presets
from wifi_service import get_wifi_networks, get_saved_network
from mpd_control import MPDControl

#### GLOBAL constants ####################
from oradio_const import (
    GREEN, NC,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_REQUEST_CONNECT,
    MESSAGE_REQUEST_STOP,
    MESSAGE_WEB_SERVICE_SOURCE,
    MESSAGE_WEB_SERVICE_PL_WEBRADIO,
    MESSAGE_WEB_SERVICE_PL1_CHANGED,
    MESSAGE_WEB_SERVICE_PL2_CHANGED,
    MESSAGE_WEB_SERVICE_PL3_CHANGED,
    MESSAGE_WEB_SERVICE_PLAYING_SONG,
    MESSAGE_NO_ERROR,
)

#### LOCAL constants #####################
# Web file with wifi credentials
WIFI_FILE     = "/tmp/Wifi_invoer.json"
# templates
EMPTY_PRESETS = {"preset1": "", "preset2": "", "preset3": ""}
INFO_MISSING  = {"serial": "not found", "version": "not found"}
INFO_ERROR    = {"serial": "undefined", "version": "undefined"}
# Location of version info
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# Initialise MPD client
mpd_control = MPDControl()

# Get the web server app
api_app = FastAPI()

# Get the path for the server to mount/find the web pages and associated resources
web_path = path.dirname(path.realpath(__file__))

# Mount static files
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Initialize templates with custom filters and globals
templates = Jinja2Templates(directory=web_path+"/templates")

#### FAVICON #############################

@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """ Handle default browser request for /favicon.ico """
    return FileResponse(path.dirname(__file__) + '/static/favicon.ico')

#### BUTTONS #############################

@api_app.get("/buttons")
async def buttons_page(request: Request):
    """
    Render the buttons management page
    This page allows users to:
     - Assign playlists to presets
     - View songs within playlists
    Returns: HTML page populated with current presets, music directories, and playlists
    """
    oradio_log.debug("Serving buttons page")

    # Return playlist page and presets, directories and playlists as context
    context = {
        "presets"     : load_presets(),
        "directories" : mpd_control.get_directories(),
        "playlists"   : mpd_control.get_playlists()
    }
    return templates.TemplateResponse(request=request, name="buttons.html", context=context)

class ChangedPreset(BaseModel):
    """
    Data model for assigning a playlist to a preset
    preset (str): Preset identifier (e.g., 'preset1')
    playlist (str): Playlist name to assign
    """
    preset:   str = None
    playlist: str = None

# POST endpoint to save changed preset
@api_app.post("/save_preset")
async def save_preset(changedpreset: ChangedPreset):
    """
    Handle saving changes when a playlist is assigned to a preset
    changedpreset (ChangedPreset): Contains the preset identifier and the playlist to assign
     - Loads current presets
     - Updates the specified preset with the new playlist
     - Stores the updated presets
     - Sends a notification message to the web service queue about the change
     - Handles web radio presets differently by sending a specific state message
     - Logs errors if the preset identifier is invalid
    """
    oradio_log.debug("Save changed preset '%s' to playlist '%s'", changedpreset.preset, changedpreset.playlist)

    message = {"source": MESSAGE_WEB_SERVICE_SOURCE, "error": MESSAGE_NO_ERROR}

    # Message state options
    preset_map = {
        "preset1": MESSAGE_WEB_SERVICE_PL1_CHANGED,
        "preset2": MESSAGE_WEB_SERVICE_PL2_CHANGED,
        "preset3": MESSAGE_WEB_SERVICE_PL3_CHANGED
    }

    if changedpreset.preset in preset_map:
        # load presets
        presets = load_presets()

        # Modify preset
        presets[changedpreset.preset] = changedpreset.playlist
        oradio_log.debug("Preset '%s' playlist changed to '%s'", changedpreset.preset, changedpreset.playlist)

        # Store presets
        store_presets(presets)

#REVIEW Onno: Send only which preset has changed, let oradio_control check if changed preset is a webradio or not
        if mpd_control.is_webradio(mpdlist=changedpreset.playlist):
            # Send message playlist is web radio
            message["state"] = MESSAGE_WEB_SERVICE_PL_WEBRADIO
            oradio_log.debug("Send web service message: %s", message)
            safe_put(api_app.state.queue, message)
        else:
            # Send message which playlist has changed
            message["state"] = preset_map[changedpreset.preset]
            oradio_log.debug("Send web service message: %s", message)
            safe_put(api_app.state.queue, message)

    else:
        oradio_log.error("Invalid preset '%s'", changedpreset.preset)

#### PLAYLISTS ###########################

@api_app.get("/playlists")
async def playlists_page(request: Request):
    """
    Render the playlists management page
    This page allows users to:
     - Manage their playlists (create, add songs, remove songs, delete playlists)
     - Search songs by artist and title tags
    Returns: HTML page showing available playlists
    """
    oradio_log.debug("Serving playlists page")

    context = {"playlists": mpd_control.get_playlists()}
    return templates.TemplateResponse(request=request, name="playlists.html", context=context)

class Modify(BaseModel):
    """
    Data model for modifying playlists
    action (str): The action to perform ('Add' or 'Remove')
    playlist (str): Name of the playlist to modify
    song (Optional[str]): Song to add or remove. If None, the playlist itself is created or deleted
    """
    action: str = None
    playlist: str = None
    song: Optional[str] = None

# POST endpoint to modify playlist
@api_app.post("/playlist_modify")
async def playlist_modify(modify: Modify):
    """
    Handle playlist modification requests
     - 'Add': Add a song to a playlist or create a new playlist if it does not exist
     - 'Remove': Remove a song from a playlist or delete the playlist if no song is specified
    modify (Modify): Contains the action, playlist name, and optionally a song
    Returns: Success or error response depending on the action
    """
    if modify.action == 'Add':
        if modify.song is None:
            oradio_log.debug("Create playlist: '%s'", modify.playlist)
        else:
            oradio_log.debug("Add song '%s' to playlist '%s'", modify.song, modify.playlist)
        return mpd_control.add(modify.playlist, modify.song)

    if modify.action == 'Remove':
        if modify.song is None:
            oradio_log.debug("Delete playlist: '%s'", modify.playlist)
        else:
            oradio_log.debug("Delete song '%s' from playlist '%s'", modify.song, modify.playlist)
        return mpd_control.remove(modify.playlist, modify.song)

    oradio_log.error("Unexpected action '%s'", modify.action)
    return JSONResponse(status_code=400, content={"message": f"De action '{modify.action}' is ongeldig"})

#### SHARED: BUTTONS AND PLAYLISTS #######

class Songs(BaseModel):
    """
    Data model for requesting songs
    source (str): Source type, either 'playlist' or 'search'
    pattern (str): Playlist name or search pattern depending on the source
    """
    source:  str = None
    pattern: str = None

# POST endpoint to get songs
@api_app.post("/get_songs")
async def get_songs(songs: Songs):
    """
    Retrieve songs based on the given source and pattern
    songs (Songs): Contains source type and pattern
    Returns: Songs from the specified playlist or search results
    """
    oradio_log.debug("Serving songs from '%s' for pattern '%s'", songs.source, songs.pattern)

    if songs.source == 'playlist':
        return mpd_control.get_songs(songs.pattern)

    if songs.source == 'search':
        return mpd_control.search(songs.pattern)

    oradio_log.error("Invalid source '%s'", songs.source)
    return JSONResponse(status_code=400, content={"message": f"De source '{songs.source}' is ongeldig"})

class Song(BaseModel):
    """
    Data model representing a single song to play
    song (str): Song identifier or path
    """
    song: str = None

# POST endpoint to play song
@api_app.post("/play_song")
async def play_song(song: Song):
    """
    Play the specified song
    song (Song): The song to play
    Behavior:
     - Triggers MPD control to play the song
     - Sends a notification message indicating a song is playing
    Returns: None
    """
    oradio_log.debug("play song: '%s'", song.song)

    # Call MPD to play selected song
    mpd_control.play_song(song.song)

    # Create message
    message = {
        "source": MESSAGE_WEB_SERVICE_SOURCE,
        "state" : MESSAGE_WEB_SERVICE_PLAYING_SONG,
        "error" : MESSAGE_NO_ERROR
    }

    # Put message in queue
    oradio_log.debug("Send web service message: %s", message)
    safe_put(api_app.state.queue, message)

#### STATUS ##############################

def _get_sw_info():
    """
    Retrieve software configuration information from the software version JSON file
    Returns: Contains 'serial' and 'version' keys with software info
             If the file is missing or unreadable, returns default placeholders
    """
    oradio_log.debug("Get software info")

    # Try to load software version info
    try:
        with open(SOFTWARE_VERSION_FILE, "r", encoding="utf-8") as file:
            data = load(file)
            software_info = {
                "serial": data.get("serial", "missing serial"),
                "version": data.get("gitinfo", "missing gitinfo")
            }
    except FileNotFoundError:
        oradio_log.error("Software version info '%s' not found", SOFTWARE_VERSION_FILE)
        software_info = INFO_MISSING
    except (JSONDecodeError, PermissionError, OSError) as ex_err:
        oradio_log.error("Failed to read '%s'. error: %s", SOFTWARE_VERSION_FILE, ex_err)
        software_info = INFO_ERROR

    # Return sanitized data set
    return software_info

@api_app.get("/status")
async def status_page(request: Request):
    """
    Serve the status page with hardware and software information
    Returns: Status page populated with
              - Oradio serial number from hardware command
              - Software serial and version
    """
    oradio_log.debug("Serving status page")

    # Get RPI serial number
    cmd = 'vcgencmd otp_dump | grep "28:" | cut -c 4-'
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get serial number, error: %s", cmd, response)
        serial = "Unknown"
    else:
        serial = response

    # Get software configuration info
    sw_info = _get_sw_info()

    # Return status page and serial and active wifi connection as context
    context = {
                "serial"     : serial,
                "sw_serial"  : sw_info['serial'],
                "sw_version" : sw_info['version']
            }
    return templates.TemplateResponse(request=request, name="status.html", context=context)

#### NETWORK #############################

@api_app.get("/network")
async def network_page(request: Request):
    """
    Serve the network management page
    This page provides information about:
     - The saved wifi connection before the access point was started
     - The current Spotify device name
    Returns: Network page with saved SSID and Spotify name
    """
    oradio_log.debug("Serving network page")

    # Get Spotify name
    oradio_log.debug("Get Spotify name")
    cmd = "systemctl show librespot | sed -n 's/.*--name \\([^ ]*\\).*/\\1/p' | uniq"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Get the network Oradio was connected to before starting access point, empty string if None
    oldssid = get_saved_network()

    # Return network page and saved wifi connection and spotify name as context
    context = {
                "oldssid" : oldssid,
                "spotify" : response
            }
    return templates.TemplateResponse(request=request, name="network.html", context=context)

# POST endpoint to get wifi networks
@api_app.post("/get_networks")
async def get_networks():
    """
    Handle POST request to retrieve the SSIDs of active wifi networks
    Returns: A list of available wifi network SSIDs
    """
    oradio_log.debug("Serving active wifi networks")

    # Return available wifi networks
    return get_wifi_networks()

class Credentials(BaseModel):
    """
    Data model representing wifi network credentials
    ssid (str): The SSID (network name) of the Wifi
    pswd (str): The password for the wifi network
    """
    ssid: str = None
    pswd: str = None

# POST endpoint to connect to wifi network
@api_app.post("/wifi_connect")
async def wifi_connect(credentials: Credentials):
    """
    Handle POST request to save wifi credentials and initiate connection
    The credentials are sent to the parent web service
    credentials (Credentials): The wifi ssid and password
    """
    oradio_log.debug("Saving credentials for connection to '%s' to '%s'", credentials.ssid, WIFI_FILE)

    # Send connect message to web service
    message = {
        "request": MESSAGE_REQUEST_CONNECT,
        "ssid"  : credentials.ssid,
        "pswd"  : credentials.pswd
    }
    safe_put(api_app.state.queue, message)

class Spotify(BaseModel):
    """
    Data model representing the Spotify device name
    name (str): The Spotify device name (allowed characters: letters, numbers, '-' and '_')
    """
    name: str = None

# POST endpoint to set Spotify device name
@api_app.post("/spotify")
async def spotify_name(spotify: Spotify):
    """
    Handle POST request to update the Spotify device name
    Validates the device name to ensure it only contains allowed characters,
    updates the librespot systemd service configuration,
    reloads systemd, and restarts the librespot service
    spotify (Spotify): The new Spotify device name
    Returns: The validated Spotify device name on success, or
             HTTP 400 with error message if validation or any system command fails
    """
    oradio_log.debug("Set Spotify name to '%s'", spotify.name)

    # Regex pattern to validate Spotify device name characters
    pattern = r'^[A-Za-z0-9_-]+$'
    if not bool(match(pattern, spotify.name)):
        response = f"'{spotify.name}' is ongeldig. Alleen hoofdletters, kleine letters, cijfers, - of _ is toegestaan"
        oradio_log.error(response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Update the librespot.service file with the new device name
    cmd = f"sudo sed -i 's/--name \\S*/--name {spotify.name}/' /etc/systemd/system/librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Reload systemd daemon to apply changes in the service file
    cmd = "sudo systemctl daemon-reload"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Restart the librespot service to activate the new device name
    cmd = "sudo systemctl restart librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Return the new device name on success
    return spotify.name

#### WEBSOCKET ###########################

class WebSocketManager:
    """
    Manages active WebSocket connections keyed by client token.
    Each client token can have multiple tabs/windows connected.
    """
    def __init__(self):
        """Initialize the WebSocketManager with an empty dictionary of clients."""
        self.clients: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_token: str):
        """
        Accept a new WebSocket connection and register it under the client token.
        Multiple tabs/windows are supported per token.
        
        Args:
            websocket (WebSocket): The WebSocket connection to accept.
            client_token (str): The unique identifier for the client.
        """
        # Accept the incoming WebSocket connection
        await websocket.accept()
        # Add the WebSocket to the list of connections for this client token
        self.clients.setdefault(client_token, []).append(websocket)

    async def disconnect(self, websocket: WebSocket, client_token: str) -> bool:
        """
        Remove a WebSocket connection for a given client token.
        
        Args:
            websocket (WebSocket): The WebSocket connection to remove.
            client_token (str): The client token associated with the connection.
        
        Returns:
            bool: True if this was the last connection for the client token, False otherwise.
        """
        # Get current connections for the client
        conns = self.clients.get(client_token, [])

        if not conns:
            # No client to disconnect
            return True

        if websocket in conns:
            # Remove the disconnected WebSocket
            conns.remove(websocket)

        # Fully await websocket is closed
        try:
            await websocket.close()
        # Safe to ignore during shutdown
        except (
            ConnectionClosed,
            ConnectionClosedOK,
            ConnectionClosedError,
            OSError,
            RuntimeError,   # raised by Starlette/Uvicorn if already closed
        ):
            pass

        if not conns:
            # Remove token if no connections left
            self.clients.pop(client_token, None)
            # No more connections for this token
            return True

        # There are othe connections
        return False

# Global WebSocket state
active_ws_tasks: dict[str, list[Task]] = {}
disconnect_tasks: dict[str, Task] = {}

async def delayed_stop(client_token: str, delay: float = 2.0):
    """
    Waits for a short delay before sending a stop message for a client token
    if there are no active WebSocket connections for that token.
    
    This can be used to prevent prematurely stopping a service while a client
    might still be connecting or reconnecting.

    Args:
        client_token (str): The unique identifier of the client.
        delay (float, optional): Time in seconds to wait before checking. Defaults to 2.0.
    """
    await sleep(delay)  # Wait for the specified delay

    # Wait until all websocket handler tasks fully exit
    while client_token in active_ws_tasks:
        await sleep(0.1)

    # Check if there are still any active connections for this client
    if client_token not in manager.clients or not manager.clients[client_token]:
        # If no connections remain, send a stop message to the service queue
        message = {"request": MESSAGE_REQUEST_STOP}
        safe_put(api_app.state.queue, message)  # Safely put message in queue
        oradio_log.info("All tabs for client '%s' closed, STOP message sent", client_token)

    # Remove the task tracking entry for this client
    disconnect_tasks.pop(client_token, None)

# Global manager instance
manager = WebSocketManager()

@api_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint that supports multiple tabs/windows per client token.
    Closes only the specific tab on disconnect, and sends STOP message
    when the last tab of a client closes.

    Args:
        websocket (WebSocket): The WebSocket connection provided by FastAPI.
    """
    # Retrieve client token from query parameters
    client_token = websocket.query_params.get("client_token")
    if not client_token:
        oradio_log.warning("Reject unexpected access: no client token")
        # Close the connection if no token is provided
        await websocket.close()
        return

    # Track this WebSocket handler task
    task = current_task()
    active_ws_tasks.setdefault(client_token, set()).add(task)

    # Register the browser tab
    await manager.connect(websocket, client_token)
    oradio_log.info("Client '%s' connected (tab added)", client_token)

    try:
        while True:
            # Wait for any WebSocket event
            event = await websocket.receive()

            # Break the loop if the client disconnects
            if event["type"] == "websocket.disconnect":
                oradio_log.info("Client '%s' tab disconnected", client_token)
                break

    # Raised by FastAPI / Starlette when a client disconnects gracefully
    except WebSocketDisconnect:
        pass

    # Safety net for network drops or server issues
    except Exception as ex_err: # pylint: disable=broad-exception-caught
        oradio_log.error("Client '%s' WebSocket error: %s", client_token, ex_err)

    finally:
        # Remove task from active set
        active_ws_tasks[client_token].discard(task)
        if not active_ws_tasks[client_token]:
            active_ws_tasks.pop(client_token, None)

        # Attempt to disconnect the websocket from the manager
        empty = await manager.disconnect(websocket, client_token)

        # If this was the last connection for the client, schedule a delayed stop
        if empty:
            # Cancel any previously scheduled stop task to avoid duplicates
            if client_token in disconnect_tasks:
                disconnect_tasks[client_token].cancel()

            # Schedule a new delayed stop task for this client
            disconnect_tasks[client_token] = create_task(delayed_stop(client_token))

#### CATCH ALL ###########################

@api_app.route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Catch-all endpoint to handle undefined routes.
    - Ensures a 'client_token' cookie exists for the user.
    - Redirects the client to '/buttons'.
    - Cookie is accessible from JavaScript for WebSocket use.

    Args:
        request (Request): The incoming HTTP request.

    Returns:
        RedirectResponse: Redirects the client to '/buttons' with a cookie.
    """
    oradio_log.debug("Catchall triggered for path: %s", request.url.path)

    # Retrieve the client token from cookies
    client_token = request.cookies.get("client_token")
    if not client_token:
        # Assign a new unique token
        client_token = str(uuid4())
        oradio_log.debug("Assigning new client token: %s", client_token)

    # Create redirect response
    response = RedirectResponse(url='/buttons', status_code=302)

    # Set the cookie for 1 year, accessible from JS
    response.set_cookie(
        key="client_token",
        value=client_token,
        max_age=60*60*24*365,   # 1 year
        httponly=False,         # must be accessible to JS for WS
        samesite="Lax"
    )

    # Redirect with cookie
    return response

# Entry point for stand-alone operation
if __name__ == '__main__':

    # Imports only relevant when stand-alone
    import uvicorn
    from multiprocessing import Process, Event, Queue
    from queue import Empty

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    def _check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        try:
            while not stop_event.is_set():
                try:
                    # Wait for message with 1s timeout
                    message = queue.get(block=True, timeout=0.5)
                    # Show message received
                    print(f"\n{GREEN}Message received: '{message}'{NC}\n")
                except Empty:
                    continue
        except KeyboardInterrupt:
            print("Listener process interrupted by KeyboardInterrupt")

    # Initialize
    stop_event = Event()
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=_check_messages, args=(message_queue,))
    message_listener.start()

    # Pass the queue to the web server
    api_app.state.queue = message_queue

    try:
        # Start the web server with log level 'trace'. log_config=Nonoe: Prevent overriding our log setup
        uvicorn.run(
            api_app,
            host=WEB_SERVER_HOST,
            port=WEB_SERVER_PORT,
            log_config=None,
            log_level="trace",
            # >= 2s is safe for small devices and small networks
            ws_ping_interval = 3,   # Send ping every X seconds
            ws_ping_timeout = 3,    # Close connection if no pong in X seconds
            lifespan="off",         # Uvicorn server will not wait for or execute startup/shutdown events
        )
    except KeyboardInterrupt:
        # Stop listening to messages
        stop_event.set()
        message_listener.join()

# Restore temporarily disabled pylint duplicate code check
# pylint: enable=duplicate-code
