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
    exposes a generic /execute command endpoint, runs a watchdog that
    shuts the portal down once nobody is using it any more (see the
    "Portal watchdog" section), and redirects all unmatched paths back
    to /oradio3.
    References:
        https://fastapi.tiangolo.com/
"""
import subprocess
from os import path
from time import monotonic
from typing import Any
from json import load, JSONDecodeError
from threading import Lock
from multiprocessing import Queue
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

#### Oradio modules #######################################
from log_service import oradio_log
from utilities import get_serial, load_presets, store_presets, mask_secrets, ThreadTemplate
from wifi_service import get_wifi_networks, get_saved_network
from wifi_listener import WIFI_INTERFACE
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
INFO_MISSING = {"dtstamp": "not found", "version": "not found"}
INFO_ERROR   = {"dtstamp": "undefined", "version": "undefined"}

# Location of the JSON file written by the build/deploy process
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# Portal watchdog timeouts, in seconds. See the "Portal watchdog" section for
# how they combine. All of them are measured on the monotonic clock, so an NTP
# step of the wall clock (the Pi has no RTC) can neither fire nor stall them.
#
# No page has pinged yet: the user still has to pick the Oradio network on the
# phone and wait for the portal to pop up. Also the upper bound for a portal
# nobody ever opens.
FIRST_CONTACT_TIMEOUT = 600
# The page stopped pinging while a phone is still associated with the access
# point: the user switched to another app or locked the screen (the page only
# pings while it is visible). Long enough to look something up and come back;
# short enough that a forgotten portal does not linger.
KEEP_ALIVE_TIMEOUT = 180
# No client is associated with the access point any more, after one has been:
# the user left the Oradio network, so nobody can come back to the page.
NO_STATION_TIMEOUT = 30
# How often the watchdog evaluates the rules above.
WATCHDOG_INTERVAL = 2
# Upper bound for one iw call, kept well inside ThreadTemplate's
# JOIN_TIMEOUT (5 s): one check runs iw twice, so stopping the watchdog never
# waits on a stuck iw.
STATION_CHECK_TIMEOUT = 2

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

##### Helpers #############################################

def _get_sw_info() -> dict:
    """
    Read software version metadata from the version file.

    Parses the JSON file at SOFTWARE_VERSION_FILE and extracts
    the timestamp and gitinfo fields.

    Returns:
        dict with keys "dtstamp" (str) and "version" (str) on success.
        Falls back to INFO_MISSING if the file does not exist, or
        INFO_ERROR if the file is present but unreadable or invalid.
    """
    oradio_log.debug("Get software info")

    try:
        with open(SOFTWARE_VERSION_FILE, encoding="utf-8") as file:
            data = load(file)
            software_info = {
                "dtstamp": data.get("dtstamp", "missing dtstamp"),
                "version": data.get("gitinfo", "missing gitinfo"),
            }
    except FileNotFoundError:
        oradio_log.error("Software version info '%s' not found", SOFTWARE_VERSION_FILE)
        software_info = INFO_MISSING
    except (JSONDecodeError, PermissionError, OSError) as ex_err:
        oradio_log.error("Failed to read '%s'. error: %s", SOFTWARE_VERSION_FILE, ex_err)
        software_info = INFO_ERROR

    return software_info

def play_song(args: dict[str, Any] | None):
    """
    Play a song via MPD and publish a WEB_PLAYING_SONG command.

    Args:
        args: dict containing "song" (str) — path or identifier of the
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

def get_networks(_args: dict[str, Any] | None):
    """
    Return the list of WiFi networks accumulated by the WiFi event listener.

    get_wifi_networks() separates "no networks" from "no list": [] means the neighbourhood really is empty, None
    means there is no maintained list -- the WiFi event listener is not running, or its NetworkManager subscription
    is down -- and there is nothing to report. Returning None straight through
    would encode as JSON null with a 200, which the page cannot iterate and which reads to the user as an empty
    neighbourhood -- the one reading that invites them to retry forever. 503 says the subsystem is down, which is
    what actually happened and what the page should say.

    Reaching this at all means the listener died, or lost NetworkManager, while the access point it reports on is
    still serving this very request, so it is logged as an error rather than passed over quietly.

    Args:
        _args: Unused; accepted to match the command handler signature.

    Returns:
        A list of {"ssid": str, "type": "open" | "closed"} dicts as
        returned by get_wifi_networks(), or a JSONResponse with status 503
        if get_wifi_networks() has no list to report.
    """
    networks = get_wifi_networks()

    if networks is None:
        oradio_log.error("WiFi event listener is not running; cannot serve the network list")
        return JSONResponse(
            status_code=503,
            content={"message": "Wifi-netwerken zijn niet beschikbaar. Herstart de Oradio"},
        )

    return networks

def shutdown_webapp(_args: dict[str, Any] | None):
    """
    Request a graceful web server shutdown via the service queue.

    Places a REQUEST_STOP message on api_app.state.queue so
    WebService's message-listener thread can stop the portal cleanly.

    Args:
        _args: Unused; accepted to match the command handler signature.
    """
    safe_put(api_app.state.queue, {"request": REQUEST_STOP})

def wifi_connect(args: dict[str, Any] | None):
    """
    Send a WiFi connection request to the service queue.

    Places a REQUEST_CONNECT message containing the SSID and
    optional password on api_app.state.queue for WebService's message-listener thread to act on.

    Args:
        args: dict containing:
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

def save_preset(args: dict[str, Any] | None):
    """
    Save a playlist or webradio entry as a preset and publish the change.

    Persists the updated preset mapping and sends the appropriate
    WEB_PL*_PLAYLIST or WEB_PL*_WEBRADIO command so other modules
    are notified of the change.

    Args:
        args: dict containing:
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

def get_playlist_songs(args: dict[str, Any] | None):
    """
    Return all songs contained in a given playlist.

    Args:
        args: dict containing "playlist" (str) — playlist name.

    Returns:
        The list of songs returned by MPDControl.get_songs().

    Raises:
        ValueError: If args is None or does not contain "playlist".
    """
    playlist = args.get("playlist") if args else None
    if not playlist:
        raise ValueError("'playlist' vereist argument 'playlist'")

    return mpd_control.get_songs(playlist)

def get_search_songs(args: dict[str, Any] | None):
    """
    Return songs matching a search pattern.

    Args:
        args: dict containing "pattern" (str) — search string.

    Returns:
        The list of matching songs returned by MPDControl.search().

    Raises:
        ValueError: If args is None or does not contain "pattern".
    """
    pattern = args.get("pattern") if args else None
    if not pattern:
        raise ValueError("'search' vereist argument 'pattern'")

    return mpd_control.search(pattern)

def modify_playlist(args: dict[str, Any] | None):
    """
    Add or remove a song or playlist via MPD and return the updated playlist list.

    Args:
        args: dict containing:
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

def log_message(args: dict[str, Any] | None):
    """
    Log a message originating from the web interface.

    Allows the browser-side JavaScript to write entries into the server-side
    log for debugging purposes.

    Args:
        args: dict containing "message" (str) — the text to log.

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
    args: dict[str, Any] | None = None

# Serialises /execute and /oradio3.
#
# Both are plain 'def', not 'async def': they call MPD (lock timeout 5 s,
# command timeout 15 s), read and write presets.json on the stick, run
# vcgencmd and wait in validate_presets() for a library scan to finish. As
# 'async def' all of that ran on the event loop and froze the whole portal --
# /keep_alive and /static included -- for as long as it took. FastAPI runs a
# plain 'def' endpoint in its threadpool instead, so the loop stays free.
#
# The threadpool would also run them in parallel, which they never did before
# and were not written for (two presets.json writes through one .tmp file, MPD
# command sequences interleaving). This lock keeps them one at a time, as the
# event loop used to, without putting the loop itself on hold.
_handler_lock = Lock()

@api_app.post("/execute")
def execute(request: ExecuteRequest):
    """
    Dispatch a command from the web interface to the appropriate handler.

    Looks up request.cmd in the command dispatch table and calls the
    associated function with request.args. Runs in FastAPI's threadpool,
    one request at a time (see _handler_lock).

    Args:
        request: Parsed ExecuteRequest body from the POST payload.

    Returns:
        The handler's return value on success (type varies by command), or a
        JSONResponse with status 400 if the command name is unknown or a
        required argument is missing.
    """
    oradio_log.info("Executing '%s' with args '%s'", request.cmd, mask_secrets(request.args))

    commands = {
        "play"       : play_song,
        "networks"   : get_networks,
        "shutdown"   : shutdown_webapp,
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
        with _handler_lock:
            result = commands[request.cmd](request.args)
        return result
    except ValueError as ex_err:
        return JSONResponse(status_code=400, content={"message": str(ex_err)})

##### Oradio3 #############################################

@api_app.get("/oradio3")
def oradio3_page(request: Request):
    """
    Render and serve the Oradio3 web interface page.

    Assembles the full template context by gathering the last connected
    WiFi network, saved presets, available MPD directories and playlists,
    and software version information. Runs in FastAPI's threadpool, under
    _handler_lock: it reads presets.json, which /execute writes.

    Args:
        request: The incoming HTTP request (passed through to the template engine).

    Returns:
        A TemplateResponse rendering oradio3.html with the assembled context.
    """
    oradio_log.info("Serving Oradio3 page")

    # Last WiFi network connected before the access point was started (empty string if none).
    oldssid = get_saved_network()

    sw_info = _get_sw_info()

    with _handler_lock:
        context = {
            "oldssid"    : oldssid,
            "presets"    : load_presets(),
            # Which of those presets point at nothing, so the page can say so
            # instead of showing a playlist name that no longer exists. Asked of
            # mpd_control rather than worked out here or in the browser: it is the
            # same question the button press asks, and one definition of "invalid"
            # is the point.
            "invalid"    : mpd_control.validate_presets(),
            "directories": mpd_control.get_directories(),
            "playlists"  : mpd_control.get_playlists(),
            "hw_serial"  : get_serial(),
            "sw_dtstamp" : sw_info["dtstamp"],
            "sw_version" : sw_info["version"],
        }

    return templates.TemplateResponse(request=request, name="oradio3.html", context=context)

##### Portal watchdog #####################################
#
# The portal must stop by itself once nobody uses it, but must survive the user
# briefly switching to another app. The server cannot tell "page hidden" from
# "user gone" by the pings alone -- in both cases they simply stop, and mobile
# browsers suspend JavaScript too quickly to rely on a goodbye message -- so the
# watchdog combines two signals:
#
#   1. Pings from the page (POST /keep_alive, only sent while it is visible).
#      Each ping pushes the deadline KEEP_ALIVE_TIMEOUT ahead. Before the first
#      ping the deadline is FIRST_CONTACT_TIMEOUT after start.
#   2. Clients associated with the access point (iw station dump). Once a client
#      has been seen, NO_STATION_TIMEOUT without any client stops the portal:
#      the phone left the Oradio network, so the user cannot come back.
#
# Whichever rule fires first stops the portal. Captive-portal probes from the
# OS (/hotspot-detect.html, /generate_204, ...) deliberately do NOT count as
# activity: some phones repeat them for as long as they stay connected, which
# would keep a forgotten portal alive forever.

def _iw(*args: str) -> str | None:
    """
    Run 'iw dev <WIFI_INTERFACE> <args>' and return its output.

    These iw queries need no root privileges.

    Args:
        args: The iw sub-command, e.g. "info" or "station", "dump".

    Returns:
        stdout on success, or None if iw failed (missing, interface gone, ...).
    """
    try:
        result = subprocess.run(
            ["iw", "dev", WIFI_INTERFACE, *args],
            capture_output=True, text=True, timeout=STATION_CHECK_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as ex_err:
        oradio_log.warning("Station check failed: %s", ex_err)
        return None
    if result.returncode != 0:
        oradio_log.warning("Station check failed: %s", result.stderr.strip())
        return None
    return result.stdout

def _station_connected() -> bool | None:
    """
    Report whether any client is associated with the Oradio access point.

    Only counts while the interface is in AP mode: in client mode, 'station
    dump' lists the router the Oradio itself is connected to, which right
    after a portal start (before the switch to AP mode) would otherwise look
    like a phone that joined and then left.

    Returns:
        True if at least one client is associated with the access point,
        False if none (or the access point is not up),
        None if the check itself failed.
    """
    info = _iw("info")
    if info is None:
        return None
    if "type AP" not in info:
        return False
    stations = _iw("station", "dump")
    if stations is None:
        return None
    return "Station " in stations

class PortalWatchdog(ThreadTemplate):
    """
    Stop the portal once nobody is using it; see the section comment above.

    Built on ThreadTemplate with interval=WATCHDOG_INTERVAL: do_work() is one
    evaluation of the rules. web_service arms and starts it together with the
    Uvicorn server and stops it in WebService.stop(). It runs on its own
    thread rather than as a task on Uvicorn's event loop, because the server
    is stopped with force_exit, which skips the ASGI lifespan shutdown.

    The session state (deadline, stations seen) is set by arm(), not setup():
    setup() runs again on a restart after a crash, and resetting there would
    lose a keep-alive or restart the FIRST_CONTACT_TIMEOUT window.

    The /keep_alive endpoint (event loop thread) and do_work() (watchdog
    thread) share the deadline; _state_lock keeps their accesses consistent.
    """
    # A crashed watchdog would leave the portal open indefinitely; restarting
    # is safe because setup() and teardown() hold nothing.
    restart_on_crash = True

    def __init__(self) -> None:
        """Create an idle watchdog; arm() and safe_start() bring it to life."""
        super().__init__(interval=WATCHDOG_INTERVAL, name="PortalWatchdog")
        self._state_lock = Lock()
        self._queue: Queue | None = None   # Set by arm()
        self._deadline = 0.0
        self._pinged = False
        self._station_seen = False
        self._station_lost_since: float | None = None
        self._station_check_ok = True

    def arm(self, queue: Queue) -> None:
        """
        Prepare a new portal session. Call before safe_start().

        Args:
            queue: The service request queue that receives REQUEST_STOP.
        """
        with self._state_lock:
            self._queue              = queue
            self._deadline           = monotonic() + FIRST_CONTACT_TIMEOUT  # Ping deadline
            self._pinged             = False  # A page has pinged at least once
            self._station_seen       = False  # A client has been associated at least once
            self._station_lost_since = None   # Monotonic time the last client left, or None
            self._station_check_ok   = True   # False once iw proved unusable: rule 2 skipped

    def ping(self) -> None:
        """Register a keep-alive from the page: push the deadline ahead."""
        with self._state_lock:
            if not self._pinged:
                self._pinged = True
                oradio_log.debug("Portal page is open: first keep-alive received")
            else:
                oradio_log.trace("Keep-alive received")
            self._deadline = monotonic() + KEEP_ALIVE_TIMEOUT

    def _check_stations(self, now: float) -> str | None:
        """
        Apply rule 2 (no client associated any more).

        Args:
            now: Current monotonic time.

        Returns:
            A reason string if the portal must stop, otherwise None.
        """
        if not self._station_check_ok:
            return None

        connected = _station_connected()
        reason = None

        if connected is None:
            # Fall back to the ping rules only, rather than guessing.
            self._station_check_ok = False
            oradio_log.warning("Station check unavailable: portal stops on ping timeouts only")

        elif connected:
            if not self._station_seen:
                oradio_log.debug("Client associated with the access point")
            elif self._station_lost_since is not None:
                oradio_log.debug("Client associated with the access point again")
            self._station_seen = True
            self._station_lost_since = None

        elif self._station_seen:
            # A client was here and has left. Before anyone joined at all,
            # FIRST_CONTACT_TIMEOUT covers the wait instead.
            if self._station_lost_since is None:
                self._station_lost_since = now
                oradio_log.debug("No client associated with the access point any more")
            elif now - self._station_lost_since >= NO_STATION_TIMEOUT:
                reason = f"no client associated for {NO_STATION_TIMEOUT}s"

        return reason

    def do_work(self) -> None:
        """
        Evaluate the rules once; called every WATCHDOG_INTERVAL seconds.

        When a rule fires, places a REQUEST_STOP message on the service queue
        to trigger a graceful shutdown, and ends its own run: one request per
        session is enough, and WebService.stop() follows right after.
        """
        now = monotonic()

        reason = self._check_stations(now)

        if reason is None:
            with self._state_lock:
                expired, pinged = now >= self._deadline, self._pinged
            if expired:
                if pinged:
                    reason = f"no keep-alive from the page for {KEEP_ALIVE_TIMEOUT}s"
                else:
                    reason = f"portal not opened within {FIRST_CONTACT_TIMEOUT}s"

        if reason is not None and not self.stopping:
            oradio_log.info("Closing the web server: %s", reason)
            if self._queue is None:
                # Unreachable when arm() precedes safe_start(), as web_service does.
                oradio_log.error("Portal watchdog not armed: cannot request the stop")
            else:
                safe_put(self._queue, {"request": REQUEST_STOP})
            # End this run: ThreadTemplate's loop sees the event and exits.
            self._stop_event.set()

# One watchdog per process; web_service reaches it through api_app.state.
api_app.state.watchdog = PortalWatchdog()

@api_app.post("/keep_alive")
async def keep_alive():
    """
    Register that the portal page is open and visible.

    The page pings while it is visible; each ping pushes the watchdog's
    deadline KEEP_ALIVE_TIMEOUT seconds ahead.

    Returns:
        JSONResponse({"status": "ok"}) always.
    """
    api_app.state.watchdog.ping()
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
    oradio_log.trace("Catchall triggered for path: %s", request.url.path)

    return RedirectResponse(url=oradioap_url + "/oradio3", status_code=302)

##### Stand-alone entry point #############################

if __name__ == '__main__':

    import uvicorn
    from threading import Thread                            # pylint: disable=ungrouped-imports
    from messaging import safe_get, DebugMessageHandler     # pylint: disable=ungrouped-imports

    # Most stand-alone entry points share this pattern across modules
    # pylint: disable=duplicate-code

    def _check_requests(queue):
        """
        Monitor the service request queue and print received messages.

        Runs in a background thread. Loops until the bare
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

    request_queue: Queue = Queue()

    # Spawn the message monitor before starting uvicorn so no messages are missed.
    message_listener = Thread(target=_check_requests, args=(request_queue,), name="RequestMonitor")
    message_listener.start()

    api_app.state.queue = request_queue
    api_app.state.watchdog.arm(request_queue)
    api_app.state.watchdog.safe_start()

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
        api_app.state.watchdog.safe_stop()
        safe_put(request_queue, REQUEST_STOP)
        message_listener.join()
        Commands.unsubscribe(cmd_handler.get_queue())
        cmd_handler.stop()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
