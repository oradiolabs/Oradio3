#!/usr/bin/env python3

from pydantic import BaseModel, create_model
from typing import Dict, Any, Optional

import inspect      # logging
import subprocess   # run_shell_script

# Simplified logging function - remove logging and monitoring, only print formatted log message
import queue
import threading

# Create a thread-safe queue for logging messages
log_queue = queue.Queue()

def logging(level, log_text):
    """
    Asynchronous logging of log message in a separate thread while keeping print order correct.
    :param level (str) - level of logging [ 'warning' | 'error' | 'info']
    :param log_text (str) - logging message
    """
    # Get caller information
    inspect_info = inspect.stack()
    module_info  = inspect_info[1]
    mod_name     = inspect.getmodule(module_info[0]).__name__
    frame_info   = inspect_info[1][0]
    func_name    = inspect.getframeinfo(frame_info)[2]

    # Build logging text
    logging_text = f"{mod_name:s} - {func_name:s} : {log_text:s}"

    RED_TXT     = "\033[91m"
    GREEN_TXT   = "\033[92m"
    YELLOW_TXT  = "\033[93m"
    END_TXT     = "\x1b[0m"

    # Add colors to logging text
    if level == 'success':
        logging_text = GREEN_TXT + logging_text + END_TXT
    elif level == 'warning':
        logging_text = YELLOW_TXT + logging_text + END_TXT
    elif level == 'error':
        logging_text = RED_TXT + logging_text + END_TXT

    # Queue the log message
    log_queue.put(logging_text)

# Log processor to print messages in correct order
def process_logs():
    while True:
        try:
            log_message = log_queue.get(block=True)
            print(log_message, flush=True)
            log_queue.task_done()
        except queue.Empty:
            continue

# Start a background thread to process the log queue
log_thread = threading.Thread(target=process_logs, daemon=True)
log_thread.start()

def run_shell_script(script):
    """
    Simplified shell command execution
    :param script (str) - shell command to execute
    Returns exit status and output of running the script
    """
    logging("info", f"Runnning shell script: {script}")
    process = subprocess.run(script, shell = True, capture_output = True, encoding = 'utf-8')
    if process.returncode != 0:
        logging("error", f"shell script error: {process.stderr}")
        return False, process.stderr
    return True, process.stdout


def json_schema_to_pydantic(name: str, schema: Dict[str,Any]) -> BaseModel:
    '''
    Dynamic Model generation based on a JSON schema
    '''
    if "properties" not in schema:  # Skip first entry
        return None    
    fields ={}
    required_fields = set(schema.get("required", []))  # Get required fields from schema
    
    for prop, details in schema["properties"].items():
        field_type = str  # Default type
        if details["type"] == "integer":
            field_type = int
        elif details["type"] == "boolean":
            field_type = bool
        elif details["type"] == "number":
            field_type = float
        elif details["type"] == "array":
            field_type = list
        if "required" in schema and prop in schema["required"]:
            fields[prop] = (field_type, ...)
        else:
            fields[prop] = (field_type, None)

        # Handle optional fields (not in "required")
        if prop not in required_fields:
            fields[prop] = (Optional[field_type], None)  # Mark as Optional
        else:
            fields[prop] = (field_type, ...)  # Required fields
                
    return create_model(name, **fields)


