#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

Created on February 8, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       2
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:
    Provides communication with the Remote Monitoring Service (RMS).

    When WiFi connectivity becomes available, a periodic heartbeat is
    started and a SYS_INFO message containing hardware and software
    information is sent. The heartbeat stops when WiFi is lost.

    Any other service in the application (e.g. incident_service) can also
    use RMService.send_message(INCIDENT, incident) to report an
    IncidentMessage to RMS, attaching the current log files for context.
    Like HEARTBEAT/SYS_INFO, this requires start() to have been called;
    RMS is expected to start early enough in the boot sequence that this
    is not a practical limitation.

    send_message() never posts on the calling thread. Every message is
    handed to a background sender thread and the call returns immediately,
    so a slow or unreachable RMS server cannot stall the caller -- the
    incident bus worker, the WiFi handler, or the heartbeat timer. The
    sender posts one message at a time, in the order they were submitted.

    Log files are attached by streaming them straight from disk into a
    multipart body of a size fixed before the send starts, so memory use
    stays flat (tens of kB) no matter how large the logs have grown, and a
    log still being written while it is uploaded cannot corrupt the
    request. The application's own log is attached first, and what follows
    it is bounded per file, in total and in count; a log too large for its
    share is sent as its tail, and files that do not fit at all are named
    in the log. Every incident carries them. If logrotate rotates a log
    out from under
    a send, the POST still completes cleanly and that log is sent again
    from its rotated generation, marked resend.

    Helper functions collect Raspberry Pi telemetry and software version
    information. Outgoing POST requests are protected by a simple
    exponential backoff retry mechanism. A POST that exhausts its attempts
    is logged and publishes an incident, which is reported on the bus but
    never posted to RMS itself. Redirects are
    refused rather than followed, since a redirected POST arrives without
    its body, and a success is only accepted when the reply is RMS's own,
    so a captive portal answering in its place is not read as delivery.

    A heartbeat response may carry a command for the device, which is run
    by a thread of its own, so it cannot hold up the messages behind it.
    The command runs to completion; how long it takes is the sender's
    responsibility.
"""
import re
import json
import uuid
import subprocess
from time import sleep
from pathlib import Path
from collections.abc import Callable
from threading import Timer, Event, Thread
from datetime import datetime
from dataclasses import dataclass
from platform import python_version
from queue import Queue as JobQueue, Empty, Full
from multiprocessing import Queue, Lock
from requests import post, RequestException, Response, Timeout

##### Oradio modules ######################################
from singleton import singleton
from utilities import get_serial, ThreadTemplate
from log_service import oradio_log, ORADIO_LOG_PATH
from messaging import (
    Commands,
    Incidents,
    IncidentMessage,
    MessageHandlerTemplate,
    WIFI_SOURCE,
    WIFI_CONNECTED,
    WIFI_DISCONNECTED,
    WIFI_ACCESS_POINT,
    RMS_SOURCE,
    RMS_START_FAILED,
    RMS_POST_FAILED,
)

##### GLOBAL constants ####################################
from constants import (
    YELLOW, NC,
    RMS_SERVER_URL,
    RMS_SERVER_KEY,
)

##### LOCAL constants #####################################
# RMS message type identifiers
HEARTBEAT = 'HEARTBEAT'
SYS_INFO  = 'SYS_INFO'
INCIDENT  = 'INCIDENT'

# Path to the JSON file written by the deployment pipeline with version info
SOFTWARE_VERSION_FILE = "/var/log/oradio_sw_version.log"

# How the 'generated' field is formatted in every message posted to RMS
TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'

# How often the heartbeat is sent (seconds); currently once per hour
HEARTBEAT_REPEAT = 60 * 60

# Remote Monitoring Service endpoint and HTTP POST tuning parameters
MAX_RETRIES     = 3   # Maximum number of POST attempts before giving up
BACKOFF_FACTOR  = 2   # Base for exponential backoff: delay = BACKOFF_FACTOR ** attempt (1s, 2s, 4s)
CONNECT_TIMEOUT = 5   # Per-attempt TCP/TLS connect timeout in seconds. Separate from the read timeout
                      # below so a server that is simply not there fails in seconds instead of holding
                      # the sender thread for the full timeout.
POST_TIMEOUT    = 30  # Per-attempt read timeout in seconds. Generous because RMS may run its
                      # notification and retention routines inside the POST before responding:
                      # giving up early would treat a stored record as a failure and post it
                      # again on the next attempt.

##### Log file attachment limits ##########################
# Ceilings on what an INCIDENT attaches, so a runaway log cannot turn one incident into a
# multi-hundred-MB upload.
# The per-file cap sits under the server's own FileHelper::MAX_FILE_BYTES (5 MB), above which
# an upload is transferred and then discarded. It is set lower than that ceiling on purpose:
# Strato PHP's upload_max_filesize and post_max_size (128 MB / 128 MB) are not limiting, so
# what binds is the device's own uplink. The fleet includes rural connections, where every
# attached megabyte is real time spent, and a POST that stalls long enough on a single send
# still runs into POST_TIMEOUT.
# The total has no counterpart on the server and is a client-side choice: it bounds what one
# incident costs the device in upload time, which on the slowest connections in the fleet is
# a few minutes at this size. Files that do not fit are named in the log rather than dropped
# quietly, so a missing log is never mistaken for a fault.
# The count matches PHP's max_file_uploads (20): a request carrying more than that has its
# extra files ignored server-side, so they would cost upload time and arrive nowhere.
# Under the standard logrotate policy (250k, rotate 1) real logs sit far below all three, so
# the limits only bite when something is filling a log fast -- which is when an incident is raised.
MAX_UPLOAD_FILE_BYTES  =  3 * 1024 * 1024   # Per attached file; under FileHelper::MAX_FILE_BYTES
MAX_UPLOAD_TOTAL_BYTES = 10 * 1024 * 1024   # All attachments in one POST
MAX_UPLOAD_FILES       = 20                 # Attachments in one POST; matches PHP max_file_uploads
COPY_CHUNK_BYTES       = 64 * 1024          # Read granularity while streaming

# Written into an attachment that could not be read in full, in place of the bytes that are missing.
# A part must send the exact number of bytes it was measured at, so something has to fill the gap;
# saying what happened beats padding silently.
TRUNCATION_NOTE = b"\n[oradio: log rotated or truncated while uploading]\n"

# How many times the logs may be sent for one message when logrotate keeps rotating them mid-send.
# Two means one repeat, which is enough: rotation runs hourly, so a second collision is not rotation
# but something else truncating the logs, and repeating would not help.
MAX_ROTATION_SENDS = 2

# Seconds to wait before sending the logs again after a rotation. logrotate copies the log aside and
# only then truncates it, so the copy may still be in progress at the moment the truncation is noticed.
ROTATION_SETTLE_DELAY = 2

# The only names attached, matching what the ingestion API stores: the current log and the numbered
# generations logrotate leaves beside it.Compressed rotations are out of scope by policy, which is
# what lets any oversized file be sent as its tail: part of a text log is readable, part of a .gz is not.
ALLOWED_LOG_PATTERN = re.compile(r'\.log(\.\d+)?$')

# The log the application itself writes, and so the one holding the lines that explain an
# incident. It is offered the upload budget before anything else in the directory, and its
# current generation before its rotated ones, so a crowded log directory can cost the older
# and less relevant files their place but never this one.
ESSENTIAL_LOG_BASE = "oradio"

# Rejects a filename that cannot be placed in a MIME header as-is. Log names never contain these;
# a stray file in the log directory might.
UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f"\\\x7f]')

##### Remote command execution ############################
# A command from RMS runs to completion; there is no time limit. Knowing what a command does
# and how long it takes on the device is the responsibility of whoever sends it. Anything
# long-running should detach itself -- systemd-run is the usual way -- so that it survives a
# reboot of the service and reports through its own unit rather than this one.

# Where a command's stdout and stderr go. The command writes to this file descriptor itself,
# so nothing is buffered in this process: reading the output instead would let a command that
# writes without stopping grow the service until the OOM killer takes it. It sits in the log
# directory and ends in .log, so it is picked up by the incident upload, and rotated by
# logrotate, like any other log here.
REMOTE_COMMAND_LOG = ORADIO_LOG_PATH / "rms.log"

##### Send queue ##########################################
# Depth of the queue between send_message() and the sender thread. Deep enough to absorb a burst
# of incidents while one POST is in flight, capped so an unreachable server cannot grow it without
# bound: past this, the newest message is dropped with a warning rather than queued forever.
SEND_QUEUE_SIZE = 32

##### Helpers #############################################

def _get_temperature() -> str:
    """
    Return the Raspberry Pi SoC temperature in degrees Celsius.
 
    A platform that supports vcgencmd answers with a reading, so anything
    else -- no binary to run, no answer, or an answer with no number in it
    -- means the platform does not support it.
 
    Returns:
        str: Temperature in °C, e.g. "42.8", or "Unsupported platform" if
        unavailable.
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True, text=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as ex_err:
        # Every way of not getting a reading arrives here. OSError is the
        # child never starting, which off a Pi is every call, there being
        # no vcgencmd to run; SubprocessError is it starting and then
        # timing out or exiting non-zero, the latter thanks to check=True.
        oradio_log.debug("Could not read temperature: %s", ex_err)
        return "Unsupported platform"

    # Output format: "temp=42.8'C". Matched rather than sliced at a fixed
    # position, which only holds for a two-digit reading: a cold boot
    # ("temp=8.4'C") and a thermal event ("temp=100.0'C") are both a digit
    # off and would take the quote or drop the decimal along with them.
    reading = re.search(r"temp=(-?\d+(?:\.\d+)?)", result.stdout)

    return reading.group(1) if reading else "Unsupported platform"

def _get_rpi_version() -> str:
    """
    Return the Raspberry Pi model string.

    Returns:
        str: Human-readable model description, or "Unsupported platform" if unavailable.
    """
    result = subprocess.run(
        ["cat", "/proc/cpuinfo"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return "Unsupported platform"
    for line in result.stdout.splitlines():
        if line.startswith("Model"):
            return line.split(":", 1)[1].strip()
    return "Unsupported platform"

def _get_os_version() -> str:
    """
    Return the operating system description.

    Returns:
        str: OS name and version, or "Unsupported platform" if unavailable.
    """
    result = subprocess.run(
        ["lsb_release", "-a"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return "Unsupported platform"
    for line in result.stdout.splitlines():
        if line.startswith("Description:"):
            return line.split(":", 1)[1].strip()
    return "Unsupported platform"

def _get_sw_version() -> str:
    """
    Return the installed Oradio software version.

    Returns:
        str: Software version string, or "Invalid SW version" if the
        version file is missing or invalid.
    """
    try:
        with open(SOFTWARE_VERSION_FILE, encoding="utf-8") as file:
            data = json.load(file)
        return data["dtstamp"] + " (" + data["gitinfo"] + ")"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        oradio_log.error("'%s': Missing file or invalid content", SOFTWARE_VERSION_FILE)
        return "Invalid SW version"

def _extract_command(response: Response) -> str | None:
    """
    Read the pending command out of an RMS response body.

    RMS wraps its payload in the standard API envelope, so the command
    arrives as data.command:

        {"success": true, ..., "data": {"stored": true, "command": "..."}}

    The unwrapped top-level shape is accepted as well, so a response that
    is relayed rather than returned directly still works.

    Args:
        response: The successful response returned by _post_with_retry().

    Returns:
        The command to run, or None if the body carried none or could not
        be parsed as JSON.
    """
    try:
        body = response.json()
    except ValueError:
        oradio_log.error("RMS response was not JSON: %s", response.text[:200])
        return None

    if not isinstance(body, dict):
        return None

    data = body.get("data")
    command = body.get("command") or (data.get("command") if isinstance(data, dict) else None)

    if command is None:
        return None

    command = str(command).strip()

    return command or None

def _run_remote_command(command: str) -> None:
    """
    Run one command from RMS and record what it did.

    Runs on a thread of its own, so a command that takes minutes -- or never
    finishes -- cannot hold up the sender thread and with it every message
    behind this one.

    The command runs to completion with no time limit imposed here. A
    command that never returns holds this thread until the device reboots;
    nothing else is affected. Judging what a command does and how long it
    takes is the sender's responsibility, not this module's.

    Its stdout and stderr are handed to REMOTE_COMMAND_LOG as a file
    descriptor, so the command writes there itself and this process holds
    none of it. That is what makes an unbounded command safe to allow: the
    output costs disk, which logrotate manages, rather than memory, which
    nothing here could reclaim. The two streams share the descriptor, so
    they interleave in the order the command wrote them, and a long run can
    be followed with tail -f while it is still going.

    Nothing here bounds the file. Trimming it between runs would not bound
    it either -- a single command that writes without stopping grows it
    while it runs -- and would discard history that logrotate keeps as
    rms.log.1 and its successors, which the incident upload collects. So
    the file's size is logrotate's to manage, like every other log here.

    The command still gets its own process session, so it is not tied to the
    lifetime of the service's own process group: a command that restarts or
    stops oradio.service survives long enough to finish doing it.

    Args:
        command: Shell command as received from the RMS server.
    """
    oradio_log.debug("Run command '%s' from RMS server", command)

    try:
        # Append, so the header below and the command's own writes are
        # ordered by the kernel rather than by this process's buffer.
        output = REMOTE_COMMAND_LOG.open("a", encoding="utf-8", errors="replace")
    except OSError as ex_err:
        # The command must still run when its log cannot be opened. A card
        # that has gone read-only is exactly when a repair command matters
        # most, so the output is dropped rather than the command.
        oradio_log.error(
            "Could not open '%s'; running '%s' with its output discarded: %s",
            REMOTE_COMMAND_LOG, command, ex_err
        )
        output = None

    returncode = None

    try:
        if output is not None:
            output.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} | {command}\n")
            # Before the child starts, or the header lands after its output
            output.flush()

        with subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL if output is None else output,
            # Same descriptor as stdout, so the two interleave in order
            stderr=subprocess.STDOUT,
            # executable must be set explicitly; without it Python falls
            # back to /bin/sh which may lack bash-specific features.
            executable="/usr/bin/bash",
            # Its own session, so the command is not signalled along with the
            # service that started it
            start_new_session=True,
        ) as process:
            # No timeout: this returns when the command does. Nothing is read
            # from the child, so there is no pipe to drain and no deadlock to
            # avoid by using communicate() instead.
            returncode = process.wait()
    except OSError as ex_err:
        # The shell itself could not be started: missing, not executable, or
        # no memory to fork with.
        oradio_log.error("Could not run command '%s' from RMS server: %s", command, ex_err)
    finally:
        if output is not None:
            if returncode is not None:
                output.write(f"===== exit code {returncode}\n")
            output.close()

    # output is left non-None once opened, closed or not, so this still
    # reports correctly for a run whose log could not be opened at all.
    where = "discarded" if output is None else f"in '{REMOTE_COMMAND_LOG}'"

    if returncode == 0:
        oradio_log.debug("Command '%s' finished; output %s", command, where)
    elif returncode is not None:
        oradio_log.error(
            "shell script '%s' exit code: %d; output %s", command, returncode, where
        )

def _handle_response_command(response: Response) -> None:
    """
    Execute a command returned by the RMS server, if there was one.

    RMS clears a pending command as soon as it hands it out, so it is
    delivered exactly once: a command that is not run here is not offered
    again on the next heartbeat.

    Handed to a thread rather than run here, because here is the sender
    thread: every message to RMS goes through it, and a command that blocks
    it stops all reporting for as long as it runs. Nothing waits on the
    thread; its result reaches the log either way.

    Warning:
        Executing commands received from a remote system is inherently
        risky and should eventually be replaced by validated commands
        handled elsewhere.

    Args:
        response: The successful response returned by _post_with_retry().
    """
    command = _extract_command(response)

    if command is None:
        return

    # Daemon, so a command still running at shutdown does not keep the
    # process alive. Commands arrive one per heartbeat at most, so no limit
    # is placed on how many of these threads can exist.
    Thread(target=_run_remote_command, args=(command,), name="RmsCommand", daemon=True).start()

def _report_post_failure(context: str, failure: str) -> None:
    """
    Log a POST that exhausted its attempts and publish the outage.

    Args:
        context: Short label used in log messages, e.g. "message".
        failure: Description of the last failure, for the log line.
    """
    oradio_log.error("Failed to POST %s: %s", context, failure)
    Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_POST_FAILED))

def _log_base_name(file_name: str) -> str:
    """
    Reduce a log filename to the name shared by all its generations.

    "oradio.log", "oradio.log.1" and "oradio.log.12" all belong to the same
    log and all reduce to "oradio", which is what lets a resend pick up the
    rotated generations of the file that was rotated, and only those.

    Args:
        file_name: Name of a file matching ALLOWED_LOG_PATTERN.

    Returns:
        str: The part before ".log", or the name unchanged if it is not a
        log name at all.
    """
    match = ALLOWED_LOG_PATTERN.search(file_name)

    return file_name[:match.start()] if match else file_name

def _log_rotation_index(file_name: str) -> int:
    """
    Return which generation of its log a file is.

    "oradio.log" is 0, "oradio.log.1" is 1, and so on, matching logrotate's
    numbering where a higher number means older.

    Args:
        file_name: Name of a file matching ALLOWED_LOG_PATTERN.

    Returns:
        int: The rotation number, 0 for the current log.
    """
    match = ALLOWED_LOG_PATTERN.search(file_name)
    suffix = match.group(1) if match else None

    return int(suffix.lstrip(".")) if suffix else 0

def _selection_order(candidate: tuple) -> tuple:
    """
    Rank one candidate file for the upload budget.

    In order:
      - the essential log first, whatever the timestamps say. Anything else
        in the directory is another service's business; the lines that
        explain the incident are here.
      - then newest first, so recent material outranks old.
      - then current generation before rotated. This decides the case
        logrotate creates by copying a log aside and truncating it within
        the same second: both files carry the same mtime, and the rotated
        one is the larger of the two, so size alone would rank it first.
      - then by name, so the result never depends on directory order.

    Args:
        candidate: (path, mtime, size) as gathered by _collect_log_files().

    Returns:
        tuple: Sort key, ascending.
    """
    path, mtime, _ = candidate

    return (
        0 if _log_base_name(path.name) == ESSENTIAL_LOG_BASE else 1,
        -mtime,
        _log_rotation_index(path.name),
        path.name,
    )

def _collect_log_files(only_bases: set[str] | None = None) -> list[tuple[Path, int, int]]:
    """
    Choose which log files to attach, and which part of each one.

    Only "<name>.log" and its numbered rotations "<name>.log.1" and so on
    are considered; anything else in the directory is ignored.

    Files are ranked by _selection_order(): the essential log first, then
    newest first, so the log that explains the incident gets the budget
    before anything else does, and is the last to be cut off by the file
    count limit. A file larger than what is left of the budget is attached
    as its tail: the end of a
    log is where the failure is, and truncating is better than dropping the
    file or sending the whole thing.

    Nothing is read here -- only sizes are inspected -- so this stays cheap
    even when a log has grown to hundreds of megabytes.

    Args:
        only_bases: Base names (as returned by _log_base_name()) to limit
                    the selection to, or None for every log. Used by a
                    resend, which repeats only the log that was rotated
                    mid-send, together with its rotated generations: the
                    other logs went out complete the first time and do not
                    need sending twice.

    Returns:
        list[tuple[Path, int, int]]: (path, offset, length) per file, in
        attachment order. length is fixed here and is what the body sends,
        whatever the file does afterwards: a log that grows past it is cut
        at that point, and one that is truncated below it is padded out.
    """
    try:
        # stat() per candidate, so a file removed by logrotate between the
        # glob and the sort does not abort the whole selection
        candidates = []
        for path in ORADIO_LOG_PATH.glob("*.log*"):
            if not ALLOWED_LOG_PATTERN.search(path.name):
                continue
            if only_bases is not None and _log_base_name(path.name) not in only_bases:
                continue
            try:
                stats = path.stat()
            except OSError:
                continue
            if path.is_file() and stats.st_size > 0:
                candidates.append((path, stats.st_mtime, stats.st_size))
    except OSError as ex_err:
        oradio_log.error("Could not list log files in '%s': %s", ORADIO_LOG_PATH, ex_err)
        return []

    candidates.sort(key=_selection_order)

    selected: list[tuple[Path, int, int]] = []
    budget = MAX_UPLOAD_TOTAL_BYTES

    for index, (path, _, size) in enumerate(candidates):
        if budget <= 0 or len(selected) >= MAX_UPLOAD_FILES:
            # Everything from here on is older than what was taken, so it is
            # left behind. Named rather than dropped quietly: a log that is
            # simply absent from a record looks like a fault, and this is the
            # only place that can say it was a deliberate omission.
            reason = ("upload budget spent" if budget <= 0
                      else f"limit of {MAX_UPLOAD_FILES} files reached")
            omitted = [candidate.name for candidate, _, _ in candidates[index:]]

            oradio_log.warning(
                "Not attaching %d older file(s), %s: %s",
                len(omitted), reason, ", ".join(omitted)
            )
            break

        if UNSAFE_NAME_CHARS.search(path.name):
            oradio_log.warning("Not attaching '%s': unusable file name", path.name)
            continue

        allowance = min(budget, MAX_UPLOAD_FILE_BYTES)

        if size <= allowance:
            offset, length = 0, size
        else:
            offset, length = size - allowance, allowance
            oradio_log.warning(
                "Attaching last %d bytes of '%s' (%d bytes total)", length, path.name, size
            )

        selected.append((path, offset, length))
        budget -= length

    return selected

class _MultipartBody:
    """
    Streaming multipart/form-data body of an exactly known size.

    Solves the problem that makes attaching a live log awkward: the logs
    are still being written while they are being sent. The size of every
    part is decided up front by _collect_log_files() and this class emits
    exactly that many bytes per part, whatever the file does in the
    meantime. A log that grows during the send is cut at the agreed
    length; one that is truncated under it (logrotate uses copytruncate)
    is padded out to the agreed length. Either way the byte count matches
    the Content-Length that was announced, and the closing boundary is
    always reached, so the server sees a complete, parseable body.

    Reading straight from the logs means no copy of them exists anywhere:
    file content passes through a COPY_CHUNK_BYTES buffer on its way to the
    socket and is never accumulated, so memory use is flat and independent
    of how large the logs have grown.

    requests treats this as a stream, because it is iterable, and takes
    Content-Length from the len attribute, so the request goes out with a
    fixed length rather than in chunked transfer encoding, which not every
    PHP setup accepts.

    Single use: once read, a new instance is needed to send again. Building
    one is cheap (no file is opened until it is read), so _post_with_retry()
    simply builds a fresh body per attempt.

    Attributes:
        len: Exact size of the body in bytes; read as Content-Length by
            requests, and never exceeded by read().
        shrunk: Base names of the logs that gave fewer bytes than they
            were measured at. The body is still completed, padded out to
            the size it announced, but the attachments it produced for
            those logs are partial ones -- so the sender treats this as a
            signal to send them again rather than as a finished job. Only
            meaningful once the body has been read.
    """
    def __init__(
        self,
        boundary: str,
        fields: dict,
        attachments: list[tuple[Path, int, int]],
    ) -> None:
        """
        Lay out the body and compute its size.

        Args:
            boundary:    Multipart boundary, without leading dashes.
            fields:      Message fields to send alongside the files.
            attachments: (path, offset, length) tuples from _collect_log_files().
        """
        marker = f"--{boundary}\r\n".encode()

        # The body as a list of segments: bytes objects are sent verbatim,
        # (path, offset, length) tuples are read from disk when reached.
        self._segments: list = []

        for name, value in fields.items():
            self._segments.append(
                marker
                + f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                + str(value).encode()
                + b"\r\n"
            )

        for path, offset, length in attachments:
            # Each file is sent under a field name equal to its own
            # filename, which is what the ingestion API expects: it walks
            # $_FILES directly rather than looking for known field names.
            self._segments.append(
                marker
                + f'Content-Disposition: form-data; name="{path.name}"; '
                  f'filename="{path.name}"\r\n'.encode()
                + b"Content-Type: text/plain\r\n\r\n"
            )
            self._segments.append((path, offset, length))
            self._segments.append(b"\r\n")

        self._segments.append(f"--{boundary}--\r\n".encode())

        self.len = sum(
            len(segment) if isinstance(segment, bytes) else segment[2]
            for segment in self._segments
        )

        self._chunks = self._iter_chunks()
        self._buffer = b""
        self._remaining = self.len
        self.shrunk: set[str] = set()

    def read(self, amount: int = -1) -> bytes:
        """
        Return the next bytes of the body.

        Args:
            amount: Number of bytes wanted; -1 or negative for the rest of
                    the body, which callers should avoid since it defeats
                    the point of streaming.

        Returns:
            bytes: Up to amount bytes, or b"" once the body is exhausted.
            The total returned over the life of the object is exactly len.
        """
        while amount < 0 or len(self._buffer) < amount:
            chunk = next(self._chunks, None)

            if chunk is None:
                break

            self._buffer += chunk

        if amount < 0:
            data, self._buffer = self._buffer, b""
        else:
            data, self._buffer = self._buffer[:amount], self._buffer[amount:]

        return data

    def __iter__(self):
        """
        Yield the body in chunks.

        Present because requests only treats an object as a stream when it
        is iterable; the actual sending goes through read().
        """
        while True:
            data = self.read(COPY_CHUNK_BYTES)

            if not data:
                return

            yield data

    def _iter_chunks(self):
        """
        Walk the segments, reading file content as it is reached.

        Yields:
            bytes: The next piece of the body, never more in total than
            len: file reads are capped at the agreed length, and a file
            that comes up short is padded rather than left incomplete.
        """
        for segment in self._segments:
            if isinstance(segment, bytes):
                self._remaining -= len(segment)
                yield segment
                continue

            path, offset, length = segment
            short = length

            try:
                with path.open("rb") as handle:
                    handle.seek(offset)

                    while short > 0:
                        # Capped at what is left of the agreed length, so a
                        # log that grew since it was measured contributes
                        # nothing extra: over-sending would push the closing
                        # boundary past Content-Length, and the server would
                        # reject the whole request as malformed.
                        chunk = handle.read(min(COPY_CHUNK_BYTES, short))

                        if not chunk:
                            break

                        short -= len(chunk)
                        self._remaining -= len(chunk)
                        yield chunk
            except OSError as ex_err:
                # Rotated away or unreadable mid-send. The part still owes
                # its agreed bytes, which the padding below supplies.
                oradio_log.warning("Could not read '%s' while sending: %s", path.name, ex_err)

            if short > 0:
                # A log only ever gets shorter because logrotate has just
                # rotated it, which means the content that was about to be
                # read is now in the next generation and can be sent in
                # full. Recorded by base name rather than acted on here:
                # this body has already promised its size and must finish
                # sending it, and only this log needs sending again.
                self.shrunk.add(_log_base_name(path.name))
                oradio_log.warning(
                    "'%s' lost %d bytes while being sent; padding to the announced size",
                    path.name, short
                )
                yield self._padding(short)

    @staticmethod
    def _padding(length: int) -> bytes:
        """
        Fill the remainder of a part that could not be read in full.

        Says so in the file itself rather than padding silently, so a
        support engineer reading the uploaded log sees why it ends the way
        it does.

        Args:
            length: Number of bytes still owed for this part.

        Returns:
            bytes: Exactly length bytes.
        """
        if length <= len(TRUNCATION_NOTE):
            return b"\n" * length

        return TRUNCATION_NOTE + b"\n" * (length - len(TRUNCATION_NOTE))

def _build_multipart_body(
    payload_info: dict,
    only_bases: set[str] | None = None,
) -> tuple[_MultipartBody, str] | None:
    """
    Build the body for a message with its log files attached.

    Args:
        payload_info: Message fields to send alongside the files.
        only_bases:   Passed to _collect_log_files() to limit which logs
                      are attached; None for every log.

    Returns:
        tuple[_MultipartBody, str] | None: The body and the matching
        Content-Type header value, or None when there is nothing to
        attach, in which case the caller posts the fields on their own: an
        incident without its logs is still worth delivering.
    """
    attachments = _collect_log_files(only_bases)

    if not attachments:
        oradio_log.debug("No log files to attach")
        return None

    boundary = uuid.uuid4().hex

    return _MultipartBody(boundary, payload_info, attachments), \
        f"multipart/form-data; boundary={boundary}"

def _rms_response_problem(response: Response) -> str | None:
    """
    Check that a successful status came from RMS and not from something else.

    A 2xx only says something answered. A device that has joined a WiFi
    network without getting past its captive portal, or one behind a
    transparent proxy, gets a perfectly good 200 carrying a login page, and
    treating that as a stored record loses the message with nothing logged.

    RMS answers in Joomla's API envelope, so a body that is JSON with a
    success flag set is the evidence that the record reached the component
    rather than something in the way of it.

    Args:
        response: A response whose status code is below 300.

    Returns:
        str | None: None when the body is RMS's, otherwise a description of
        what answered instead, for the retry log.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code} carrying a non-JSON body: {response.text[:100]!r}"

    if not isinstance(body, dict) or "success" not in body:
        return f"HTTP {response.status_code} carrying an unrecognised JSON body"

    if body["success"] is not True:
        return f"HTTP {response.status_code} reporting success=false: {body.get('message')}"

    return None

def _attempt_post(data, headers: dict, context: str) -> tuple[Response | None, str | None]:
    """
    Make one POST attempt and classify the outcome.

    For a 3xx or 4xx, logs why the request was refused. The caller is left
    with the one decision that is its own, namely whether to try again.

    A 2xx is not taken at face value. It is checked against
    _rms_response_problem() first, so a reply from something standing in
    the way of RMS is not mistaken for a stored record.

    Redirects are not followed. requests turns a redirected POST into a GET
    without its body, so following one would deliver an empty request and
    return the final status as though the record had been stored.

    Args:
        data:    Body to send: either the form fields or an open, rewound
                 multipart body to stream from.
        headers: Request headers, including the multipart Content-Type when
                 data is a prepared body.
        context: Short label used in log messages, e.g. "message".

    Returns:
        tuple[Response | None, str | None]:
            (response, None) when the POST succeeded;
            (None, failure) when it failed in a way worth retrying, with
            failure describing why;
            (None, None) when the server refused the request, by redirect
            or by 4xx, which is final: retrying sends the identical
            request.
    """
    try:
        response = post(
            url=RMS_SERVER_URL,
            headers=headers,
            data=data,
            timeout=(CONNECT_TIMEOUT, POST_TIMEOUT),
            # requests turns a redirected POST into a GET and drops the body, so a
            # redirect would deliver an empty request and hand back the final 200 as
            # though the record had been stored. Followed, that is silent data loss;
            # refused, it is a configuration fault that says so.
            allow_redirects=False,
        )
    except (RequestException, Timeout, OSError) as ex_err:
        # Fall back to the class name: some requests exceptions carry
        # an empty message, which would log a failure with no reason
        return None, str(ex_err) or type(ex_err).__name__

    if 300 <= response.status_code < 400:
        # The server answered, but RMS_SERVER_URL does not address it directly:
        # an http-to-https upgrade, a www canonicalisation, or a missing
        # trailing slash. Retrying repeats the same request for the same answer, so this
        # is final, and the destination is logged to point at the fix.
        oradio_log.error(
            "POST %s redirected: HTTP %d to '%s'. RMS_SERVER_URL must address the endpoint "
            "directly; a redirected POST arrives without its body.",
            context, response.status_code, response.headers.get("Location", "<no Location>")
        )
        return None, None

    if 400 <= response.status_code < 500:
        # The server answered; the request itself is what it refused.
        # Recorded with the status code because the fix
        # differs per code, and reported back as final: no retry, and no
        # incident published.
        oradio_log.error(
            "POST %s rejected: HTTP %d, body: %s",
            context, response.status_code, response.text[:200] or "<none>"
        )
        return None, None

    if response.status_code >= 500:
        # Server-side and possibly transient, so treated like a transport
        # failure and retried.
        return None, f"HTTP {response.status_code}"

    problem = _rms_response_problem(response)

    if problem is not None:
        # Something answered in RMS's place. Retried like any other
        # failure: whatever is in the way, the record did not arrive.
        return None, problem

    return response, None

def _post_attempts(
    payload_info: dict,
    attach_log_files: bool,
    context: str,
    abort: Event | None,
    only_bases: set[str] | None = None,
) -> tuple[Response | None, set[str]]:
    """
    Deliver one message, retrying transport failures with backoff.

    Failures fall into two classes, which call for different responses:

    - 3xx and 4xx mean the server answered and this request is the problem.
      Retrying sends the identical request for the identical answer, so
      there is no retry: a redirect
      (RMS_SERVER_URL not addressing the endpoint), 401 (key rotated) and
      413 (payload too large) are configuration faults, not an outage. No
      incident is published either -- publishing one would POST an incident
      that is rejected in turn, publishing another.
    - 5xx, transport errors (DNS, TLS, timeout) and a 2xx that did not come
      from RMS may clear on their own, so these retry with backoff and, once
      exhausted, log the failure and publish RMS_POST_FAILED.

    Args:
        payload_info:     Form fields to POST.
        attach_log_files: Whether to attach the current log files.
        context:          Short label used in log messages.
        abort:            Set while the service is shutting down.
        only_bases:       Limits which logs are attached; None for all.

    Returns:
        tuple[Response | None, set[str]]: The successful response (or None
        if rejected, exhausted or abandoned), and the base names of any
        logs the body had to pad over because they shrank while being read.
    """
    attempts = MAX_RETRIES
    headers = {"X-Api-Key": RMS_SERVER_KEY}

    for attempt in range(1, attempts + 1):
        if abort is not None and abort.is_set():
            oradio_log.debug("Shutting down; abandoning POST %s", context)
            return None, set()

        # Either the plain fields or, once there is something to attach, the
        # multipart body that carries them along with the files. Annotated
        # because the two are assigned to the same name.
        data: dict | _MultipartBody = payload_info
        body = None

        if attach_log_files:
            # Built per attempt: a body is consumed once it has been read,
            # and rebuilding costs a directory scan, no file access. It also
            # means a retry measures the logs again rather than resending
            # what they held before the previous attempt failed.
            prepared = _build_multipart_body(payload_info, only_bases)

            if prepared is not None:
                body, content_type = prepared
                data = body
                headers["Content-Type"] = content_type

        # failure holds the reason this attempt failed, as text: a transport
        # error and an HTTP 5xx are treated alike from here on, and only ever
        # end up in a log line.
        response, failure = _attempt_post(data, headers, context)

        if failure is None:
            # Either the POST succeeded, or the server rejected it and
            # response is None. Both are final for this cycle.
            if response is None or body is None:
                return response, set()

            return response, body.shrunk

        # Per-attempt detail is informative only while more attempts follow
        if attempts > 1:
            oradio_log.warning("Attempt %d failed to POST %s: %s", attempt, context, failure)

        if attempt < attempts:
            # Wait before retrying; delay grows exponentially with each
            # attempt, cut short if the service is stopping.
            delay = BACKOFF_FACTOR ** attempt

            if abort is not None:
                abort.wait(delay)
            else:
                sleep(delay)

            continue

        _report_post_failure(context, failure)
        return None, set()

    return None, set()  # Unreachable (loop always returns), keeps type checkers happy

def _post_with_retry(
    payload_info: dict,
    attach_log_files: bool = False,
    context: str = "message",
    abort: Event | None = None,
) -> Response | None:
    """
    POST payload_info to the RMS server, retrying on failure.

    Shared across the message types handled by
    WifiMessageHandler.send_message(): all POST to RMS_SERVER_URL under the
    same MAX_RETRIES/BACKOFF_FACTOR/POST_TIMEOUT policy. They differ only
    in whether log files are attached and in what happens with a successful
    response (a heartbeat acts on a returned command, the others do not),
    both of which stay with the caller.

    Transport failures are handled by _post_attempts(). What this adds is
    the one case where a POST succeeds and the result is still not the one
    that was wanted: a log that shrank mid-send, which only happens when
    logrotate rotated it out from under the read. The attachment that went
    out is padded and incomplete, while the content it was missing is now
    sitting in the next generation, so that log is sent again.

    The repeat is narrow on purpose. It carries only the log that was
    rotated and the other generations of the same base name, since the
    rest went out complete the first time, and it adds resend=1 to the
    fields so the extra record it creates says what it is. Because the
    server stores uploads by name, the files it sends replace the partial
    ones rather than piling up beside them.

    Args:
        payload_info:     Form fields to POST.
        attach_log_files: If True, attach the log files selected by
                           _collect_log_files(), rotated logs included, as a
                           streamed multipart body. Built fresh per attempt,
                           since a body is consumed by the attempt that
                           sends it.
        context:          Short label used in log messages, e.g.
                           "message" or "incident".
        abort:            Set while the service is shutting down. Checked
                           before each attempt and used for the backoff wait,
                           so a pending retry cycle gives up promptly instead
                           of holding shutdown open.

    Returns:
        The successful requests.Response, or None if the request was
        rejected with a 4xx, the retryable attempts were exhausted, or the
        send was abandoned because abort was set. When the logs were sent
        more than once, this is the response to the last send.
    """
    fields     = payload_info
    only_bases = None

    for send in range(1, MAX_ROTATION_SENDS + 1):
        response, shrunk = _post_attempts(fields, attach_log_files, context, abort, only_bases)

        if not shrunk:
            return response

        if send == MAX_ROTATION_SENDS:
            # Rotation is hourly at most, so one repeat covers it. Landing
            # here twice means something else is truncating the logs, and
            # repeating further would not fix it.
            oradio_log.error("Logs rotated during every send of %s; keeping the padded copy",
                             context)
            return response

        oradio_log.warning(
            "Logs rotated while sending %s; sending %s again",
            context, ", ".join(sorted(shrunk))
        )

        # The next send carries only the log that was rotated and its own
        # rotated generations. The other logs went out complete already, so
        # repeating them would upload the same bytes twice for nothing.
        only_bases = shrunk

        # Marks the record as the repeat of one already stored, so a
        # duplicate in the records table is self-explaining. Set on a copy:
        # the caller's fields are theirs, and the first send must not carry
        # this.
        fields = {**payload_info, 'resend': 1}

        # logrotate copies the log aside before truncating it. Waiting lets
        # that copy finish, so the resend measures the new generation at its
        # full size rather than catching it half written.
        if abort is not None:
            if abort.wait(ROTATION_SETTLE_DELAY):
                oradio_log.debug("Shutting down; not sending %s again", context)
                return response
        else:
            sleep(ROTATION_SETTLE_DELAY)

    return response  # Unreachable (loop always returns), keeps type checkers happy

class Heartbeat(Timer):
    """
    Timer that repeatedly invokes a callback.

    Extends threading.Timer, overriding run() so the callback executes
    immediately on start and then repeats every interval seconds until
    cancel() is called.

    Use the classmethods start_heartbeat() and stop_heartbeat() rather
    than instantiating directly; they keep at most one timer active.

    Note:
        Each start_heartbeat() call must be free to construct a fresh
        instance, because a Timer thread is consumable and cannot run
        again once it has finished or been cancelled. Keep this class
        undecorated by @singleton, which would pin one instance for the
        lifetime of the process.

    Attributes:
        instance: The active timer, or None when no heartbeat is running.
        start_lock: Serialises start/stop calls so they cannot race on
            instance.
    """
    instance = None
    start_lock = Lock()

    def __init__(self, interval, function, args=None, kwargs=None) -> None:
        """
        Initialise the heartbeat timer.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (tuple, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        super().__init__(interval, function, args=args, kwargs=kwargs)

    def run(self) -> None:
        """
        Execute the callback immediately and repeat until cancelled.

        Exceptions raised by the callback are caught and logged so that the
        timer thread remains alive.
        """
        while not self.finished.is_set():
            try:
                self.function(*self.args, **self.kwargs)
            # Catch all non-system exceptions: we must not let an unpredictable callback
            # error kill the timer thread.
            except Exception as ex_err:  # pylint: disable=broad-exception-caught
                oradio_log.error("Heartbeat execution failed: %s", ex_err)

            # Block for *interval* seconds; returns True early if cancel() is called
            if self.finished.wait(self.interval):
                break

    @classmethod
    def start_heartbeat(cls, interval, function, args=None, kwargs=None) -> None:
        """
        Stop any running heartbeat and start a new one.

        Args:
            interval (int): Time in seconds between successive callback calls.
            function (callable): Callback to invoke on each tick.
            args (tuple, optional): Positional arguments forwarded to *function*.
            kwargs (dict, optional): Keyword arguments forwarded to *function*.
        """
        with cls.start_lock:
            # Cancel and discard the previous instance before creating a new one
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None

            cls.instance = cls(interval, function, args=args, kwargs=kwargs)

            # Daemon thread: exits automatically when the main program exits
            cls.instance.daemon = True
            cls.instance.start()
            oradio_log.info("Heartbeat started")

    @classmethod
    def stop_heartbeat(cls) -> None:
        """
        Cancel the running heartbeat timer, if any.

        Thread-safe: uses start_lock to serialise concurrent calls.
        Does nothing if no heartbeat is currently running.
        """
        with cls.start_lock:
            if cls.instance is not None:
                cls.instance.cancel()
                cls.instance = None
                oradio_log.info("Heartbeat stopped")
            else:
                oradio_log.debug("No heartbeat to stop")

@dataclass(frozen=True)
class _SendJob:
    """
    One message waiting to be posted to RMS.

    Only what the caller knows is captured here. The telemetry that goes
    with the message (temperature, versions) is collected by the sender
    thread just before the POST, so nothing runs on the calling thread.

    Attributes:
        msg_type:  HEARTBEAT, SYS_INFO, or INCIDENT.
        generated: Timestamp of the moment the event happened, not of the
            moment the POST happens: a message that waits behind a slow
            send still reports when its event occurred. For HEARTBEAT and
            SYS_INFO that is when send_message() was called; for INCIDENT
            it is taken from the IncidentMessage itself, which is when the
            incident was raised, before it crossed the bus to get here.
        incident:  The IncidentMessage to report, for INCIDENT only.
    """
    msg_type: str
    generated: str
    incident: IncidentMessage | None = None

class _RmsSender(ThreadTemplate):
    """
    Post queued messages to RMS on a thread of its own.

    Everything RMS sends goes through here, so no caller ever waits for the
    network: submit() returns as soon as the message is queued, and this
    thread does the telemetry collection, the log streaming, the POST and
    its retries. Its callers are the incident bus worker, the WiFi message
    worker and the heartbeat timer, and a POST that runs its full retry
    cycle against an unreachable server takes about a minute and a half.

    One message is posted at a time, in submission order, keeping the peak
    cost of RMS traffic to a single in-flight request.

    Built on ThreadTemplate with interval=0, the same way
    MessageHandlerTemplate is: do_work() blocks on the queue, so there is
    no polling delay between one message and the next.

    ThreadTemplate's stop event doubles as the abort signal handed to
    _post_with_retry(), so a retry cycle already under way gives up when
    the service stops instead of holding shutdown open.
    """
    def __init__(self, serial: str, is_wifi_connected: Callable[[], bool]) -> None:
        """
        Initialise the sender. The thread is started by safe_start().

        Args:
            serial:            Device serial, sent with every message.
            is_wifi_connected: Read just before each POST, so a message
                               queued while WiFi was up is dropped rather
                               than posted if the link went down while it
                               waited.
        """
        self._serial = serial
        self._is_wifi_connected = is_wifi_connected

        # A plain in-process queue: this is a hand-off between threads of
        # one process, unlike the multiprocessing queues used by the
        # message bus. Bounded, so an unreachable server cannot let the
        # backlog grow without limit.
        self._jobs: JobQueue = JobQueue(maxsize=SEND_QUEUE_SIZE)

        # Identity comparison is enough for a queue that never crosses a
        # process boundary, so no unique-value sentinel is needed here.
        self._stop_sentinel = object()

        super().__init__(interval=0, name=self.__class__.__name__)

    def submit(self, job: _SendJob) -> bool:
        """
        Queue a message for sending and return immediately.

        Never blocks and never raises: RMS reporting is best effort, and a
        caller reporting an incident must not be held up (or brought down)
        by the state of the monitoring service.

        Args:
            job: The message to send.

        Returns:
            bool: True if queued, False if the queue was full and the
            message was dropped.
        """
        try:
            self._jobs.put_nowait(job)
        except Full:
            # Dropped rather than queued: the backlog is already deeper
            # than the server is getting through, and the local log still
            # holds everything this message would have carried.
            oradio_log.warning(
                "RMS send queue full (%d); dropping %s message", SEND_QUEUE_SIZE, job.msg_type
            )
            return False

        return True

    def do_work(self) -> None:
        """
        Take one message off the queue and post it.

        Blocks until a message (or the stop sentinel) arrives. Exceptions
        are caught and logged so an unexpected failure on one message does
        not take the sender thread down with it.
        """
        job = self._jobs.get()

        if job is self._stop_sentinel:
            return

        try:
            self._deliver(job)
        # We don't know what code is executed, thus not what exceptions are possible
        except Exception as ex_err:     # pylint: disable=broad-exception-caught
            oradio_log.error(
                "Error sending %s message to RMS: %s", job.msg_type, ex_err, exc_info=True
            )

    def stop(self) -> None:
        """
        Stop the sender thread.

        Anything still queued is discarded first: at shutdown a backlog
        would keep the thread posting well past the join timeout, and the
        messages are already in the local log. The sentinel then unblocks
        the pending get(), and because the stop event is set before it is
        sent, the worker exits rather than blocking on the queue again.
        """
        self._stop_event.set()

        while True:
            try:
                self._jobs.get_nowait()
            except Empty:
                break

        try:
            self._jobs.put_nowait(self._stop_sentinel)
        except Full:
            # Cannot happen: the queue was just drained. A message
            # submitted in between only means the worker wakes on that
            # instead, sees the stop event and exits anyway.
            pass

        # Uses safe_stop()'s own default timeout; it already logs a
        # warning on timeout, so no extra logging is needed here.
        self.safe_stop()

    def _deliver(self, job: _SendJob) -> None:
        """
        Build the payload for one queued message and POST it.

        The telemetry helpers shell out to vcgencmd, lsb_release and the
        like, so they run here, on this thread, and not at submit time.

        A message queued while WiFi was up is dropped if the link has gone
        since. An INCIDENT is always posted, and always carries the current
        log files. Only a HEARTBEAT response is examined for a pending
        command; RMS attaches one to no other message type.

        Args:
            job: The message to send.
        """
        if not self._is_wifi_connected():
            # WiFi went down while this message waited its turn
            oradio_log.debug("WiFi no longer available; not sending %s message", job.msg_type)
            return

        # Base fields present in every message type
        payload_info = {
            'generated': job.generated,
            'serial'   : self._serial,
            'type'     : job.msg_type,
        }

        # Append lightweight runtime telemetry for periodic sign-of-life messages
        if job.msg_type == HEARTBEAT:
            payload_info['temperature'] = _get_temperature()

        # Append full hardware/software identification for onboarding messages
        elif job.msg_type == SYS_INFO:
            payload_info['sw_version'] = _get_sw_version()
            payload_info['python']     = python_version()
            payload_info['rpi']        = _get_rpi_version()
            payload_info['rpi-os']     = _get_os_version()

        # Report an incident from another service, attaching current logs
        elif job.msg_type == INCIDENT:
            # send_message() rejects INCIDENT without one, so this is only
            # for the type checker
            assert job.incident is not None

            # RMS's own incidents are published for other services, never
            # POSTed. A failed POST publishes RMS_POST_FAILED, and posting
            # that would fail in turn and publish another, so this is what
            # stops a failure from looping. Nothing is lost: the POST that
            # would carry it is the one that just failed, and the failure is
            # already in the local log.
            if job.incident.source == RMS_SOURCE:
                oradio_log.debug("Not reporting RMS's own incident: %s", job.incident.message)
                return

            payload_info['source']  = job.incident.source
            payload_info['message'] = job.incident.message

            # Traceback or call stack captured where the incident was raised.
            # Omitted when empty, so routine incidents that suppress it do
            # not post an empty field.
            if job.incident.details:
                payload_info['details'] = job.incident.details

            # RMS attaches a command to heartbeats only, so the response
            # here is unused
            _post_with_retry(
                payload_info, attach_log_files=True, context="incident", abort=self._stop_event
            )

            return

        else:
            # send_message() filters unknown types, so reaching this means
            # a new type was added there and not here
            oradio_log.error("Unsupported message type: %s", job.msg_type)
            return

        response = _post_with_retry(payload_info, context="message", abort=self._stop_event)

        if response is None:
            # Rejected, or all retries failed; _post_with_retry() has
            # already logged it and published an incident where warranted
            return

        # RMS attaches a pending command to a heartbeat response only
        if job.msg_type == HEARTBEAT:
            _handle_response_command(response)

class WifiMessageHandler(MessageHandlerTemplate):
    """
    Handle WiFi state change messages and drive heartbeat and RMS reporting.

    Subscribes to the COMMAND topic filtered to WiFi messages. On a
    WIFI_CONNECTED event the heartbeat timer is started and a one-time
    SYS_INFO message is sent to the RMS server. On a WIFI_DISCONNECTED
    event the heartbeat timer is stopped.

    send_message() also handles INCIDENT, used by other services (e.g.
    incident_service) via RMService.send_message() to report an
    IncidentMessage to RMS. All three message types require this handler
    to exist (i.e. RMService.start() to have been called) and, for
    SYS_INFO/INCIDENT, WiFi to currently be connected.

    None of them is posted here. This handler owns an _RmsSender and only
    queues messages onto it, so neither the WiFi worker thread nor any
    caller of send_message() waits for the network.
    """
    def __init__(self, queue: Queue) -> None:
        """
        Initialise the WiFi message handler and start the sender thread.

        Raises:
            RuntimeError: If the sender thread could not be started. Left to
                propagate so RMService.start() rolls back its subscription
                and publishes RMS_START_FAILED, rather than leaving a
                service that accepts messages it can never send.
        """
        # Cache serial number once; used in every outgoing RMS message
        self._serial = get_serial()

        # Tracks the most recently observed WiFi state; updated in
        # _handle_message() below. Starts False since no WIFI_* message
        # has been processed yet at construction time.
        self._wifi_connected = False

        # Posts run here instead of on whichever thread called
        # send_message(). Started before the base class starts its own
        # worker, so the queue is being drained from the moment the first
        # WiFi message can arrive.
        self._sender = _RmsSender(self._serial, lambda: self._wifi_connected)

        if not self._sender.safe_start() or self._sender.crashed:
            raise RuntimeError("Failed to start RMS sender thread")

        # Initialise base class and start the worker thread
        super().__init__(queue)

    @property
    def wifi_connected(self) -> bool:
        """Whether WiFi is currently connected, per the last WIFI_* message processed."""
        return self._wifi_connected

    def _handle_message(self, message) -> None:
        """
        Handle an incoming WiFi state change message.

        Args:
            message: The received message from the queue.
        """
        if message.message == WIFI_DISCONNECTED:
            self._wifi_connected = False
            Heartbeat.stop_heartbeat()
            oradio_log.debug("WiFi disconnected. Heartbeat stopped.")

        elif message.message == WIFI_CONNECTED:
            self._wifi_connected = True
            Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, self.send_message, args=(HEARTBEAT,))
            # Immediately report hardware/software identity on every new connection
            self.send_message(SYS_INFO)
            oradio_log.debug("WiFi connected. Heartbeat started and system info sent.")

        elif message.message == WIFI_ACCESS_POINT:
            # Heartbeat cannot be active, info message cannot be sent
            self._wifi_connected = False

        else:
            oradio_log.error("Unexpected message: %s", message)

    def send_message(self, msg_type: str, incident: IncidentMessage | None = None) -> None:
        """
        Queue a message for the RMS server and return.

        The message is validated here, on the calling thread, so a mistake
        is reported to whoever made it. Everything after that -- collecting
        telemetry, attaching logs, the POST and its retries -- happens on
        the sender thread, so this call does not wait for the network. A
        message that cannot be queued is dropped, never blocked on.

        HEARTBEAT and SYS_INFO carry runtime/hardware telemetry. INCIDENT
        reports an IncidentMessage from another service, with the current
        log files attached.

        The logs an INCIDENT carries are the logs as they are when the
        sender reaches the message, which for a queued message is not
        exactly the moment the incident was raised.

        Only queued while WiFi is currently known to be connected; if not,
        nothing is sent and a debug line is logged instead, since a POST
        with no network would burn through the full retry and backoff cycle
        before failing anyway.

        Args:
            msg_type: HEARTBEAT, SYS_INFO, or INCIDENT.
            incident: Required when msg_type is INCIDENT (ignored
                      otherwise) -- the IncidentMessage to report.
        """
        if msg_type not in (HEARTBEAT, SYS_INFO, INCIDENT):
            oradio_log.error("Unsupported message type: %s", msg_type)
            return

        if msg_type == INCIDENT and incident is None:
            oradio_log.error("send_message(INCIDENT) requires an IncidentMessage")
            return

        if not self._wifi_connected:
            oradio_log.debug("WiFi not available; not sending %s message", msg_type)
            return

        # Timestamped here rather than at POST time, so the message reports
        # when its event happened and not when the sender got to it. For an
        # incident the event is when it was raised, which is earlier still:
        # it has already crossed the incident bus and its queue to get here,
        # so its own timestamp is used in place of the current time.
        if msg_type == INCIDENT:
            # send_message() rejects INCIDENT without one above, so this is
            # only for the type checker
            assert incident is not None
            generated = datetime.fromtimestamp(incident.timestamp).strftime(TIMESTAMP_FORMAT)
        else:
            generated = datetime.now().strftime(TIMESTAMP_FORMAT)

        self._sender.submit(
            _SendJob(
                msg_type=msg_type,
                generated=generated,
                incident=incident,
            )
        )

    def stop(self) -> None:
        """
        Stop the sender thread, then the message worker.

        In this order so that nothing is left sitting in the send queue
        with no thread left to drain it.
        """
        self._sender.stop()
        super().stop()

@singleton
class RMService:
    """
    Manage communication with the Remote Monitoring Service (RMS).

    Subscribes to WiFi connectivity events and delegates all message
    handling -- HEARTBEAT, SYS_INFO, and INCIDENT alike -- to an internal
    WifiMessageHandler. All three require start() to have been called, so
    start RMS early in the application's boot sequence, ahead of any
    service that may raise an incident. See
    WifiMessageHandler.send_message() for the per-type detail.

    Delivery is asynchronous: send_message() queues the message and
    returns, and a background thread posts it. Callers get no delivery
    result back, by design -- a service reporting an incident should not
    be waiting on, or reacting to, the state of the monitoring service.

    Construction only sets up internal state; the WiFi subscription and
    the handler's worker thread begin at the first start() call. Callers
    therefore choose when subscribing and threading start, and may
    stop() and start() again later.
    """
    def __init__(self) -> None:
        """
        Initialise the service.

        No subscription is made and no thread is started here; call
        start() to begin operation.
        """
        self._queue: Queue | None = None
        self._handler: WifiMessageHandler | None = None

    def start(self) -> None:
        """
        Subscribe to WiFi state change events and start the handler thread.

        Idempotent: calling start() when the service is already running is
        a no-op. If handler creation fails, any partial subscription is
        rolled back and an incident is published.
        """
        if self._handler is not None:
            oradio_log.debug("RMS service already running")
            return

        # Subscribe to WiFi messages only
        self._queue = Commands.subscribe(sources=(WIFI_SOURCE,))

        # Start queue listener thread
        try:
            self._handler = WifiMessageHandler(self._queue)
            oradio_log.info("RMS service started")
        except Exception as ex_err:  # pylint: disable=broad-exception-caught
            oradio_log.error("RMS service failed to start: %s", ex_err)
            # Roll back the subscription so a retry via start() starts clean
            Commands.unsubscribe(self._queue)
            self._queue = None
            Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_START_FAILED))

    def send_message(self, msg_type: str, incident: IncidentMessage | None = None) -> None:
        """
        Send a message to the RMS server.

        Thin delegator to the internal WiFi-driven handler, letting
        callers and the interactive test menu trigger sends on the
        RMService instance without touching internal state. See
        WifiMessageHandler.send_message() for what each type does and
        which require WiFi to be connected.

        Returns as soon as the message is queued; the POST itself happens
        on the sender thread, so this is safe to call from a worker thread
        that must not block, such as the incident bus handler.

        Args:
            msg_type: HEARTBEAT, SYS_INFO, or INCIDENT.
            incident: Required when msg_type is INCIDENT (ignored
                      otherwise) -- the IncidentMessage to report.
        """
        if self._handler is None:
            oradio_log.error("RMS service not started; cannot send %s", msg_type)
            return

        self._handler.send_message(msg_type, incident)

    def stop(self) -> None:
        """
        Shut down the RMS service cleanly.

        Stops the heartbeat timer, unsubscribes from the command queue,
        and signals the worker threads to exit. Anything still waiting to
        be sent is discarded rather than posted, so shutdown is not held
        up by a backlog. Does nothing if the service was never started (or
        has already been stopped).
        """
        if self._handler is None:
            oradio_log.debug("RMS service not running")
            return

        # Invariant: start() always sets _queue and _handler together, and
        # every reset path (here and the rollback in start()) clears both
        # together, so _handler being set guarantees _queue is too. Asserted
        # so mypy can narrow _queue from Optional[Queue] to Queue below.
        assert self._queue is not None

        Heartbeat.stop_heartbeat()
        Commands.unsubscribe(self._queue)
        self._handler.stop()
        self._handler = None
        self._queue = None
        oradio_log.info("RMS service stopped")

##### Stand-alone entry point #############################

if __name__ == "__main__":

    # Imports only relevant when stand-alone
    from utilities import input_prompt              # pylint: disable=ungrouped-imports
    from wifi_service import WifiService

    # Most modules use similar code in stand-alone
    # pylint: disable=duplicate-code

    # Source for incidents raised by this menu. Deliberately not RMS_SOURCE:
    # RMS's own incidents are dropped before the POST to stop a failed post
    # from publishing an incident that triggers another, so an incident from
    # RMS_SOURCE would be queued and discarded rather than sent. Named to
    # match the convention in messaging.py, and distinct enough that these
    # records are recognisable as tests on the RMS side.
    TEST_SOURCE = "RMS test message"

    ##### Fault injection for stand-alone testing #############
    #
    # Both settings are read as module globals at POST time, on the sender
    # thread, so rebinding them here reaches that thread without having to
    # restart the service. They are toggles rather than send-and-restore
    # options on purpose: the sender posts asynchronously off a private
    # queue with no way to await completion, so restoring straight after a
    # send would race the POST it is meant to affect.
    REAL_SERVER_URL = RMS_SERVER_URL
    REAL_SERVER_KEY = RMS_SERVER_KEY

    # RFC 5737 TEST-NET-1, guaranteed to be routed nowhere, so a POST runs
    # into CONNECT_TIMEOUT rather than being refused. That is the shape of a
    # server that has gone away, as opposed to one that is up and saying no.
    # A full cycle costs MAX_RETRIES connect timeouts plus backoff, so about
    # 20 seconds; swap in "http://127.0.0.1:9/" for an immediate refusal,
    # which takes the same code path far quicker but skips the timeouts.
    UNREACHABLE_URL = "http://192.0.2.1/"

    # A key RMS will not accept, so it answers 401. The server is up and
    # refusing the request, which is the case 3xx/4xx handling treats as
    # final: no retry and no incident published.
    INVALID_SERVER_KEY = "invalid-key-for-standalone-testing"

    def toggle_unreachable() -> None:
        """
        Point the service at an unroutable address, or back at RMS.

        Exercises the retryable path: MAX_RETRIES attempts with backoff,
        then the failure logged and RMS_POST_FAILED published, which is
        reported on the bus but not posted.
        """
        global RMS_SERVER_URL       # pylint: disable=global-statement

        if RMS_SERVER_URL == REAL_SERVER_URL:
            RMS_SERVER_URL = UNREACHABLE_URL
            print(f"\n{YELLOW}Simulating an unreachable RMS: POSTs now go to {UNREACHABLE_URL}{NC}")
            print("Send with 1, 2 or 3 and watch the full retry cycle, then the")
            print("outage incident, which is published to the bus but never posted.\n")
        else:
            RMS_SERVER_URL = REAL_SERVER_URL
            print("\nRestored the real RMS address\n")

    def toggle_rejecting() -> None:
        """
        Send an invalid API key, or restore the real one.

        Exercises the final-failure path: RMS answers 401, which is logged
        and not retried. No incident is published, since posting one would
        only be rejected in turn.
        """
        global RMS_SERVER_KEY       # pylint: disable=global-statement

        if RMS_SERVER_KEY == REAL_SERVER_KEY:
            RMS_SERVER_KEY = INVALID_SERVER_KEY
            print(f"\n{YELLOW}Simulating rejection: POSTs now carry an invalid API key{NC}")
            print("Send with 1, 2 or 3. Expect one attempt only, no retries and no")
            print("incident.\n")
        else:
            RMS_SERVER_KEY = REAL_SERVER_KEY
            print("\nRestored the real API key\n")

    # More incidents than the queue holds, with enough margin that the drop
    # path is still reached if the sender drains one or two while the loop runs
    FLOOD_COUNT = SEND_QUEUE_SIZE + 8

    def flood_incidents(service: RMService) -> None:
        """
        Submit more incidents at once than the send queue can hold.

        Exercises the overflow path: the sender posts one message at a time,
        so past SEND_QUEUE_SIZE submit() drops the newest with a warning
        rather than queueing it or making the caller wait.

        Pair this with option 8. Against a live server the sender drains
        between submissions and the queue may never reach its limit, while a
        POST that has to time out holds the sender still long enough for the
        backlog to build.

        Args:
            service: The running RMService to submit through.
        """
        # TEST_SOURCE rather than RMS_SOURCE matters here as well as in
        # option 3: incidents that are dropped before the POST would empty
        # the queue as fast as this fills it and never reach the limit.
        print(f"\nSubmitting {FLOOD_COUNT} incidents into a queue of {SEND_QUEUE_SIZE}...")

        for number in range(1, FLOOD_COUNT + 1):
            service.send_message(
                INCIDENT, IncidentMessage(TEST_SOURCE, f"Flood test incident {number}")
            )

        # Reaching into the sender is fair game here: there is no public view
        # of the backlog, and its depth is the whole point of the test
        handler = service._handler          # pylint: disable=protected-access

        if handler is None:
            print(f"{YELLOW}RMS service is not started; nothing was queued{NC}\n")
            return

        queued = handler._sender._jobs.qsize()      # pylint: disable=protected-access
        print(f"Queue depth now {queued} of {SEND_QUEUE_SIZE}")

        if queued == 0:
            print(f"{YELLOW}Nothing queued: send_message() drops everything while WiFi is down{NC}\n")
        else:
            print(f"Expect around {FLOOD_COUNT - SEND_QUEUE_SIZE} 'RMS send queue full' "
                  "warnings in the log\n")

    # Pylint allows more than 12 branches here because this is a test menu
    def interactive_menu() -> None:     #pylint: disable=too-many-branches
        """
        Run an interactive command-line menu for manual RMService testing.

        Creates a WifiService and RMService instance, then
        presents a numbered menu that lets a developer exercise each public
        method without running the full Oradio application stack.
        """
        input_selection = (
            "Select a function, input the number.\n"
            " 0-Quit\n"
            " 1-Test sending HEARTBEAT message\n"
            " 2-Test sending SYS_INFO message\n"
            " 3-Test sending INCIDENT message\n"
            " 4-Start heartbeat timer\n"
            " 5-Stop heartbeat timer\n"
            " 6-Connect to wifi\n"
            " 7-Disconnect wifi\n"
            " 8-Toggle simulated unreachable RMS\n"
            " 9-Toggle simulated rejected requests\n"
            f"10-Flood the send queue with {FLOOD_COUNT} incidents\n"
        )

        def menu_prompt() -> str:
            """
            Return the menu text with the current test state appended.

            Rebuilt each pass so which faults are injected can be read off
            without having to send anything to find out.
            """
            faults = []
            if RMS_SERVER_URL != REAL_SERVER_URL:
                faults.append("unreachable")
            if RMS_SERVER_KEY != REAL_SERVER_KEY:
                faults.append("rejecting")

            state = (
                f"{YELLOW}Simulating: {', '.join(faults)}{NC}" if faults
                else "Simulating: nothing, talking to the real RMS"
            )
            return f"{input_selection}[{state}]\nSelect: "

        # Create the wifi service interface
        wifi_service = WifiService()
        wifi_service.start()

        # Instantiate and start RMS service
        rms = RMService()
        rms.start()

        # User command loop
        while True:
            test_choice = input_prompt(menu_prompt(), int, -1)
            match test_choice:
                case 0:
                    rms.stop()
                    break
                case 1:
                    print("\nSend HEARTBEAT test message to Remote Monitoring Service...\n")
                    rms.send_message(HEARTBEAT)
                case 2:
                    print("\nSend SYS_INFO test message to Remote Monitoring Service...\n")
                    rms.send_message(SYS_INFO)
                case 3:
                    print("\nSend test INCIDENT message to Remote Monitoring Service...\n")
                    rms.send_message(INCIDENT, IncidentMessage(TEST_SOURCE, "Test incident from interactive menu"))
                case 4:
                    print("\nStarting heartbeat timer...\n")
                    Heartbeat.start_heartbeat(HEARTBEAT_REPEAT, rms.send_message, args=(HEARTBEAT,))
                case 5:
                    print("\nStop heartbeat timer...\n")
                    Heartbeat.stop_heartbeat()
                case 6:
                    name = input("Enter SSID of the network to add: ")
                    pswrd = input("Enter password for the network to add (empty for open network): ")
                    if name:
                        wifi_service.wifi_connect(name, pswrd)
                        print(f"\nConnecting with '{name}'. Check messages for result\n")
                    else:
                        print(f"\n{YELLOW}No network given{NC}\n")
                case 7:
                    print("\nDisconnecting wifi...\n")
                    wifi_service.wifi_disconnect()
                case 8:
                    toggle_unreachable()
                case 9:
                    toggle_rejecting()
                case 10:
                    flood_incidents(rms)
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
