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
    started and a one-time SYS_INFO message containing hardware and
    software information is sent. The heartbeat stops when WiFi is lost.

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
    request. What is attached is bounded per file and in total, and a log
    that outgrew the per-file limit is sent as its tail rather than in
    full. If logrotate rotates a log out from under a send, the POST still
    completes cleanly and the logs are then sent a second time, from the
    rotated files, so the server ends up with the complete content.

    Helper functions collect Raspberry Pi telemetry and software version
    information. Outgoing POST requests are protected by a simple
    exponential backoff retry mechanism. A failing POST marks the server
    unreachable, after which each message makes a single probe attempt and
    logs one line until a POST succeeds or WiFi connects.
"""
import re
import json
import uuid
import subprocess
from time import sleep
from pathlib import Path
from collections.abc import Callable
from threading import Timer, Event
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
# The total has no counterpart on the server and is a client-side choice: two full-size files,
# enough for a runaway log and the generation it just rotated into.
# The count matches PHP's max_file_uploads (20): a request carrying more than that has its
# extra files ignored server-side, so they would cost upload time and arrive nowhere.
# Under the standard logrotate policy (250k, rotate 1) real logs sit far below all three, so
# the limits only bite when something is filling a log fast -- which is when an incident is raised.
MAX_UPLOAD_FILE_BYTES  =  3 * 1024 * 1024   # Per attached file; under FileHelper::MAX_FILE_BYTES
MAX_UPLOAD_TOTAL_BYTES =  6 * 1024 * 1024   # All attachments in one POST
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

# Rejects a filename that cannot be placed in a MIME header as-is. Log names never contain these;
# a stray file in the log directory might.
UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f"\\\x7f]')

##### Send queue ##########################################
# Depth of the queue between send_message() and the sender thread. Deep enough to absorb a burst
# of incidents while one POST is in flight, capped so an unreachable server cannot grow it without
# bound: past this, the newest message is dropped with a warning rather than queued forever.
SEND_QUEUE_SIZE = 32

##### RMS reachability state ##############################

class _RmsReachability:
    """
    Cached view of whether the RMS server is reachable.

    Cleared when a POST exhausts its retries, set again on the first
    successful POST and when WiFi connects. While cleared, a POST makes a
    single probe attempt rather than the full retry/backoff cycle, and
    RMS's own incidents are dropped rather than POSTed.

    Never instantiated; use the classmethods.

    Attributes:
        reachable: Whether the server is believed to be reachable.
        lock: Serialises access to reachable across the heartbeat timer
            thread, the WiFi handler thread, and any caller of
            send_message().
    """
    reachable = True
    lock = Lock()

    @classmethod
    def is_reachable(cls) -> bool:
        """
        Return whether the RMS server is believed to be reachable.

        Returns:
            bool: The current reachability state.
        """
        with cls.lock:
            return cls.reachable

    @classmethod
    def update(cls, reachable: bool) -> bool:
        """
        Set the reachability state and report whether it changed.

        The test and the assignment share one lock, so concurrent callers
        cannot both observe the same transition.

        Args:
            reachable: The new state.

        Returns:
            bool: True if this call changed the state, False if it already
            held that value. Callers log and publish on the transition
            only, keeping a prolonged outage to one line per message.
        """
        with cls.lock:
            changed = cls.reachable != reachable
            cls.reachable = reachable
        return changed

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

def _handle_response_command(response: Response) -> None:
    """
    Execute a command returned by the RMS server, if there was one.

    RMS clears a pending command as soon as it hands it out, so it is
    delivered exactly once: a command that is not run here is not offered
    again on the next heartbeat.

    Warning:
        Executing commands received from a remote system is inherently
        risky and should eventually be replaced by validated commands
        handled elsewhere.

    Args:
        response: The successful response returned by _post_with_retry().
    """
    command = _extract_command(response)

    if command is not None:
        # Pass command to linux shell for execution
        oradio_log.debug("Run command '%s' from RMS server", command)
        try:
            # executable must be set explicitly; without it Python falls
            # back to /bin/sh which may lack bash-specific features.
            # text=True decodes stdout/stderr to str for readable logging.
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                check=True,
                executable="/usr/bin/bash",
                text=True,
            )
            oradio_log.debug("shell script result:\n%s", result.stdout)
        except subprocess.CalledProcessError as ex_err:
            oradio_log.error(
                "shell script '%s' exit code: %d\nOutput:\n%s\nError:\n%s",
                command, ex_err.returncode, ex_err.stdout, ex_err.stderr
            )

def _mark_reachable() -> None:
    """
    Record that the RMS server answered, logging only the transition.

    Called both when a POST succeeds and when it is rejected with a 4xx:
    either way the server replied, so it is reachable.
    """
    if _RmsReachability.update(True):
        oradio_log.info("RMS server reachable again")

def _mark_unreachable(context: str, failure: str) -> None:
    """
    Record that a POST exhausted its attempts and report the outage once.

    The state is cleared before the incident is published, so
    send_message() recognises the incident published here as
    undeliverable and drops it instead of starting another POST.

    Args:
        context: Short label used in log messages, e.g. "message".
        failure: Description of the last failure, for the log line.
    """
    if _RmsReachability.update(False):
        oradio_log.error("Failed to POST %s: %s", context, failure)
        Incidents.publish(IncidentMessage(RMS_SOURCE, RMS_POST_FAILED))
    else:
        # Outage already reported: one line per message
        oradio_log.error("Failed to POST %s: RMS server still unreachable", context)

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

def _collect_log_files(only_bases: set[str] | None = None) -> list[tuple[Path, int, int]]:
    """
    Choose which log files to attach, and which part of each one.

    Only "<name>.log" and its numbered rotations "<name>.log.1" and so on
    are considered; anything else in the directory is ignored.

    Files are considered newest first, so the log that was being written
    when the incident happened gets the budget before older rotations do,
    and is the last to be cut off by the file count limit. A file larger
    than what is left of the budget is attached as its tail: the end of a
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
                candidates.append((stats.st_mtime, stats.st_size, path))
    except OSError as ex_err:
        oradio_log.error("Could not list log files in '%s': %s", ORADIO_LOG_PATH, ex_err)
        return []

    candidates.sort(reverse=True)   # Newest first

    selected: list[tuple[Path, int, int]] = []
    budget = MAX_UPLOAD_TOTAL_BYTES

    for _, size, path in candidates:
        if budget <= 0 or len(selected) >= MAX_UPLOAD_FILES:
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

    Reading straight from the logs also means no copy of them exists
    anywhere: file content passes through a COPY_CHUNK_BYTES buffer on its
    way to the socket and is never accumulated, so memory use is flat and
    independent of how large the logs have grown -- which is what the
    files= argument of requests gets wrong, encoding the whole body in
    memory (and then copying it).

    requests recognises this as a stream and takes Content-Length from the
    len attribute, the same way it does for requests_toolbelt's
    MultipartEncoder, so the request is sent normally rather than with
    chunked transfer encoding, which not every PHP setup accepts.

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

def _attempt_post(data, headers: dict, context: str) -> tuple[Response | None, str | None]:
    """
    Make one POST attempt and classify the outcome.

    Owns everything that means "the server answered": marking it reachable
    and, for a 4xx, logging the rejection. The caller is left with the one
    decision that is its own, namely whether to try again.

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
            (None, None) when the server rejected the request, which is
            final: retrying sends the identical request.
    """
    try:
        response = post(
            url=RMS_SERVER_URL,
            headers=headers,
            data=data,
            timeout=(CONNECT_TIMEOUT, POST_TIMEOUT)
        )
    except (RequestException, Timeout, OSError) as ex_err:
        # Fall back to the class name: some requests exceptions carry
        # an empty message, which would log a failure with no reason
        return None, str(ex_err) or type(ex_err).__name__

    if 400 <= response.status_code < 500:
        # The server answered, so it is reachable; the request itself is
        # what it refused. Recorded with the status code because the fix
        # differs per code, and reported back as final: no retry, and no
        # incident published.
        oradio_log.error(
            "POST %s rejected: HTTP %d, body: %s",
            context, response.status_code, response.text[:200] or "<none>"
        )
        _mark_reachable()
        return None, None

    if response.status_code >= 500:
        # Server-side and possibly transient, so treated like a transport
        # failure and retried.
        return None, f"HTTP {response.status_code}"

    _mark_reachable()
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

    Failures are split the way the crash action script splits them, since
    the two classes call for different responses:

    - 4xx means the server answered and rejected this request. Retrying
      sends the identical request and earns the identical rejection, so
      there is no retry and the reachability state is left alone: 401 (key
      rotated) and 413 (payload too large) are configuration faults, not an
      outage. No incident is published either -- publishing one would POST
      an incident that is rejected in turn, publishing another.
    - 5xx and transport errors (DNS, TLS, timeout) may clear on their own,
      so these retry with backoff and, once exhausted, clear the
      reachability state and publish RMS_POST_FAILED.

    Retries apply while _RmsReachability reports the server as reachable.
    Once it does not, each call makes a single probe attempt, so a dead
    server costs one timeout rather than the full retry and backoff cycle
    while recovery is still picked up on the next message.

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
    attempts = MAX_RETRIES if _RmsReachability.is_reachable() else 1
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

        _mark_unreachable(context, failure)
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
        generated: Timestamp of the moment send_message() was called, not
            of the moment the POST happens: a message that waits behind a
            slow send still reports when its event occurred.
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
    its retries. Without it a single unreachable POST blocks its caller for
    the whole retry cycle -- and its callers are the incident bus worker,
    the WiFi message worker and the heartbeat timer, none of which can
    afford to stall for a minute and a half.

    One message is posted at a time, in submission order. That keeps the
    peak cost of RMS traffic to a single in-flight request and means the
    reachability state is only ever driven by one thread.

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

        This is the work that used to run on the caller's thread. The
        telemetry helpers below shell out to vcgencmd, lsb_release and the
        like, so they are called here rather than at submit time.

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

            # An incident about RMS can only be delivered while RMS answers.
            # Dropping it here keeps a failed POST from publishing an
            # incident that triggers another POST; the failure is already in
            # the local log.
            if job.incident.source == RMS_SOURCE and not _RmsReachability.is_reachable():
                oradio_log.debug("RMS unreachable; not reporting: %s", job.incident.message)
                return

            payload_info['source']  = job.incident.source
            payload_info['message'] = job.incident.message
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
    caller of send_message() waits for the network: a WIFI_CONNECTED event
    is processed in microseconds rather than held for the length of a
    SYS_INFO POST.
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
            # A new connection may resolve an earlier failure, so allow the
            # next POST a full retry cycle rather than a single probe
            _RmsReachability.update(True)
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
        reports an IncidentMessage from another service, attaching the
        current log files for context. Note that those are the logs as they
        are when the sender gets to the message, which for a queued message
        is not exactly the moment the incident was raised.

        Only a HEARTBEAT response is inspected for a pending command: that
        is the one message type RMS attaches one to, so parsing any other
        response for it would never find anything.

        Only queued while WiFi is currently known to be connected; if not,
        nothing is sent and a debug line is logged instead, since attempting
        a POST with no network would just burn through the full retry/backoff
        cycle before failing anyway.

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
        # when its event happened and not when the sender got to it.
        self._sender.submit(
            _SendJob(
                msg_type=msg_type,
                generated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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

    def interactive_menu() -> None:
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
            "Select: "
        )

        # Create the wifi service interface
        wifi_service = WifiService()
        wifi_service.start()

        # Instantiate and start RMS service
        rms = RMService()
        rms.start()

        # User command loop
        while True:
            test_choice = input_prompt(input_selection, int, -1)
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
                    print("\nSend test INCIDENT message to Remote Monitoring Service...\n")
                    rms.send_message(INCIDENT, IncidentMessage("rms_service.py:0", "Test incident from interactive menu"))
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
                case _:
                    print(f"\n{YELLOW}Please input a valid number{NC}\n")

    print("\nStarting test program...\n")

    # Present menu with tests
    interactive_menu()

    print("\nExiting test program...\n")

    # Restore temporarily disabled pylint duplicate code check
    # pylint: enable=duplicate-code
