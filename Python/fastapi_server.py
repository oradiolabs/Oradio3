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
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from wifi_service import WifiService, get_wifi_networks, get_wifi_connection

##### oradio modules ####################
from oradio_logging import oradio_log
from oradio_utils import run_shell_script
from mpd_control import MPDControl

##### GLOBAL constants ####################
from oradio_const import (
    USB_SYSTEM,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
    MESSAGE_WEB_SERVICE_TYPE,
    MESSAGE_WEB_SERVICE_PL1_CHANGED,
    MESSAGE_WEB_SERVICE_PL2_CHANGED,
    MESSAGE_WEB_SERVICE_PL3_CHANGED,
    MESSAGE_WEB_SERVICE_PL_WEBRADIO,
    MESSAGE_WEB_SERVICE_PLAYING_SONG,
    MESSAGE_NO_ERROR
)
################## USB #############################

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

#### FAVICON ####################

@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """ Handle default browser request for /favicon.ico """
    return FileResponse(os.path.dirname(__file__) + '/static/favicon.ico')

#### PLAYLISTS ####################

def load_presets():
    """ Load the current presets from presets.json in the USB Systeem folder """
    # Catch if USB_SYSTEM does not exist
    if not os.path.isdir(USB_SYSTEM):
        oradio_log.error("USB system path '%s' does not exist or is not a directory", USB_SYSTEM)
        return {"preset1": "", "preset2": "", "preset3": ""}

    # Try to load the presets
    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        oradio_log.warning("Presets file '%s' not found", PRESETS_FILE)
        return {"preset1": "", "preset2": "", "preset3": ""}
    except Exception as ex_err:     # pylint: disable=broad-exception-caught
        oradio_log.error("Failed to read '%s'. error: %s", PRESETS_FILE, ex_err)
        return {"preset1": "", "preset2": "", "preset3": ""}

def store_presets(presets):
    """ Write the presets to presets.json in the USB Systeem folder """
    # Try to store the presets
    try:
        # try to make the directory if it does not exist
        Path(USB_SYSTEM).mkdir(parents=True, exist_ok=True)
    except FileExistsError as ex_err:
        oradio_log.error("'%s' does not exist. Presets cannot be saved. error: %s", USB_SYSTEM, ex_err)

    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as file:
            json.dump({"preset1": presets['preset1'], "preset2": presets['preset2'], "preset3": presets['preset3']}, file, indent=4)
    except IOError as ex_err:
        oradio_log.error("Failed to write '%s'. error: %s", PRESETS_FILE, ex_err)

# Get mpd functions
mpdcontrol = MPDControl()

@api_app.get("/playlists")
async def playlists_page(request: Request):
    """
    Page managing options to:
      - Assign playlists to presets
      - Show playlist songs
      - Manage own playlists
      - Search songs by artist and title tags
    """
    oradio_log.debug("Serving playlists page")

    # Return playlist page and presets, directories and playlists as context
    context = {
                "presets"     : load_presets(),
                "directories" : mpdcontrol.get_directories(),
                "playlists"   : mpdcontrol.get_playlists()
            }
    return templates.TemplateResponse(request=request, name="playlists.html", context=context)

class ChangedPreset(BaseModel):
    """ Model for playlist asssignment """
    preset:   str = None
    playlist: str = None

# POST endpoint to save changed preset
@api_app.post("/save_preset")
async def save_preset(changedpreset: ChangedPreset):
    """ Handle POST with changed preset """
    oradio_log.debug("Save changed preset '%s' to playlist '%s'", changedpreset.preset, changedpreset.playlist)

    # Create message
    message = {}
    message = {"type": MESSAGE_WEB_SERVICE_TYPE, "error": MESSAGE_NO_ERROR}

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

        if mpdcontrol.preset_is_webradio(changedpreset.preset):
            # Send message playlist is web radio
            message["state"] = MESSAGE_WEB_SERVICE_PL_WEBRADIO
            oradio_log.debug("Send web service message: %s", message)
            api_app.state.service.msg_q.put(message)
        else:
            # Send message which playlist has changed
            message["state"] = preset_map[changedpreset.preset]
            oradio_log.debug("Send web service message: %s", message)
            api_app.state.service.msg_q.put(message)

    else:
        oradio_log.error("Invalid preset '%s'", changedpreset.preset)

class Songs(BaseModel):
    """ Model for getting songs from mpd """
    source:  str = None
    pattern: str = None

# POST endpoint to get songs
@api_app.post("/get_songs")
async def get_songs(songs: Songs):
    """ Handle POST for getting the songs for the given source """
    oradio_log.debug("Serving songs from '%s' for pattern '%s'", songs.source, songs.pattern)
    if songs.source == 'playlist':
        return mpdcontrol.get_songs(songs.pattern)
    if songs.source == 'search':
        return mpdcontrol.search(songs.pattern)
    oradio_log.error("Invalid source '%s'", songs.source)
    return JSONResponse(status_code=400, content={"message": f"De source '{songs.source}' is ongeldig"})

class Modify(BaseModel):
    """ Model for modifying playlist """
    action:   str = None
    playlist: str = None
    song:     Optional[str] = None

# POST endpoint to modify playlist
@api_app.post("/playlist_modify")
async def playlist_modify(modify: Modify):
    """
    Handle POST to:
    - Add song to existing playlist
    - Create playlist if no song given and playlist does not exist
    - Create playlist if it does not exist and add given song
    - Remove song from playlist
    - Remove playlist if no song given
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

class Song(BaseModel):
    """ Model for song """
    song: str = None

# POST endpoint to play song
@api_app.post("/play_song")
async def play_song(song: Song):
    """
    Handle POST to play a song
    """
    oradio_log.debug("play song: '%s'", song.song)
    mpdcontrol.play_song(song.song)

    # Create message
#OMJ: Het type klopt niet? Het is geen web service state message, eerder iets als info. Maar voor control is wel een state...
    message = {
        "type": MESSAGE_WEB_SERVICE_TYPE,
        "state": MESSAGE_WEB_SERVICE_PLAYING_SONG,
        "error": MESSAGE_NO_ERROR
    }

    # Put message in queue
    oradio_log.debug("Send web service message: %s", message)
    api_app.state.service.msg_q.put(message)

#### STATUS ####################

@api_app.get("/status")
async def status_page(request: Request):
    """ Return status """
    oradio_log.debug("Serving status page")

    # Get Oradio serial number
    stream = os.popen('vcgencmd otp_dump | grep "28:" | cut -c 4-')
    serial = stream.read().strip()

    # Get wifi network Oradio is connected to
    network = get_wifi_connection()

    # Return status page and serial and active wifi connection as context
    context = {
                "serial"  : serial,
                "network" : network
            }
    return templates.TemplateResponse(request=request, name="status.html", context=context)

#### NETWORK ####################

@api_app.get("/network")
async def network_page(request: Request):
    """ Return network """
    oradio_log.debug("Serving network page")

    # Get Spotify name
    oradio_log.debug("Get Spotify name")
    cmd = "systemctl show librespot | sed -n 's/.*--name \\([^ ]*\\).*/\\1/p' | uniq"

    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to get Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Return network page and available networks and Spotify name as context
    context = {
                "networks": get_wifi_networks(),
                "spotify": response.strip()
            }
    return templates.TemplateResponse(request=request, name="network.html", context=context)

class Credentials(BaseModel):
    """ # Model for wifi network credentials """
    ssid: str = None
    pswd: str = None

# POST endpoint to connect to wifi network
@api_app.post("/wifi_connect")
async def wifi_connect(credentials: Credentials, background_tasks: BackgroundTasks):
    """
    Handle POST with wifi network credentials
    Handle connecting in background task, so the POST gets a response
    https://fastapi.tiangolo.com/tutorial/background-tasks/#using-backgroundtasks
    """
    # Connect after completing return
    background_tasks.add_task(wifi_connect_task, credentials)

def wifi_connect_task(credentials: Credentials):
    """
    Executes as background task
    """
    oradio_log.debug("trying to connect to ssid=%s", credentials.ssid)

    # wifi_connect starts a thread handling the connection setup
    # IMPORTANT: Need to use parent class, as stopping the server will remove local data
    api_app.state.service.wifi.wifi_connect(credentials.ssid, credentials.pswd)

    # Stop the web service
    api_app.state.service.stop()

class Spotify(BaseModel):
    """ # Model for Spotify device name """
    name: str = None

# POST endpoint to set Spotify device name
@api_app.post("/spotify")
async def spotify_name(spotify: Spotify):
    """
    Handle POST to store Spotify device name
    """
    oradio_log.debug("Set Spotify name to '%s'", spotify.name)

    # Spotify name must be one or more uppercase letters, lowercase letters, numbers, - or _
    pattern = r'^[A-Za-z0-9_-]+$'
    if not bool(re.match(pattern, spotify.name)):
        response = f"'{spotify.name}' is ongeldig. Alleen hoofdletters, kleine letters, cijfers, - of _ is toegestaan"
        oradio_log.error(response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Change name in librespot service configuration file
    cmd = f"sudo sed -i 's/--name \\S*/--name {spotify.name}/' /etc/systemd/system/librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Have systemd reload all .service files
    cmd = "sudo systemctl daemon-reload"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    # Restart librespot service to activate new name
    cmd = "sudo systemctl restart librespot.service"
    result, response = run_shell_script(cmd)
    if not result:
        oradio_log.error("Error during <%s> to set Spotify name, error: %s", cmd, response)
        # Return fail, so caller can try to recover
        return JSONResponse(status_code=400, content={"message": response})

    return spotify.name

#### CATCH ALL ####################

@api_app.route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all(request: Request):
    """
    Any unknown path will return playlists page
    """
    oradio_log.debug("Catchall triggered for path: %s", request.url.path)
    return RedirectResponse(url='/playlists', status_code=302)

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

    class DummyWebService():
        """ Dummy class to handle API calls sending messages and calling functions """

        def __init__(self, queue):
            """" Class constructor: Setup the class """
            # Initialize
            self.msg_q = queue

            # Register wifi service
            self.wifi = WifiService(self.msg_q)

            # Pass the class instance to the web server
            api_app.state.service = self

        def stop(self):
            """ Dummy for handling network page shutdown """
            print("Call to dummy 'stop server': not really stopping...")

    # Initialize
    message_queue = Queue()

    # Start  process to monitor the message queue
    message_listener = Process(target=check_messages, args=(message_queue,))
    message_listener.start()

    # Setup dummy web service
    web_service = DummyWebService(message_queue)

    # Start the web server with log level 'trace'
    uvicorn.run(api_app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT, log_level="trace")

    # Stop listening to messages
    if message_listener:
        message_listener.kill()
