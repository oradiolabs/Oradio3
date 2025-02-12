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
import os, sys, json
from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

##### oradio modules ####################
from oradio_logging import oradio_log
from wifi_service import wifi_service

##### GLOBAL constants ####################
from oradio_const import *

##### LOCAL constants ####################

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

#### CATCH ALL / CAPTIVE PORTAL ####################

@api_app.get("/{full_path:path}")
async def captiveportal(request: Request):
    """
    Any unknown path will return:
      captive portal if WiFi is an access point, or
      home page if WiFi connected to a network
    """
    # Get access to wifi functions
    wifi = wifi_service(api_app.state.message_queue)

    if wifi.get_wifi_connection() == ACCESS_POINT_SSID:
        oradio_log.debug("Send captive portal page")

        # Get list of available WiFi networks
        networks = wifi.get_wifi_networks()

        # Set captive portal page and context
        page = "captiveportal.html"
        context = {"networks": networks}

    else:
        oradio_log.debug("Send home page")

        # Get info for playlist page
#        presets = load_presets()
        presets = {"preset1": None, "preset2": None, "preset3": None}
#        folders = get_mpd_directories_simple()
        folders = []
        selected_preset_files = []

        # Set playlist page and context
        page = "playlists.html"
        context = {
                    "presets": presets,
                    "folders": folders,
                    "selected_preset_files": selected_preset_files
                }

    # Return page and context
    return templates.TemplateResponse(request=request, name=page, context=context)

# Model for WiFi network credentials
class credentials(BaseModel):
    ssid: str = None
    pswd: str = None

# POST endpoint to connect to WiFi network
@api_app.post("/wifi_connect")
async def wifi_connect_task(credentials: credentials, background_tasks: BackgroundTasks):
    """
    Handle POST with WiFi network credentials
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
