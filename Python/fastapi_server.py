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
@summary: Class for web interface and Captive Portal server
    :Note
    :Install
    :Documentation
        https://fastapi.tiangolo.com/
"""
import os
import json
import multipart    # Used to get POST form data
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

##### oradio modules ####################
from oradio_logging import oradio_log
from wifi_service import wifi_service
from mpd_control import MPDControl

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################
PRESETS_FILE = USB_SYSTEM + "/presets.json"

# Get the web server app
api_app = FastAPI()

# Get the path for the server to mount/find the web pages and associated resources
web_path = os.path.dirname(os.path.realpath(__file__))

# Mount static files
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Initialize templates with custom filters and globals
templates = Jinja2Templates(directory=web_path+"/templates")

@api_app.middleware("http")
async def middleware(request: Request, call_next):
    """
    “A 'middleware' is a function that works with every request before it is processed
    by any specific path operation. And also with every response before returning it.”
    """
    oradio_log.debug("Send timeout reset message")

    # User interaction: Set event flag to signal timeout counter reset
    api_app.state.event_reset.set()

    # Continue processing requests
    response = await call_next(request)
    return response

#### FAVICON ####################

@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """ Handle default browser request for /favicon.ico """
    return FileResponse(os.path.dirname(__file__) + '/static/favicon.ico')

#### PLAYLISTS ####################

def load_presets():
    """ Load the current presets from presets.json in the USB Systeem folder """
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE, "r") as f:
            return json.load(f)
    else:
        oradio_log.error("Failed to open '%s'", PRESETS_FILE)
        return {"preset1": None, "preset2": None, "preset3": None}

def store_presets(presets):
    """ Write the presets to presets.json in the USB Systeem folder """
    try:
        Path(USB_SYSTEM).mkdir(parents=True, exist_ok=True)
    except FileExistsError as ex_err:
        oradio_log.error("'%s' does not exist. Presets cannot be saved. error: %s", USB_SYSTEM, ex_err)
    try:
        with open(PRESETS_FILE, "w") as f:
            json.dump({"preset1": presets[0], "preset2": presets[1], "preset3": presets[2]}, f, indent=4)
    except IOError as ex_err:
        oradio_log.error("Failed to write '%s'. error: %s", PRESETS_FILE, ex_err)

# Get mpd functions
mpdcontrol = MPDControl()

@api_app.route("/playlists", methods=["GET", "POST"])
async def playlists(request: Request):
    """
    Page managing options to:
      - Assign playlists to presets
      - Show playlist songs
      - Manage own playlists
      - Search songs by artist and title tags
    """
    oradio_log.debug("Serving playlists page")

    # Load presets and list available directories
    presets = load_presets()
    directories = mpdcontrol.get_directories()
    playlists = mpdcontrol.get_playlists()
    folders = sorted(directories + playlists, key=str.casefold)

    # Unknown playlist and thus empty song list
    playlist = ""
    playlist_songs = []

    # Unknown search pattern and thus empty song list
    search = ""
    search_songs = []

    # Unknown action
    action = ""

    if request.method == "POST":
        # Load form data
        form_data = await request.form()
        oradio_log.debug("form_data=%s", form_data)

        # Get requested action
        action = form_data.get('action')

        # Get selected playlist
        playlist = form_data.get("playlist")
        # Get songs for the playlist
        if playlist:
            playlist_songs = mpdcontrol.get_songs(playlist)
            print("playlist=", playlist_songs)

        # Get search pattern
        search = form_data.get('search')
        # Get songs for the search pattern
        if search:
            search_songs = mpdcontrol.search(search)

    # Set playlists page and lists info as context
    context = {
                "anchor"         : action,
                "presets"        : presets,
                "directories"    : directories,
                "playlists"      : playlists,
                "folders"        : folders,
                "playlist"       : playlist,
                "playlist_songs" : playlist_songs,
                "search"         : search,
                "search_songs"   : search_songs
            }

    # Return playlists page and available networks as context
    return templates.TemplateResponse(request=request, name="playlists.html", context=context)

class presets(BaseModel):
    """ Model for playlist asssignment """
    change: str = None
    list_1: str = None
    list_2: str = None
    list_3: str = None

# POST endpoint to save presets
@api_app.post("/save_presets")
async def save_presets(presets: presets):
    """
    Handle POST with presets to assign
    """
    # Create message
    message = {}
#OMJ: Het type klopt niet? Het is geen web service state message, eerder iets als info. Maar voor control is wel een state...
    message = {"type": MESSAGE_WEB_SERVICE_TYPE, "error": MESSAGE_NO_ERROR}

    if presets.change == "preset1":
        oradio_log.debug("preset1 playlist: '%s'", presets.list_1)
        message["state"] = MESSAGE_WEB_SERVICE_PL1_CHANGED
    elif presets.change == "preset2":
        oradio_log.debug("preset2 playlist: '%s'", presets.list_2)
        message["state"] = MESSAGE_WEB_SERVICE_PL2_CHANGED
    elif presets.change == "preset3":
        oradio_log.debug("preset3 playlist: '%s'", presets.list_3)
        message["state"] = MESSAGE_WEB_SERVICE_PL3_CHANGED
    else:
        oradio_log.error("Invalid playlist for '%s'", presets.change)

    # Store presets
    store_presets([presets.list_1, presets.list_2, presets.list_3])

    # Put message in queue
    oradio_log.debug("Send web service message: %s", message)
    api_app.state.message_queue.put(message)

class modify(BaseModel):
    """ Model for modifying playlist """
    action:   str = None
    playlist: str = None
    song:     str = None

# POST endpoint to modify playlist
@api_app.post("/playlist_modify")
async def playlist_modify(modify: modify):
    """
    Handle POST to:
    - Add song to existing playlist
    - Create playlist if no song given and playlist does not exist
    - Create playlist if it does not exist and add given song
    - Remove song from playlist
    - Remove playlist if no song given
    """
    if modify.action == 'Add':
        if modify.song == 'None':
            oradio_log.debug("Create playlist: '%s'", modify.playlist)
        else:
            oradio_log.debug("Add song '%s' to playlist '%s'", modify.song, modify.playlist)
        return mpdcontrol.playlist_add(modify.playlist, modify.song)
    elif modify.action == 'Remove':
        if modify.song == 'None':
            oradio_log.debug("Delete playlist: '%s'", modify.playlist)
        else:
            oradio_log.debug("Delete song '%s' from playlist '%s'", modify.song, modify.playlist)
        return mpdcontrol.playlist_remove(modify.playlist, modify.song)
    else:
        oradio_log.error("Unexpected action '%s'", modify.action)
        return False

class song(BaseModel):
    """ Model for song """
    song: str = None

# POST endpoint to play song
@api_app.post("/play_song")
async def play_song(song: song):
    """
    Handle POST to play a song
    """
    oradio_log.debug("play song: '%s'", song.song)
    mpdcontrol.play_song(song.song)

    # Create message
#OMJ: Het type klopt niet? Het is geen web service state message, eerder iets als info. Maar voor control is wel een state...
    message = {"type": MESSAGE_WEB_SERVICE_TYPE, "state": MESSAGE_WEB_SERVICE_PLAYING_SONG, "error": MESSAGE_NO_ERROR}

    # Put message in queue
    oradio_log.debug("Send web service message: %s", message)
    api_app.state.message_queue.put(message)

#### STATUS ####################

@api_app.get("/status")
async def status(request: Request):
    """ Return status """
    oradio_log.debug("Serving status page")

    # Get Oradio serial number
    stream = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-')
    serial = stream.read().strip()

    # Get wifi network Oradio is connected to
    wifi = wifi_service(api_app.state.message_queue)
    network = wifi.get_wifi_connection()

    # Set playlists page and lists info as context
    context = {
                "serial"  : serial,
                "network" : network
            }

    # Return playlists page and available networks as context
    return templates.TemplateResponse(request=request, name="status.html", context=context)

#### CAPTIVE PORTAL ####################

@api_app.get("/captiveportal")
async def captiveportal(request: Request):
    """ Return captive portal """
    oradio_log.debug("Serving captive portal page")

    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)
    context = {"networks": wifi.get_wifi_networks()}

    # Return active portal page and available networks as context
    return templates.TemplateResponse(request=request, name="captiveportal.html", context=context)


class credentials(BaseModel):
    """ # Model for wifi network credentials """
    ssid: str = None
    pswd: str = None

# POST endpoint to connect to wifi network
@api_app.post("/wifi_connect")
async def wifi_connect(credentials: credentials, background_tasks: BackgroundTasks):
    """
    Handle POST with wifi network credentials
    Handle connecting in background task, so the POST gets a response
    https://fastapi.tiangolo.com/tutorial/background-tasks/#using-backgroundtasks
    """
    # Connect after completing return
    background_tasks.add_task(wifi_connect_task, credentials)

def wifi_connect_task(credentials: credentials):
    """
    Executes as background task
    """
    oradio_log.debug("trying to connect to ssid=%s, pswd=%s", credentials.ssid, credentials.pswd)
    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)

    # Try to connect is handled is separate thread
    wifi.wifi_connect(credentials.ssid, credentials.pswd)

#### CATCH ALL ####################

@api_app.route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Any unknown path will return:
      captive portal if wifi is an access point, or
      playlists if wifi connected to a network
    """
    print("in catch all")
    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)

    # Access point is active, so serve captive portal
    if wifi.get_wifi_connection() == ACCESS_POINT_SSID:
        # Return captive portal
        return await captiveportal(request)

    # Default: serve playlists
    return RedirectResponse(url='/playlists')

# Entry point for stand-alone operation
if __name__ == "__main__":

    # import when running stand-alone
    import uvicorn
    from multiprocessing import Process, Queue

    def check_messages(queue):
        """
        Check if a new message is put into the queue
        If so, read the message from queue and display it
        :param queue = the queue to check for
        """
        print("Listening for messages\n")

        while True:
            # Wait for message
            message = queue.get(block=True, timeout=None)
            # Show message received
            print(f"\nMessage received: '{message}'\n")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Pass the queue to the web server
    api_app.state.message_queue = message_queue

    # Start the web server with log level 'trace'
    uvicorn.run(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_level="trace")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
