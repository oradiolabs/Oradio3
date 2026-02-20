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
    Note:
    Install:
    Documentation:
        https://fastapi.tiangolo.com/
"""
from os import path
from re import match
from typing import Optional, Dict, Any

from json import load, JSONDecodeError
from asyncio import sleep, create_task, CancelledError
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from fastapi import FastAPI, Request

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse

from starlette.responses import RedirectResponse




#### oradio modules ######################
from oradio_logging import oradio_log
from oradio_utils import get_serial, safe_put, run_shell_script, load_presets, store_presets
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
    MESSAGE_WEB_SERVICE_PL1_PLAYLIST,
    MESSAGE_WEB_SERVICE_PL2_PLAYLIST,
    MESSAGE_WEB_SERVICE_PL3_PLAYLIST,
    MESSAGE_WEB_SERVICE_PL1_WEBRADIO,
    MESSAGE_WEB_SERVICE_PL2_WEBRADIO,
    MESSAGE_WEB_SERVICE_PL3_WEBRADIO,
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
# Stop server if no keep alive message received, in seconds
KEEP_ALIVE_TIMEOUT = 5

# Initialise MPD client
mpd_control = MPDControl()

# Get the web server app
api_app = FastAPI()

# Get the path for the server to mount/find the web pages and associated resources
web_path = path.dirname(path.dirname(path.realpath(__file__))) + "/webapp"

# Mount static files
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Initialize templates with custom filters and globals
templates = Jinja2Templates(directory=web_path+"/templates")

# Store in api_app.state to persists over multiple HTTP requests and application lifetime
api_app.state.timer_task = None         # The actual timer task
api_app.state.timer_deadline = None     # When the timer should expire

# Catch any request before doing anything else
@api_app.middleware("http")
async def keep_alive_middleware(request: Request, call_next):
    """Manage keep_alive counter while executing requests."""
    if request.url.path != "/keep_alive" and api_app.state.timer_task and not api_app.state.timer_task.done():
        # Stop the running keep-alive timer
        api_app.state.timer_task.cancel()
        api_app.state.timer_deadline = None
        api_app.state.timer_task = None
        oradio_log.debug("Keep-alive timer stopped")

    # Process the actual request
    response = await call_next(request)

    if request.url.path != "/keep_alive" and not request.query_params.get("redirected"):
        # Restart timeout counter if not redirected
        await keep_alive()

    # Return response for actual request
    return response

#### FAVICON #############################

@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """ Handle default browser request for /favicon.ico """
    return FileResponse(web_path + "/static/favicon.ico")

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

    # Return playlist page, directories and playlists as context
    context = {
        "directories" : mpd_control.get_directories(),
        "playlists"   : mpd_control.get_playlists()
    }
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

#### EXECUTE #############################

class ExecuteRequest(BaseModel):
    """
    Generic command request:
    - cmd: command name
    - args: dictionary of arguments (can have 0 or more entries)
    """
    cmd:  str
    args: Optional[Dict[str, Any]] = None

# generic POST endpoint
@api_app.post("/execute")
async def execute(request: ExecuteRequest):
    """
    Execute the provided command using relevant arguments
    """
    oradio_log.debug("Executing '%s' with args '%s'", request.cmd, request.args)

    # --- Helper functions ---
    def play_song(args: Optional[Dict[str, Any]]):
        """Play a song via MPD"""
        # Extract required argument, none if no args sent
        songfile = args.get("song") if args else None
        if not songfile:
            # Missing required argument
            raise ValueError("'play' vereist argument 'song'")

        # Trigger MPD control to play the song
        mpd_control.play_song(songfile)

        # Send notification message
        message = {
            "source": MESSAGE_WEB_SERVICE_SOURCE,
            "state": MESSAGE_WEB_SERVICE_PLAYING_SONG,
            "error": MESSAGE_NO_ERROR
        }
        oradio_log.debug("Send web service message: %s", message)
        safe_put(api_app.state.queue, message)

        # Success
        return {"message": f"'{songfile}' is nu te horen"}

    def get_networks(args: Optional[Dict[str, Any]]):
        """Return available WiFi networks"""
        return get_wifi_networks()

    def shutdown_webapp(args: Optional[Dict[str, Any]]):
        """Shutdown the web server"""
        # Send a stop message to the service queue
        message = {"request": MESSAGE_REQUEST_STOP}
        safe_put(api_app.state.queue, message)

    def rename_spotify(args: Optional[Dict[str, Any]]):
        """Modify Spotify device name"""
        # Extract required argument, none if no args sent
        name = args.get("name") if args else None
        if not name:
            # Missing required argument
            raise ValueError("'spotify' vereist argument 'name'")

        # Use regex pattern to validate Spotify device name characters
        pattern = r'^[A-Za-z0-9_-]+$'
        if not bool(match(pattern, name)):
            response = f"'{name}' is ongeldig. Alleen hoofdletters, kleine letters, cijfers, - of _ is toegestaan"
            oradio_log.error(response)
            # Return fail, so caller can try to recover
            return JSONResponse(status_code=400, content={"message": response})

        # Update the librespot.service file with the new device name
        cmd = f"sudo sed -i 's/--name \\S*/--name {name}/' /etc/systemd/system/librespot.service"
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
        return name

    def wifi_connect(args: Optional[Dict[str, Any]]):
        """Connect to wifi network"""
        # Extract required arguments, none if no args sent
        ssid = args.get("ssid") if args else None
        if not ssid:
            # Missing required argument
            raise ValueError("'connect' vereist argument 'ssid'")
        pswd = args.get("pswd") if args else None

        # Send connect message to web service
        message = {
            "request": MESSAGE_REQUEST_CONNECT,
            "ssid"  : ssid,
            "pswd"  : pswd
        }
        safe_put(api_app.state.queue, message)

    def save_preset(args: Optional[Dict[str, Any]]):
        """Save preset playlist"""
        # Extract required arguments, none if no args sent
        preset = args.get("preset") if args else None
        if not preset:
            # Missing required argument
            raise ValueError("'preset' vereist argument 'preset'")
        playlist = args.get("playlist") if args else None
        if not playlist:
            # Missing required argument
            raise ValueError("'preset' vereist argument 'playlist'")

        message = {"source": MESSAGE_WEB_SERVICE_SOURCE, "error": MESSAGE_NO_ERROR}

        # Mapping of presets to constants per type
        preset_map = {
            "preset1": {
                "playlist": MESSAGE_WEB_SERVICE_PL1_PLAYLIST,
                "webradio": MESSAGE_WEB_SERVICE_PL1_WEBRADIO,
            },
            "preset2": {
                "playlist": MESSAGE_WEB_SERVICE_PL2_PLAYLIST,
                "webradio": MESSAGE_WEB_SERVICE_PL2_WEBRADIO,
            },
            "preset3": {
                "playlist": MESSAGE_WEB_SERVICE_PL3_PLAYLIST,
                "webradio": MESSAGE_WEB_SERVICE_PL3_WEBRADIO,
            }
        }

        # Determine type
        preset_type = "webradio" if mpd_control.is_webradio(mpdlist=playlist) else "playlist"

        # Set message state
        if preset in preset_map:
            # load presets
            presets = load_presets()

            # Modify preset
            presets[preset] = playlist
            oradio_log.debug("Preset '%s' playlist changed to '%s'", preset, playlist)

            # Store presets
            store_presets(presets)

            # Send message which preset has changed and its type
            message["state"] = preset_map[preset][preset_type]
            oradio_log.debug("Send web service message: %s", message)
            safe_put(api_app.state.queue, message)
        else:
            oradio_log.error("Invalid preset '%s'", preset)



    # --- Command dispatch dictionary ---
    commands = {
        "play": play_song,
        "networks": get_networks,
        "shutdown": shutdown_webapp,
        "spotify": rename_spotify,
        "connect": wifi_connect,
        "preset": save_preset,
        # Add other commands were
    }

    # --- Check command validity ---
    if request.cmd not in commands:
        oradio_log.error("Invalid command '%s'", request.cmd)
        return JSONResponse(status_code=400, content={"message": f"Opdracht '{request.cmd}' is onbekend"})

    # --- Execute command ---
    try:
        result = commands[request.cmd](request.args)
        return result
    except ValueError as ve:
        # Argument ontbreekt of fout
        return JSONResponse(status_code=400, content={"message": str(ve)})
    except Exception as e:
        # Andere fouten (MPD, server etc.)
        oradio_log.error("Fout bij uitvoeren van '%s': %s", request.cmd, str(e))
        return JSONResponse(status_code=500, content={"message": str(e)})

#### BUTTONS #############################

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

#### ORADIO3 ##############################

@api_app.get("/oradio3")
async def oradio3_page(request: Request):
    """
    Serve the Oradio3 web interface page, with:
    - network: 
    - buttons: 
    - playlists: 
    - status: hardware and software information
    Returns: Status page populated with
              - Oradio serial number from hardware command
              - Software serial and version
    """
    oradio_log.debug("Serving Oradio3 page")

    # --- Network page info ---

    # Get the network Oradio was connected to before starting access point, empty string if None
    oldssid = get_saved_network()

    # Get Spotify name
    oradio_log.debug("Get Spotify name")
    cmd = "systemctl show librespot | sed -n 's/.*--name \\([^ ]*\\).*/\\1/p' | uniq"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})
    spotify = response

    # --- Status page info ---

    # Get RPI serial number
    serial = get_serial()

    # Get software configuration info
    sw_info = _get_sw_info()

    context = {
        # Return saved wifi connection and spotify name as context
        "oldssid"     : oldssid,
        "spotify"     : spotify,
        # Return presets, directories and playlists as context
        "presets"     : load_presets(),
        "directories" : mpd_control.get_directories(),
        "playlists"   : mpd_control.get_playlists(),
        # Return serial and active wifi connection as context
        "serial"      : serial,
        "sw_serial"   : sw_info['serial'],
        "sw_version"  : sw_info['version'],
    }

    return templates.TemplateResponse(request=request, name="oradio3.html", context=context)

#### KEEP ALIVE ######################

async def stop_task():
    """The wait task sending the stop message when timer expires."""
    try:
        # Sleep until timeout unless reset
        while True:
            # Compute remaining time until deadline
            remaining = (api_app.state.timer_deadline - datetime.now(timezone.utc)).total_seconds()

            # If deadline passed, break the loop
            if remaining <= 0:
                break

            # Sleep a short time (or until deadline, whichever is smaller)
            await sleep(min(remaining, 0.2))

        # Timer expired: Send stop message
        message = {"request": MESSAGE_REQUEST_STOP}
        safe_put(api_app.state.queue, message)
        oradio_log.debug("Keep alive timer expired: closing the web server")

    except CancelledError:
        # Timer cancelled (because a new keep alive request arrived)
        pass

# POST endpoint to reset the keep alive timer
@api_app.post("/keep_alive")
async def keep_alive():
    """Handle POST request to (re)set the inactive timer for closing the web server."""
    if api_app.state.timer_deadline is None:
        oradio_log.debug("Starting the keep alive timer")
    else:
        remaining = (api_app.state.timer_deadline - datetime.now(timezone.utc)).total_seconds()
        oradio_log.debug("Time remaining: %f. Resetting the keep alive timer", remaining)

    # Set the new deadline
    api_app.state.timer_deadline = datetime.now(timezone.utc) + timedelta(seconds=KEEP_ALIVE_TIMEOUT)

    # Only create the timer task if it doesn't exist or is done
    if api_app.state.timer_task is None or api_app.state.timer_task.done():
        api_app.state.timer_task = create_task(stop_task())

#### CATCH ALL ###########################

@api_app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Catch-all endpoint to handle undefined routes.
    - Ignore /static/ requests
    - Redirects the client to '/oradio3'.

    Args:
        request (Request): The incoming HTTP request.

    Returns:
        RedirectResponse: Redirects the client to '/buttons'.
    """
    oradio_log.debug("Catchall triggered for path: %s", request.url.path)

    # Do not intercept /static/ requests
    if request.url.path.startswith("/static/"):
        return FileResponse(web_path + request.url.path)

    # Redirect all requests to webapp
#REVIEW Onno: Do I need to use redirected=1? If yes, document why
    return RedirectResponse(url="/oradio3?redirected=1", status_code=302)

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
            log_level="debug",      # trace | debug | info | warning | errror | critical
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
