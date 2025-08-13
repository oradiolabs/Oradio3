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
import os
import re
import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script, safe_put
from mpd_control import get_mpd_control
from wifi_service import get_wifi_networks, get_saved_network

##### GLOBAL constants ####################
from oradio_const import (
    GREEN, NC,
    USB_SYSTEM,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_WEB_SERVICE_TYPE,
    MESSAGE_WEB_SERVICE_PL_WEBRADIO,
    MESSAGE_WEB_SERVICE_PL1_CHANGED,
    MESSAGE_WEB_SERVICE_PL2_CHANGED,
    MESSAGE_WEB_SERVICE_PL3_CHANGED,
    MESSAGE_WEB_SERVICE_PLAYING_SONG,
    MESSAGE_NO_ERROR
)
################## USB #############################

##### LOCAL constants ####################
WIFI_FILE     = "/tmp/Wifi_invoer.json"             # Web file with wifi credentials
PRESETS_FILE  = USB_SYSTEM + "/presets.json"        # Location of presets
EMPTY_PRESETS = {"preset1": "", "preset2": "", "preset3": ""}
INFO_MISSING  = {"serial": "not found", "version": "not found"}
INFO_ERROR    = {"serial": "undefined", "version": "undefined"}
# Locations of system version info
HARDWARE_VERSION_FILE = "/var/log/oradio_hw_version.log"
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# Get the web server app
api_app = FastAPI()

# Get the path for the server to mount/find the web pages and associated resources
web_path = os.path.dirname(os.path.realpath(__file__))

# Mount static files
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Initialize templates with custom filters and globals
templates = Jinja2Templates(directory=web_path+"/templates")

#### FAVICON ####################

@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """ Handle default browser request for /favicon.ico """
    return FileResponse(os.path.dirname(__file__) + '/static/favicon.ico')

#### PLAYLISTS ####################

def _load_presets():
    """
    Load presets from the JSON file specified by PRESETS_FILE
    First checks if the parent directory of the presets file exists and is a directory
    - If not, logs an error and returns empty presets
    Then tries to open and load the JSON data from the file
    - If the file is not found, logs a warning and returns empty presets
    - If there are errors reading or parsing the file, logs an error and returns empty presets
    Returns: A dictionary with the loaded presets, or an empty presets dictionary if errors occur
    """
    presets_path = Path(PRESETS_FILE)

    # Check if the parent directory of the presets file exists and is a directory
    if not presets_path.parent.is_dir():
        oradio_log.error("USB system path '%s' does not exist or is not a directory", presets_path.parent)
        return EMPTY_PRESETS

    try:
        # Attempt to open the presets file and load it as JSON
        with presets_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        # File does not exist; log a warning and return empty presets
        oradio_log.warning("Presets file '%s' not found", presets_path)
    except (json.JSONDecodeError, PermissionError, OSError) as ex:
        # Error reading or parsing the file; log an error and return empty presets
        oradio_log.error("Failed to read '%s'. error: %s", presets_path, ex)

    # On any failure, return the default empty presets
    return EMPTY_PRESETS

def _store_presets(presets):
    """
    Save the provided presets dictionary to the presets.json file in the USB_SYSTEM folder
    presets (dict): A dictionary containing keys 'preset1', 'preset2', 'preset3' with playlist values
     - Creates the USB_SYSTEM directory if it does not exist
     - Logs errors if directory creation or file writing fails
    """
    try:
        # Ensure the USB_SYSTEM directory exists, create if necessary
        Path(USB_SYSTEM).mkdir(parents=True, exist_ok=True)
    except FileExistsError as ex_err:
        oradio_log.error("'%s' does not exist. Presets cannot be saved. error: %s", USB_SYSTEM, ex_err)

    try:
        # Write the presets dictionary to the JSON file with indentation for readability
        with open(PRESETS_FILE, "w", encoding="utf-8") as file:
            json.dump({"preset1": presets['preset1'], "preset2": presets['preset2'], "preset3": presets['preset3']}, file, indent=4)
    except IOError as ex_err:
        oradio_log.error("Failed to write '%s'. error: %s", PRESETS_FILE, ex_err)

# Get mpd functions
mpdcontrol = get_mpd_control()

#### BUTTONS ####################

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
        "presets"     : _load_presets(),
        "directories" : mpdcontrol.get_directories(),
        "playlists"   : mpdcontrol.get_playlists()
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
     - Saves the updated presets back to storage
     - Sends a notification message to the web service queue about the change
     - Handles web radio presets differently by sending a specific state message
     - Logs errors if the preset identifier is invalid
    """
    oradio_log.debug("Save changed preset '%s' to playlist '%s'", changedpreset.preset, changedpreset.playlist)

    message = {"type": MESSAGE_WEB_SERVICE_TYPE, "error": MESSAGE_NO_ERROR}

    # Message state options
    preset_map = {
        "preset1": MESSAGE_WEB_SERVICE_PL1_CHANGED,
        "preset2": MESSAGE_WEB_SERVICE_PL2_CHANGED,
        "preset3": MESSAGE_WEB_SERVICE_PL3_CHANGED
    }

    if changedpreset.preset in preset_map:
        # load presets
        presets = _load_presets()

        # Modify preset
        presets[changedpreset.preset] = changedpreset.playlist
        oradio_log.debug("Preset '%s' playlist changed to '%s'", changedpreset.preset, changedpreset.playlist)

        # Store presets
        _store_presets(presets)

        if mpdcontrol.preset_is_webradio(changedpreset.preset):
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

#### PLAYLISTS ##################

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

    context = {"playlists": mpdcontrol.get_playlists()}
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
        return mpdcontrol.playlist_add(modify.playlist, modify.song)

    if modify.action == 'Remove':
        if modify.song is None:
            oradio_log.debug("Delete playlist: '%s'", modify.playlist)
        else:
            oradio_log.debug("Delete song '%s' from playlist '%s'", modify.song, modify.playlist)
        return mpdcontrol.playlist_remove(modify.playlist, modify.song)

    oradio_log.error("Unexpected action '%s'", modify.action)
    return JSONResponse(status_code=400, content={"message": f"De action '{modify.action}' is ongeldig"})

#### SHARED: BUTTONS AND PLAYLISTS

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
        return mpdcontrol.get_songs(songs.pattern)

    if songs.source == 'search':
        return mpdcontrol.search(songs.pattern)

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
    mpdcontrol.play_song(song.song)

    # Create message
    message = {
        "type": MESSAGE_WEB_SERVICE_TYPE,
        "state": MESSAGE_WEB_SERVICE_PLAYING_SONG,
        "error": MESSAGE_NO_ERROR
    }

    # Put message in queue
    oradio_log.debug("Send web service message: %s", message)
    safe_put(api_app.state.queue, message)

#### STATUS ####################

def _get_hw_info():
    """
    Retrieve hardware configuration information from the hardware version JSON file
    Returns: Contains 'serial' and 'version' keys with hardware info
             If the file is missing or unreadable, returns default placeholders
    """
    oradio_log.debug("Get hardware info")

    # Try to load hardware version info
    try:
        with open(HARDWARE_VERSION_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            hardware_info = {
                "serial": data.get("serial", "missing serial"),
                "version": data.get("hw_detected", "missing hw_detected")
            }
    except FileNotFoundError:
        oradio_log.error("Hardware version info '%s' not found", HARDWARE_VERSION_FILE)
        hardware_info = INFO_MISSING
    except (json.JSONDecodeError, PermissionError, OSError) as ex_err:
        oradio_log.error("Failed to read '%s'. error: %s", HARDWARE_VERSION_FILE, ex_err)
        hardware_info = INFO_ERROR

    # Return sanitized data set
    return hardware_info

def _get_sw_info():
    """
    Retrieve software configuration information from the software version JSON file
    Returns: Contains 'serial' and 'version' keys with software info
             If the file is missing or unreadable, returns default placeholders
    """
    oradio_log.debug("Get hardware info")

    # Try to load software version info
    try:
        with open(SOFTWARE_VERSION_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            software_info = {
                "serial": data.get("serial", "missing serial"),
                "version": data.get("gitinfo", "missing gitinfo")
            }
    except FileNotFoundError:
        oradio_log.error("Hardware version info '%s' not found", SOFTWARE_VERSION_FILE)
        software_info = INFO_MISSING
    except (json.JSONDecodeError, PermissionError, OSError) as ex_err:
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
              - Hardware serial and version
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

    # Get hardware and software configuration info
    hw_info = _get_hw_info()

    # Get software configuration info
    sw_info = _get_sw_info()

    # Return status page and serial and active wifi connection as context
    context = {
                "serial"     : serial,
                "hw_serial"  : hw_info['serial'],
                "hw_version" : hw_info['version'],
                "sw_serial"  : sw_info['serial'],
                "sw_version" : sw_info['version']
            }
    return templates.TemplateResponse(request=request, name="status.html", context=context)

#### NETWORK ####################

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

    # Send message to web service
    message = {
        "type": MESSAGE_WEB_SERVICE_TYPE,
        "ssid": credentials.ssid,
        "pswd": credentials.pswd
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
    if not bool(re.match(pattern, spotify.name)):
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

#### CATCH ALL ####################

@api_app.route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Catch-all route handler for any undefined GET or POST paths
    When a request is made to a path that doesn't match any existing endpoint,
    this handler redirects the client to the playlists page at '/buttons'
    request (Request): The incoming HTTP request object
    Returns:A 302 redirect to the '/buttons' URL
    """
    oradio_log.debug("Catchall triggered for path: %s", request.url.path)

    # Redirect all unknown routes to the playlists page
    return RedirectResponse(url='/buttons', status_code=302)

# Entry point for stand-alone operation
if __name__ == '__main__':

# Most modules use similar code in stand-alone
# pylint: disable=duplicate-code

    # Imports only relevant when stand-alone
    import uvicorn
    from multiprocessing import Process, Event, Queue
    from queue import Empty

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
        uvicorn.run(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_config=None, log_level="trace")
    except KeyboardInterrupt:
        # Stop listening to messages
        stop_event.set()
        message_listener.join()
