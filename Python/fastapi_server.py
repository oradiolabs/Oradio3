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
import os, sys, json, subprocess, multipart
from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

##### oradio modules ####################
from oradio_logging import oradio_log
from wifi_service import wifi_service

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

# Handle default browser request for /favicon.ico
@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(os.path.dirname(__file__) + '/static/favicon.ico')

#### PLAYLISTS ####################

def load_presets():
    """
    Load the current presets from presets.json
    Returns: {"preset1": ..., "preset2": ..., "preset3": ...}
    """
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE, "r") as f:
            return json.load(f)
    else:
        oradio_log.error(f"Failed to open '{PRESETS_FILE}'")
        return {"preset1": None, "preset2": None, "preset3": None}

#OMJ: moet dit geen functie van mpd_control zijn?
def get_mpd_list(path = None):
    """
    List the names in the path:
        if no path the available playlists
        if path the songs in the playlist
    Returns: List of names, without directory paths
    """
    # Start from mpd root
    cmd = ["mpc", "ls"]

    # Add level 
    if path:
        cmd.append(path)

    # Get mpd info
    process = subprocess.run(cmd, capture_output = True, text = True)
    if process.returncode != 0:
        oradio_log.error(f"shell script error: {process.stderr}")
        return []

    if not path:
        # Parse string into list
        return process.stdout.strip().split("\n")
    else:
        # Extract only the filenames from the full paths, without the mp3 extension
        files = process.stdout.strip().split("\n")
        return [os.path.basename(file[:-4]) for file in files]

#OMJ: moet dit geen functie van mpd_control zijn?
def get_mpd_search(pattern):
    """
    List songs with artist name containing pattern and songs with title containing pattern
    https://mpd.readthedocs.io/en/latest/protocol.html#filters
    Returns: List of songs
    """
# mpc search '(artist contains "abb")' toont alle songs waar abb in de naam van de artiest voorkomt
# mpc search '(title contains "abb")' toont alle songs waar abb in de title voorkomt

    # Build artist search command
    cmd = 'mpc search \'(artist contains "' + pattern + '")\''

    # Get mpd info
    process = subprocess.run(cmd, shell = True, capture_output = True, text = True)
    if process.returncode != 0:
        oradio_log.error(f"shell script error: {process.stderr}")
        return []
    search_artist = process.stdout.strip().split("\n")
    print(f"search_artist={search_artist}")

    # Build title search command
    cmd = 'mpc search \'(title contains "' + pattern + '")\''
    # Parse string into list

    # Get mpd info
    process = subprocess.run(cmd, shell = True, capture_output = True, text = True)
    if process.returncode != 0:
        oradio_log.error(f"shell script error: {process.stderr}")
        return []
    search_title = process.stdout.strip().split("\n")
    print(f"search_title={search_title}")

    return search_artist + search_title

@api_app.route("/playlists", methods=["GET", "POST"])
async def playlists(request: Request):
    """
    Main page to:
      - Set a preset (preset1, preset2, preset3)
      - Display file names in the folder of a selected preset
    """
    oradio_log.debug("Serving playlists page")

    # Load presets and list available directories
    presets = load_presets()
    folders = get_mpd_list()

    # Unknown playlist and thus empty song list
    playlist = None
    songs    = []

    # unknown search pattern thus enpty search list
    search = []

    if request.method == "POST":
        # Load form data
        form_data = await request.form()
        oradio_log.debug(f"form_data={form_data}")
        '''
        # If the user clicked "Set Preset"
        if "set_preset" in request.form:
            preset_name = request.form.get("preset_name")
            folder_path = request.form.get("folder_path")
            if preset_name and folder_path:
                subprocess.run([DEF_PRESETS_SCRIPT, preset_name, folder_path])
            return redirect(url_for("index"))
        '''
        # If the user clicked "Set Presets"
        if "set_presets" in form_data:
            print("user clicked 'set presets'")

        # If the user clicked "Show Songs"
        if "show_songs" in form_data:
            print("user clicked 'show songs'")
            # Get selected playlist
            playlist = form_data.get("playlist")
            # get preset songs
            songs = get_mpd_list(playlist)

        # If the user clicked "custom playlists"
        if "custom_playlists" in form_data:
            print("user clicked 'custom_playlist'")

        # If the user clicked "Search"
        if "search_songs" in form_data:
            print("user clicked 'search songs'")
            search = get_mpd_search(form_data.get("pattern"))
            print(f"search={search}")

    # Set playlists page and lists info as context
    context = {
                "presets" : presets,
                "folders" : folders,
                "playlist": playlist,
                "songs"   : songs,
                "search"  : search
            }

    # Return playlists page and available networks as context
    return templates.TemplateResponse(request=request, name="playlists.html", context=context)

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

# Model for wifi network credentials
class credentials(BaseModel):
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
    oradio_log.debug(f"trying to connect to ssid={credentials.ssid}, pswd={credentials.pswd}")
    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)

    # Try to connect is handled is separate thread
    wifi.wifi_connect(credentials.ssid, credentials.pswd)

#### CATCH ALL ####################

@api_app.route("/{full_path:path}", methods=["GET", "POST"])
async def index(request: Request):
    """
    Any unknown path will return:
      captive portal if wifi is an access point, or
      playlists if wifi connected to a network
    """
    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)

    # Access point is active, so serve captive portal
    if wifi.get_wifi_connection() == ACCESS_POINT_SSID:
        return RedirectResponse(url='/captiveportal')

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
