'''

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
@summary: Class for web interface and Captive Portal
    :Note
    :Install
    :Documentation
        https://fastapi.tiangolo.com/
'''
import os, sys, json
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Running in subdirectory, so tell Python where to find other Oradio modules
sys.path.append("..")

##### oradio modules ####################
import oradio_utils
import wifi_utils
from oradio_const import *

# Get the web server app
api_app = FastAPI()

# Logging
import logging
logger = logging.getLogger('uvicorn.error')
# logger.info(), logger.debug(), logger.error(), etc.

# Get the path for the server to mount/find the web pages and associated resources
web_path = os.path.dirname(os.path.realpath(__file__))

# Mount static files
api_app.mount("/static", StaticFiles(directory=web_path+"/static"), name="static")

# Initialize templates with custom filters and globals
templates = Jinja2Templates(directory=web_path+"/templates")

#### FAVICON ####################

# Handle default browser request for /favicon.ico
@api_app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(os.path.dirname(__file__) + '/static/favicon.ico')

#### PLAYLISTS ####################

#### CATCH ALL / CAPTIVE PORTAL ####################

# Any unknown path will return the home page
# The home page will detect if accessing via the access point:
# I yes, it will present the option to select the network
# If not, it will present the network home page
@api_app.get("/{full_path:path}")
async def captiveportal(request: Request):
    logger.debug('Respond with login page. Called from: ' + request.url.path)

    # Pass timeout_reset to parent process
    api_command = {}
    api_command["command_type"] = COMMAND_WIFI_TYPE
    api_command["command"]      = COMMAND_WIFI_TIMEOUT_RESET

    # Access the shared queue from the app's state
    api_app.state.command_queue.put(api_command)

    # Get list of avaialble wifi networks
    list = wifi_utils.get_wifi_networks()

    # Get active wifi connection
    ssid = wifi_utils.get_wifi_connection()

    # Return page for user to select wifi network, provide password and submit to connect
    return templates.TemplateResponse(request=request, name="captiveportal.html", context={"ssid": ssid, "list": json.dumps(list)})

# Model for wifi network credentials
class credentials(BaseModel):
    ssid: str = None
    pswd: str = None

# POST endpoint to connect to wifi network
@api_app.post("/connect2network")
async def connect2network(credentials: credentials, request: Request):
    logger.debug('Send credentials to parent process')

    # Send network credentials to parent process for setting up a connection after closing down the captive portal
    try:
        # Pass login data to parent process
        # - allow for the captive portal to be dismantled in an orderly way
        # - allows for an announcement to be played
        # - allows for clean error handling
        api_command = {}
        api_command["command_type"] = COMMAND_WIFI_TYPE
        api_command["command"]      = COMMAND_WIFI_CONNECT
        api_command["ssid"]         = credentials.ssid
        api_command["pswd"]         = credentials.pswd

        # Log wifi network connection request
        oradio_utils.logging("info", f"Wifi credentials for queue ={api_command}")

        # Access the shared queue from the app's state
        request.app.state.command_queue.put(api_command)

        # Inform user
        return {"status": "success"}

    # Error handling: log error message and inform user
    except Exception as ex_err:
        oradio_utils.logging("error", str(ex_err))
        return{"status": "error", "error": "Failed to send network credentials to parent process"}

# Entry point for stand-alone operation
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api_app, host=WEB_SERVICE_HOST, port=WEB_SERVICE_PORT, log_level="trace")
