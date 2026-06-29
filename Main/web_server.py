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
@copyright:     Copyright, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       3
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Web interface and FastAPI web server for Oradio.
    Serves the Oradio3 single-page application via the /oradio3 route,
    exposes a generic /execute command endpoint, manages a keep-alive
    timer that shuts the server down when the browser stops pinging,
    and redirects all unmatched paths back to /oradio3.
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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

#### Oradio modules #######################################
from log_service import oradio_log
from utilities import get_serial, run_shell_script, load_presets, store_presets
from wifi_service import get_wifi_networks, get_saved_network
from mpd_control import MPDControl
from messaging import (
    Commands,
    safe_put,
    CommandMessage,
    WEB_SOURCE,
    WEB_PL1_PLAYLIST,
    WEB_PL2_PLAYLIST,
    WEB_PL3_PLAYLIST,
    WEB_PL1_WEBRADIO,
    WEB_PL2_WEBRADIO,
    WEB_PL3_WEBRADIO,
    WEB_PLAYING_SONG,
)

#### GLOBAL constants #####################################
from constants import (
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    ACCESS_POINT_HOST,
    REQUEST_CONNECT,
    REQUEST_STOP,
)

#### LOCAL constants ######################################
# Fallback values returned by _get_sw_info() when the version file is absent or unreadable
INFO_MISSING = {"serial": "not found", "version": "not found"}
INFO_ERROR   = {"serial": "undefined", "version": "undefined"}

# Location of the JSON file written by the build/deploy process
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# Seconds of inactivity before the keep-alive timer fires and stops the server.
# The browser pings every 2 s, so missing 2 consecutive pings triggers shutdown.
KEEP_ALIVE_TIMEOUT = 5

# Full URL required by some mobile browsers (e.g. iOS Safari) that reject bare
# hostnames in redirect responses.
oradioap_url = f"http://{ACCESS_POINT_HOST}"

# Initialise MPD client
mpd_control = MPDControl()

# FastAPI application instance shared by all route handlers
api_app = FastAPI()

# Derive the path to web assets relative to this source file's location
web_path = path.dirname(path.dirname(path.realpath(__file__))) + "/webapp"

# Serve CSS, JS, and image assets from /static
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Jinja2 template engine pointed at the templates directory
templates = Jinja2Templates(directory=web_path+"/templates")

# Per-request-cycle timer state stored on the app so it persists across requests.
# timer_started acts as a gate: keep_alive_middleware only manages the timer
# after the first /keep_alive ping has set it to True.
api_app.state.timer_task     = None   # The active asyncio task, or None if no timer is running
api_app.state.timer_deadline = None   # UTC datetime of the next expiry, or None if timer has not been armed
api_app.state.timer_started  = False  # Becomes True after the first /keep_alive ping

@api_app.middleware("http")
async def keep_alive_middleware(request: Request, call_next):
    """
    Pause and restart the keep-alive timer around every non-ping request.

    The keep-alive timer is armed by the first /keep_alive ping from the
    browser (timer_started = True). For all subsequent non-ping requests,
    this middleware cancels the running timer before passing the request to
    the handler, then either creates a new timer task or relies on the
    existing one to pick up the refreshed deadline once the response is ready.
    This prevents the server from timing out while actively serving a request.

    /keep_alive requests are passed through without touching the timer, as
    the /keep_alive endpoint manages the deadline itself.

    Args:
        request:   The incoming HTTP request.
        call_next: ASGI callable that forwards the request to the route handler.

    Returns:
        The HTTP response produced by the route handler.
    """
    # Pause the timer for any request other than /keep_alive, but only after
    # the timer has been armed by the first ping.
    if request.url.path != "/keep_alive" and api_app.state.timer_started:
        task = getattr(api_app.state, "timer_task", None)
        if task and not task.done():
            task.cancel()
            api_app.state.timer_task = None
            api_app.state.timer_deadline = None
            oradio_log.debug("Keep-alive timer stopped for request %s", request.url.path)

    # Process the request
    response = await call_next(request)

    # Restart the timer after the response is ready, again only for non-ping
    # requests once the timer has been armed.
    if request.url.path != "/keep_alive" and api_app.state.timer_started:
        api_app.state.timer_deadline = datetime.now(timezone.utc) + timedelta(seconds=KEEP_ALIVE_TIMEOUT)
        # If a task is already running, it will pick up the new deadline on its
        # next poll iteration; only create a new task when none is running.
        if not getattr(api_app.state, "timer_task", None) or api_app.state.timer_task.done():
            api_app.state.timer_task = create_task(stop_task())
        oradio_log.debug("Keep-alive timer restarted for request %s", request.url.path)

    return response

##### Helpers #############################################

def _get_sw_info() -> dict:
    """
    Read software version metadata from the version file.

    Parses the JSON file at SOFTWARE_VERSION_FILE and extracts the
    serial and gitinfo fields.

    Returns:
        dict with keys "serial" (str) and "version" (str) on success.
        Falls back to INFO_MISSING if the file does not exist, or
        INFO_ERROR if the file is present but unreadable or invalid.
    """
    oradio_log.debug("Get software info")

    try:
        with open(SOFTWARE_VERSION_FILE, "r", encoding="utf-8") as file:
            data = load(file)
            software_info = {
                "serial" : data.get("serial",  "missing serial"),
                "version": data.get("gitinfo", "missing gitinfo"),
            }
    except FileNotFoundError:
        oradio_log.error("Software version info '%s' not found", SOFTWARE_VERSION_FILE)
        software_info = INFO_MISSING
    except (JSONDecodeError, PermissionError, OSError) as ex_err:
        oradio_log.error("Failed to read '%s'. error: %s", SOFTWARE_VERSION_FILE, ex_err)
        software_info = INFO_ERROR

    return software_info

def play_song(args: Optional[Dict[str, Any]]):
    """
    Play a song via MPD and publish a WEB_PLAYING_SONG command.

    Args:
        args: Dict containing "song" (str) — path or identifier of the
              song to play.

    Returns:
        {"message": str} confirming the song that was started.

    Raises:
        ValueError: If args is None or does not contain "song".
    """
    songfile = args.get("song") if args else None
    if not songfile:
        raise ValueError("'play' vereist argument 'song'")

    mpd_control.play_song(songfile)

    oradio_log.debug("Send web service message: %s", WEB_PLAYING_SONG)
    Commands.publish(CommandMessage(WEB_SOURCE, WEB_PLAYING_SONG))

    return {"message": f"'{songfile}' is nu te horen"}

def get_networks(_args: Optional[Dict[str, Any]]):
    """
    Return the list of currently visible WiFi networks.

    Args:
        _args: Unused; accepted to match the command handler signature.

    Returns:
        A list of {"ssid": str, "type": "open" | "closed"} dicts as
        returned by get_wifi_networks().
    """
    return get_wifi_networks()

def shutdown_webapp(_args: Optional[Dict[str, Any]]):
    """
    Request a graceful web server shutdown via the service queue.

    Places a REQUEST_STOP message on api_app.state.queue so
    the owning process can stop uvicorn cleanly.

    Args:
        _args: Unused; accepted to match the command handler signature.
    """
    safe_put(api_app.state.queue, {"request": REQUEST_STOP})

def rename_spotify(args: Optional[Dict[str, Any]]):
    """
    Rename the Spotify (librespot) device and restart the service.

    Validates the new name against the allowed character set, then updates
    the librespot.service unit file via sed, reloads the systemd
    daemon, and restarts the service.

    Args:
        args: Dict containing "name" (str) — the new Spotify device name.
              Allowed characters: letters, digits, hyphen (-), underscore (_).

    Returns:
        The new device name string on success, or a JSONResponse with
        status 400 if validation fails or any shell command errors.

    Raises:
        ValueError: If args is None or does not contain "name".
    """
    name = args.get("name") if args else None
    if not name:
        raise ValueError("'spotify' vereist argument 'name'")

    # Validate that the name contains only safe characters before passing it to sed.
    pattern = r'^[A-Za-z0-9_-]+$'
    if not bool(match(pattern, name)):
        response = f"'{name}' is ongeldig. Alleen hoofdletters, kleine letters, cijfers, - of _ is toegestaan"
        oradio_log.error(response)
        return JSONResponse(status_code=400, content={"message": response})

    # Replace the --name argument in the librespot service unit file.
    cmd = f"sudo sed -i 's/--name \\S*/--name {name}/' /etc/systemd/system/librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        return JSONResponse(status_code=400, content={"message": response})

    # Reload systemd so it picks up the modified unit file.
    cmd = "sudo systemctl daemon-reload"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        return JSONResponse(status_code=400, content={"message": response})

    # Restart librespot to apply the new device name immediately.
    cmd = "sudo systemctl restart librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        return JSONResponse(status_code=400, content={"message": response})

    return name

def wifi_connect(args: Optional[Dict[str, Any]]):
    """
    Send a WiFi connection request to the service queue.

    Places a REQUEST_CONNECT message containing the SSID and
    optional password on api_app.state.queue for the owning process to act on.

    Args:
        args: Dict containing:
            "ssid" (str, required) — target network name.
            "pswd" (str, optional) — network password; pass an empty
            string or omit for open networks.

    Raises:
        ValueError: If args is None or does not contain "ssid".
    """
    ssid = args.get("ssid") if args else None
    if not ssid:
        raise ValueError("'connect' vereist argument 'ssid'")

    pswd = args.get("pswd") if args else None

    safe_put(api_app.state.queue, {
        "request": REQUEST_CONNECT,
        "ssid"   : ssid,
        "pswd"   : pswd,
    })

def save_preset(args: Optional[Dict[str, Any]]):
    """
    Save a playlist or webradio entry as a preset and publish the change.

    Persists the updated preset mapping and sends the appropriate
    WEB_PL*_PLAYLIST or WEB_PL*_WEBRADIO command so other modules
    are notified of the change.

    Args:
        args: Dict containing:
            "preset" (str, required) — preset key: "preset1",
            "preset2", or "preset3".
            "playlist" (str, required) — playlist or webradio identifier
            to assign to the preset.

    Raises:
        ValueError: If args is None, either required key is missing, or
            the preset key is not one of the three recognised values.
    """
    preset = args.get("preset") if args else None
    if not preset:
        raise ValueError("'preset' vereist argument 'preset'")

    playlist = args.get("playlist") if args else None
    if not playlist:
        raise ValueError("'preset' vereist argument 'playlist'")

    # Map preset keys to their per-type messaging constants.
    preset_map = {
        "preset1": {"playlist": WEB_PL1_PLAYLIST, "webradio": WEB_PL1_WEBRADIO},
        "preset2": {"playlist": WEB_PL2_PLAYLIST, "webradio": WEB_PL2_WEBRADIO},
        "preset3": {"playlist": WEB_PL3_PLAYLIST, "webradio": WEB_PL3_WEBRADIO},
    }

    if preset not in preset_map:
        oradio_log.error("Unexpected preset '%s'", preset)
        raise ValueError(f"De preset '{preset}' is ongeldig")

    # Determine whether this is a webradio or local playlist entry.
    preset_type = "webradio" if mpd_control.is_webradio(mpdlist=playlist) else "playlist"

    presets = load_presets()
    presets[preset] = playlist
    oradio_log.debug("Preset '%s' playlist changed to '%s'", preset, playlist)
    store_presets(presets)

    oradio_log.debug("Send web service message: %s", preset_map[preset][preset_type])
    Commands.publish(CommandMessage(WEB_SOURCE, preset_map[preset][preset_type]))

def get_playlist_songs(args: Optional[Dict[str, Any]]):
    """
    Return all songs contained in a given playlist.

    Args:
        args: Dict containing "playlist" (str) — playlist name.

    Returns:
        The list of songs returned by MPDControl.get_songs().

    Raises:
        ValueError: If args is None or does not contain "playlist".
    """
    playlist = args.get("playlist") if args else None
    if not playlist:
        raise ValueError("'playlist' vereist argument 'playlist'")

    return mpd_control.get_songs(playlist)

def get_search_songs(args: Optional[Dict[str, Any]]):
    """
    Return songs matching a search pattern.

    Args:
        args: Dict containing "pattern" (str) — search string.

    Returns:
        The list of matching songs returned by MPDControl.search().

    Raises:
        ValueError: If args is None or does not contain "pattern".
    """
    pattern = args.get("pattern") if args else None
    if not pattern:
        raise ValueError("'search' vereist argument 'pattern'")

    return mpd_control.search(pattern)

def modify_playlist(args: Optional[Dict[str, Any]]):
    """
    Add or remove a song or playlist via MPD and return the updated playlist list.

    Args:
        args: Dict containing:
            "action" (str, required) — "Add" or "Remove".
            "playlist" (str, required) — target playlist name.
            "song" (str, optional) — song to add or remove; omit to
            operate on the playlist itself.

    Returns:
        The updated list of custom playlists on success.

    Raises:
        ValueError: If args is None, a required key is missing, or
            action is not "Add" or "Remove".
    """
    action = args.get("action") if args else None
    if not action:
        raise ValueError("'modify' vereist argument 'action'")

    playlist = args.get("playlist") if args else None
    if not playlist:
        raise ValueError("'modify' vereist argument 'playlist'")

    song = args.get("song") if args else None

    # Map action strings to the MPD method and appropriate log message templates.
    action_map = {
        "Add"   : (mpd_control.add,    "Add playlist: '%s'",         "Add song '%s' to playlist '%s'"),
        "Remove": (mpd_control.remove, "Remove playlist: '%s'", "Remove song '%s' from playlist '%s'"),
    }

    if action not in action_map:
        oradio_log.error("Unexpected action '%s'", action)
        raise ValueError(f"De action '{action}' is ongeldig")

    func, msg_no_song, msg_with_song = action_map[action]
    if song is None:
        oradio_log.debug(msg_no_song, playlist)
    else:
        oradio_log.debug(msg_with_song, song, playlist)
    func(playlist, song)

    return mpd_control.get_playlists()

def log_message(args: Optional[Dict[str, Any]]):
    """
    Log a message originating from the web interface.

    Allows the browser-side JavaScript to write entries into the server-side
    log for debugging purposes.

    Args:
        args: Dict containing "message" (str) — the text to log.

    Raises:
        ValueError: If args is None or does not contain "message".
    """
    message = args.get("message") if args else None
    if not message:
        raise ValueError("'log_message' vereist argument 'message'")

    oradio_log.debug("Web interface: %s", message)

##### Execute #############################################

class ExecuteRequest(BaseModel):
    """
    Request body model for the /execute endpoint.

    Attributes:
        cmd:  Name of the command to execute.
        args: Optional dict of command-specific arguments.
    """
    cmd:  str
    args: Optional[Dict[str, Any]] = None

@api_app.post("/execute")
async def execute(request: ExecuteRequest):
    """
    Dispatch a command from the web interface to the appropriate handler.

    Looks up request.cmd in the command dispatch table and calls the
    associated function with request.args.

    Args:
        request: Parsed ExecuteRequest body from the POST payload.

    Returns:
        The handler's return value on success (type varies by command), or a
        JSONResponse with status 400 if the command name is unknown or a
        required argument is missing.
    """
    oradio_log.debug("Executing '%s' with args '%s'", request.cmd, request.args)

    commands = {
        "play"       : play_song,
        "networks"   : get_networks,
        "shutdown"   : shutdown_webapp,
        "spotify"    : rename_spotify,
        "connect"    : wifi_connect,
        "preset"     : save_preset,
        "playlist"   : get_playlist_songs,
        "search"     : get_search_songs,
        "modify"     : modify_playlist,
        "log_message": log_message,
        # Add other commands here
    }

    if request.cmd not in commands:
        oradio_log.error("Invalid command '%s'", request.cmd)
        return JSONResponse(status_code=400, content={"message": f"Opdracht '{request.cmd}' is onbekend"})

    try:
        result = commands[request.cmd](request.args)
        return result
    except ValueError as ex_err:
        return JSONResponse(status_code=400, content={"message": str(ex_err)})

##### Oradio3 #############################################

@api_app.get("/oradio3")
async def oradio3_page(request: Request):
    """
    Render and serve the Oradio3 web interface page.

    Assembles the full template context by gathering the last connected
    WiFi network, the current Spotify device name, saved presets, available
    MPD directories and playlists, and software version information.

    Args:
        request: The incoming HTTP request (passed through to the template engine).

    Returns:
        A TemplateResponse rendering oradio3.html with the assembled
        context, or a JSONResponse with status 400 if reading the Spotify
        device name fails.
    """
    oradio_log.debug("Serving Oradio3 page")

    # Last WiFi network connected before the access point was started (empty string if none).
    oldssid = get_saved_network()

    # Read the current Spotify device name from the running service unit.
    oradio_log.debug("Get Spotify name")
    cmd = "systemctl show librespot | sed -n 's/.*--name \\([^ ]*\\).*/\\1/p' | uniq"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get Spotify name, error: %s", cmd, response)
        return JSONResponse(status_code=400, content={"message": response})
    spotify = response

    serial  = get_serial()
    sw_info = _get_sw_info()

    context = {
        "oldssid"    : oldssid,
        "spotify"    : spotify,
        "presets"    : load_presets(),
        "directories": mpd_control.get_directories(),
        "playlists"  : mpd_control.get_playlists(),
        "serial"     : serial,
        "sw_serial"  : sw_info["serial"],
        "sw_version" : sw_info["version"],
    }

    return templates.TemplateResponse(request=request, name="oradio3.html", context=context)

##### Keep Alive ##########################################

async def stop_task():
    """
    Background asyncio task that fires when the keep-alive deadline passes.

    Polls the deadline in up to 200 ms increments so cancellation is
    responsive; the final sleep before expiry may be shorter than 200 ms
    if the remaining time is less than that. Once the deadline is reached,
    places a REQUEST_STOP message on the service queue to trigger
    a graceful shutdown.

    If the task is cancelled (because a new /keep_alive ping reset the
    deadline) it exits silently without sending the stop message.
    """
    try:
        while True:
            remaining = (api_app.state.timer_deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            # Sleep in up to 200 ms increments so cancellation is picked up quickly.
            await sleep(min(remaining, 0.2))

        safe_put(api_app.state.queue, {"request": REQUEST_STOP})
        oradio_log.debug("Keep alive timer expired: closing the web server")

    except CancelledError:
        # Task was cancelled because the keep-alive deadline was reset; exit cleanly.
        pass

@api_app.post("/keep_alive")
async def keep_alive():
    """
    Reset the inactivity timer; arm it on the first call.

    The first ping arms the timer (sets timer_started = True) so that
    keep_alive_middleware begins managing it for subsequent requests.
    Every ping then refreshes the deadline and ensures a stop_task
    coroutine is running.

    Returns:
        JSONResponse({"status": "ok"}) always.
    """
    now = datetime.now(timezone.utc)

    # Arm the timer on the first ping.
    if not api_app.state.timer_started:
        api_app.state.timer_started = True
        oradio_log.debug("Keep-alive timer started on first ping")

    if api_app.state.timer_deadline:
        remaining = (api_app.state.timer_deadline - now).total_seconds()
        oradio_log.debug("Keep-alive timer reset, %f seconds remaining", remaining)

    # Advance the deadline.
    api_app.state.timer_deadline = now + timedelta(seconds=KEEP_ALIVE_TIMEOUT)

    # Start a new stop_task only if none is currently running.
    if not api_app.state.timer_task or api_app.state.timer_task.done():
        api_app.state.timer_task = create_task(stop_task())

    return JSONResponse({"status": "ok"})

##### Catch all ###########################################

@api_app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Handle any request that did not match a defined route.

    The /static/ mount intercepts static asset requests before they reach
    this handler, so all paths arriving here are redirected to /oradio3
    via the fully-qualified access-point URL.

    Args:
        request: The incoming HTTP request.

    Returns:
        A 302 RedirectResponse to {oradioap_url}/oradio3.
    """
    oradio_log.debug("Catchall triggered for path: %s", request.url.path)

    return RedirectResponse(url=oradioap_url + "/oradio3", status_code=302)

##### Stand-alone entry point #############################

if __name__ == '__main__':

    import uvicorn
    from threading import Thread
    from multiprocessing import Queue
    from messaging import safe_get, DebugMessageHandler     # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    def _check_requests(queue):
        """
        Monitor the service request queue and print received messages.

        Runs in a background process. Loops until the bare
        REQUEST_STOP string sentinel is received.

        Args:
            queue: multiprocessing.Queue to drain.
        """
        while True:
            request = safe_get(queue)
            print(f"\nRequest received: '{request}'\n")

            if request == REQUEST_STOP:
                return

    print("\nStarting test program...\n")

    # The module-level value includes the AP host address, which only resolves
    # on-device; use a relative URL for stand-alone testing.
    oradioap_url = ""

    # Subscribe to command topics so messages published are printed to console.
    cmd_handler = DebugMessageHandler(Commands.subscribe())

    request_queue = Queue()

    # Spawn the message monitor before starting uvicorn so no messages are missed.
    message_listener = Thread(target=_check_requests, args=(request_queue,))
    message_listener.start()

    # timer_started is already False from the module-level initialisation above.
    api_app.state.queue = request_queue

    try:
        uvicorn.run(
            api_app,
            host=WEB_SERVER_HOST,
            port=WEB_SERVER_PORT,
            log_config=None,        # Prevent uvicorn overriding our log setup
            log_level="debug",      # trace | debug | info | warning | error | critical
            ws_ping_interval=3,     # Send WebSocket ping every 3 seconds
            ws_ping_timeout=3,      # Close connection if no pong within 3 seconds
            lifespan="off",         # Do not wait for startup/shutdown lifecycle events
        )
    except KeyboardInterrupt:
        pass
    finally:
        # Signal _check_requests to exit by sending the bare string constant as a
        # sentinel. Normal operation puts dicts; only this path puts the raw string.
        safe_put(request_queue, REQUEST_STOP)
        message_listener.join()
        Commands.unsubscribe(cmd_handler.get_queue())
        cmd_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
