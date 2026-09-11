#!/usr/bin/env python3
"""

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####

@copyright:     Copyright 2025, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@summary:
    Unit tests for rms_service.

    Nothing here touches the network, the real log directory, or the real
    message bus: requests.post, ORADIO_LOG_PATH, subprocess.run and the
    Incidents bus are all patched, so the suite is safe to run on a
    developer machine and on a Pi alike.

    Run with:
        python3 -m unittest test_rms_service -v
"""
import os
import email
import time
import unittest
from pathlib import Path
from datetime import datetime
from typing import ClassVar
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch, MagicMock

from requests import Response

import rms_service
from messaging import LED_SOURCE
from rms_service import (
    HEARTBEAT,
    SYS_INFO,
    INCIDENT,
    MAX_ROTATION_SENDS,
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    ESSENTIAL_LOG_BASE,
    SEND_QUEUE_SIZE,
    TRUNCATION_NOTE,
    _MultipartBody,
    _RmsSender,
    _SendJob,
    _build_multipart_body,
    _collect_log_files,
    _get_temperature,
    _handle_response_command,
    _log_base_name,
    _log_rotation_index,
    _post_message,
    _selection_order,
)

UNSUPPORTED = "Unsupported platform"

# Message type used only to drive the sender's queue in tests. It reaches
# delivery like any other message but matches no branch there, so it is
# never posted and cannot pollute what a test is asserting on.
DRAIN_MARKER = "__DRAIN_MARKER__"


class FakeResponse:
    """Stands in for a requests.Response, carrying only what the code reads."""

    def __init__(self, status_code=201, text='{"success":true}', body=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        # body=False means "not JSON at all", as a captive portal would answer
        self._body = None if body is False else (body or {"success": True, "data": {}})

    def json(self):
        """Return the decoded body, or raise as requests does for non-JSON."""
        if self._body is None:
            raise ValueError("no JSON object could be decoded")
        return self._body


class RmsTestCase(unittest.TestCase):
    """Base fixture: an isolated log directory with the network stubbed out."""

    def setUp(self):
        # enter_context ties the directory to this test rather than to a
        # with-block, which a fixture cannot hold open across setUp/tearDown
        # pylint: disable=consider-using-with; enterContext registers the
        # cleanup, which is what a with-block would do and a fixture cannot.
        self.logs = Path(self.enterContext(TemporaryDirectory()))

        patcher = patch.object(rms_service, "ORADIO_LOG_PATH", self.logs)
        patcher.start()
        self.addCleanup(patcher.stop)

        # ROTATION_SETTLE_DELAY would otherwise add seconds to every test
        # that drives a rotation resend. It is the only wait in the send
        # path, so patching it out costs nothing else.
        sleep_patcher = patch.object(rms_service, "sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

        # Several tests drive error paths on purpose, so the log lines they
        # produce are noise rather than signal. Patched here so a real
        # failure still stands out in the test output.
        log_patcher = patch.object(rms_service, "oradio_log")
        # The mock itself, not rms_service.oradio_log: reading the patched
        # attribute back off the module asks static analysis to know about a
        # substitution made at runtime, which it cannot.
        self.log = log_patcher.start()
        self.addCleanup(log_patcher.stop)

        # Keep RMS_POST_FAILED off the real incident bus
        incidents_patcher = patch.object(rms_service, "Incidents")
        incidents_patcher.start()
        self.addCleanup(incidents_patcher.stop)

    def warnings_logged(self):
        """Return the warning lines produced, with their arguments filled in."""
        return [call.args[0] % call.args[1:]
                for call in self.log.warning.call_args_list]

    def write_log(self, name, content):
        """Create a log file and return its path."""
        path = self.logs / name
        path.write_bytes(content)
        return path

    @staticmethod
    def parse_body(body, content_type):
        """
        Read a multipart body back into its fields and files.

        Returns:
            tuple[dict, dict, list]: fields by name, file content by
            filename, and any MIME defects found.
        """
        raw = body.read(-1)
        message = email.message_from_bytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
        )

        fields, files = {}, {}

        for part in message.get_payload():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_param("filename", header="content-disposition")
            payload = part.get_payload(decode=True)

            if filename:
                files[filename] = payload
            else:
                fields[name] = payload.decode()

        return fields, files, list(message.defects)


class TestLogBaseName(unittest.TestCase):
    """A log and its rotations must reduce to one shared name."""

    def test_current_and_rotated_share_a_base(self):
        """Every generation of one log reduces to the same base name."""
        for name in ("oradio.log", "oradio.log.1", "oradio.log.12"):
            self.assertEqual(_log_base_name(name), "oradio")

    def test_dotted_name_keeps_its_dots(self):
        """Only the .log suffix is stripped, not everything after the first dot."""
        self.assertEqual(_log_base_name("my.app.log.7"), "my.app")

    def test_non_log_name_is_returned_unchanged(self):
        """A name that is not a log has no base to reduce to."""
        self.assertEqual(_log_base_name("notes.txt"), "notes.txt")


class TestCollectLogFiles(RmsTestCase):
    """Selection of what gets attached, and how much of it."""

    def selected_names(self, only_bases=None):
        """Return just the filenames chosen, in attachment order."""
        return [path.name for path, _, _ in _collect_log_files(only_bases)]

    def test_only_log_and_numbered_rotations_are_picked_up(self):
        """Compressed, dated and non-log names are ignored."""
        for name in ("oradio.log", "oradio.log.1", "oradio.log.12",
                     "oradio.log.gz", "oradio.log.1.gz", "oradio.log-20260822",
                     "oradio.logfile", "notes.txt"):
            self.write_log(name, b"data\n")

        self.assertEqual(sorted(self.selected_names()),
                         ["oradio.log", "oradio.log.1", "oradio.log.12"])

    def test_essential_log_outranks_a_newer_unrelated_log(self):
        """
        oradio.log holds the lines that explain the incident.

        Another service writing a bigger, newer log into the same directory
        must not take the budget ahead of it.
        """
        for name, age in (("mpd.log", 5000), ("oradio.log", 1000)):
            os.utime(self.write_log(name, b"x" * 2000), (age, age))

        self.assertEqual(self.selected_names()[0], "oradio.log")

    def test_current_generation_outranks_its_rotation_on_an_mtime_tie(self):
        """
        logrotate copies a log aside and truncates it within the same second.

        Both files then carry the same mtime and the rotated one is the
        larger, so any size-based tie-break would rank it first.
        """
        os.utime(self.write_log("oradio.log.1", b"x" * 9000), (7000, 7000))
        os.utime(self.write_log("oradio.log", b"x" * 100), (7000, 7000))

        self.assertEqual(self.selected_names()[0], "oradio.log")

    def test_rotation_index_orders_the_generations(self):
        """Current is 0, and higher means older, as logrotate numbers them."""
        self.assertEqual(_log_rotation_index("oradio.log"), 0)
        self.assertEqual(_log_rotation_index("oradio.log.1"), 1)
        self.assertEqual(_log_rotation_index("oradio.log.12"), 12)

    def test_selection_order_puts_the_essential_log_in_the_first_rank(self):
        """The first key decides on base name alone, before any timestamp."""
        essential = _selection_order((Path(f"{ESSENTIAL_LOG_BASE}.log"), 0, 0))
        other = _selection_order((Path("other.log"), 9_999_999_999, 0))

        self.assertLess(essential[0], other[0])

    def test_empty_files_and_missing_directory_yield_nothing(self):
        """A zero-length log has nothing to say and is left out."""
        self.write_log("oradio.log", b"")
        self.assertEqual(self.selected_names(), [])

    def test_newest_file_is_offered_the_budget_first(self):
        """The log being written when the incident happened comes first."""
        old = self.write_log("oradio.log.1", b"old\n")
        new = self.write_log("oradio.log", b"new\n")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        self.assertEqual(self.selected_names()[0], "oradio.log")

    def test_small_file_is_sent_whole(self):
        """A file inside the cap is attached from offset zero."""
        self.write_log("oradio.log", b"x" * 500)
        (_, offset, length), = _collect_log_files()

        self.assertEqual((offset, length), (0, 500))

    def test_limits_stay_within_what_the_server_accepts(self):
        """
        Anything above a server limit is uploaded and then discarded.

        FileHelper::MAX_FILE_BYTES is 5 MB per file and PHP's
        max_file_uploads is 20 per request; sending more spends the
        device's upload time on data that arrives nowhere.
        """
        self.assertLessEqual(MAX_UPLOAD_FILE_BYTES, 5 * 1024 * 1024)
        self.assertLessEqual(MAX_UPLOAD_FILES, 20)
        self.assertLessEqual(MAX_UPLOAD_FILE_BYTES, MAX_UPLOAD_TOTAL_BYTES)

    def test_files_left_out_for_want_of_budget_are_named(self):
        """
        A log that does not fit must say so.

        Without this line an omitted log is indistinguishable from a lost
        one, which is exactly the wrong impression to leave in a record
        somebody is reading to diagnose a fault.
        """
        with patch.object(rms_service, "MAX_UPLOAD_FILE_BYTES", 1000), \
             patch.object(rms_service, "MAX_UPLOAD_TOTAL_BYTES", 1500):
            for index, name in enumerate(("new.log", "middle.log", "old.log")):
                path = self.write_log(name, b"x" * 1000)
                os.utime(path, (3000 - index, 3000 - index))

            _collect_log_files()

        omission = [line for line in self.warnings_logged() if "Not attaching" in line]

        self.assertEqual(len(omission), 1, "the omission is reported once, not per file")
        self.assertIn("old.log", omission[0], "the omitted file must be named")
        self.assertIn("upload budget spent", omission[0], "the reason must be given")
        self.assertNotIn("new.log", omission[0], "an attached file is not an omission")

    def test_files_left_out_by_the_file_limit_say_so(self):
        """The count running out is reported differently from the budget running out."""
        with patch.object(rms_service, "MAX_UPLOAD_FILES", 2):
            for index in range(4):
                self.write_log(f"log{index}.log", b"small\n")

            _collect_log_files()

        omission = [line for line in self.warnings_logged() if "Not attaching" in line]

        self.assertEqual(len(omission), 1)
        self.assertIn("limit of 2 files reached", omission[0])
        self.assertIn("2 older file(s)", omission[0])

    def test_nothing_is_reported_when_everything_fits(self):
        """The normal case stays quiet."""
        self.write_log("oradio.log", b"small\n")
        self.write_log("oradio.log.1", b"small\n")

        _collect_log_files()

        self.assertEqual([line for line in self.warnings_logged() if "Not attaching" in line], [])

    def test_no_more_files_are_attached_than_the_server_will_take(self):
        """Many small logs are cut off at MAX_UPLOAD_FILES, not at the byte budget."""
        for index in range(MAX_UPLOAD_FILES + 5):
            self.write_log(f"log{index:02d}.log", b"small\n")

        self.assertEqual(len(_collect_log_files()), MAX_UPLOAD_FILES)

    def test_newest_files_survive_the_file_count_cutoff(self):
        """When the count runs out, it is the oldest logs that are left behind."""
        for index in range(MAX_UPLOAD_FILES + 3):
            path = self.write_log(f"log{index:02d}.log", b"small\n")
            os.utime(path, (1000 + index, 1000 + index))

        kept = {path.name for path, _, _ in _collect_log_files()}

        self.assertIn(f"log{MAX_UPLOAD_FILES + 2:02d}.log", kept, "newest must be kept")
        self.assertNotIn("log00.log", kept, "oldest must be dropped")

    def test_oversized_file_is_sent_as_its_tail(self):
        """Only the last MAX_UPLOAD_FILE_BYTES are attached, from the end."""
        # Scaled down so the test does not write megabytes of scratch data;
        # what matters is the arithmetic, not the size of the numbers.
        with patch.object(rms_service, "MAX_UPLOAD_FILE_BYTES", 1000), \
             patch.object(rms_service, "MAX_UPLOAD_TOTAL_BYTES", 4000):
            self.write_log("oradio.log", b"x" * 1500)
            (_, offset, length), = _collect_log_files()

        self.assertEqual(length, 1000)
        self.assertEqual(offset, 500, "the tail is taken from the end of the file")

    def test_total_budget_is_not_exceeded(self):
        """Across all files, no more than MAX_UPLOAD_TOTAL_BYTES is selected."""
        with patch.object(rms_service, "MAX_UPLOAD_FILE_BYTES", 1000), \
             patch.object(rms_service, "MAX_UPLOAD_TOTAL_BYTES", 2500):
            for index in range(6):
                self.write_log(f"log{index}.log", b"x" * 1000)

            total = sum(length for _, _, length in _collect_log_files())

        self.assertLessEqual(total, 2500)
        self.assertGreater(total, 0, "the budget must still allow some files")

    def test_only_bases_limits_the_selection_to_one_family(self):
        """A resend repeats one log and its rotations, and nothing else."""
        for name in ("oradio.log", "oradio.log.1", "mpd.log", "mpd.log.1"):
            self.write_log(name, b"data\n")

        self.assertEqual(sorted(self.selected_names({"oradio"})),
                         ["oradio.log", "oradio.log.1"])
        self.assertEqual(self.selected_names({"absent"}), [])


class TestMultipartBody(RmsTestCase):
    """The body must send exactly the size it announces, whatever the logs do."""

    FIELDS: ClassVar[dict] = {"serial": "abc123", "type": INCIDENT}

    def build(self):
        """Build a body over whatever is currently in the log directory."""
        prepared = _build_multipart_body(self.FIELDS)
        self.assertIsNotNone(prepared, "expected a body to be built")
        return prepared

    def test_no_logs_means_no_body(self):
        """With nothing to attach the caller posts the plain fields instead."""
        self.assertIsNone(_build_multipart_body(self.FIELDS))

    def test_body_is_well_formed_and_matches_its_declared_length(self):
        """Fields and files survive a round trip, with no MIME defects."""
        self.write_log("oradio.log", b"line one\nline two\n")
        body, content_type = self.build()
        declared = body.len

        fields, files, defects = self.parse_body(body, content_type)

        self.assertEqual(defects, [])
        self.assertEqual(fields, {"serial": "abc123", "type": INCIDENT})
        self.assertEqual(files["oradio.log"], b"line one\nline two\n")
        self.assertEqual(declared, body.len)

    def test_read_returns_exactly_len_bytes_in_total(self):
        """Chunked reads add up to the announced size and then stop."""
        self.write_log("oradio.log", b"y" * 200_000)
        body, _ = self.build()

        produced = 0
        while True:
            chunk = body.read(4096)
            if not chunk:
                break
            produced += len(chunk)

        self.assertEqual(produced, body.len)

    def test_log_growing_after_measurement_is_cut_at_the_agreed_length(self):
        """Extra bytes written mid-send are never read, so nothing over-sends."""
        path = self.write_log("oradio.log", b"a" * 1000)
        body, content_type = self.build()
        declared = body.len

        # The service keeps logging while the body is being sent
        with path.open("ab") as handle:
            handle.write(b"b" * 500_000)

        fields, files, defects = self.parse_body(body, content_type)

        self.assertEqual(defects, [])
        self.assertEqual(len(files["oradio.log"]), 1000)
        self.assertNotIn(b"b", files["oradio.log"])
        self.assertEqual(declared, body.len)
        self.assertEqual(body.shrunk, set())
        self.assertIn("serial", fields)

    def test_log_truncated_mid_send_is_padded_and_flagged(self):
        """copytruncate leaves the body short, so it pads and reports the base name."""
        path = self.write_log("oradio.log", b"c" * 100_000)
        body, content_type = self.build()
        declared = body.len

        # What logrotate's copytruncate does: same inode, size zero
        with path.open("r+b") as handle:
            handle.truncate(0)

        _, files, defects = self.parse_body(body, content_type)

        self.assertEqual(defects, [])
        self.assertEqual(len(files["oradio.log"]), 100_000, "padding must fill the gap")
        self.assertIn(TRUNCATION_NOTE.strip(), files["oradio.log"])
        self.assertEqual(body.shrunk, {"oradio"})
        self.assertEqual(declared, body.len)

    def test_deleted_log_is_padded_rather_than_dropped(self):
        """A file that vanishes mid-send still owes its announced bytes."""
        path = self.write_log("oradio.log", b"d" * 50_000)
        body, content_type = self.build()

        path.unlink()

        _, files, defects = self.parse_body(body, content_type)

        self.assertEqual(defects, [])
        self.assertEqual(len(files["oradio.log"]), 50_000)
        self.assertEqual(body.shrunk, {"oradio"})


class TestPostMessage(RmsTestCase):
    """Send policy, response classification, and the abort signal."""

    PAYLOAD: ClassVar[dict] = {"serial": "abc123", "type": HEARTBEAT}

    def test_success_returns_the_response(self):
        """A message that is accepted costs one POST and returns its response."""
        response = FakeResponse()

        with patch.object(rms_service, "post", return_value=response) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIs(result, response)
        self.assertEqual(post_mock.call_count, 1)

    def test_client_error_is_reported_without_an_incident(self):
        """A 4xx is the server refusing this request; sending it again cannot help."""
        with patch.object(rms_service, "post",
                          return_value=FakeResponse(status_code=413)) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1)

    def test_rejection_publishes_no_incident(self):
        """
        A refused request must not become an incident.

        Publishing one would POST an incident that is refused in turn and
        publishes another, so a rotated key or an oversized payload would
        turn into a loop instead of a log line.
        """
        with patch.object(rms_service, "post",
                          return_value=FakeResponse(status_code=401)) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1, "the same key earns the same refusal")
        rms_service.Incidents.publish.assert_not_called()

    def test_server_error_is_posted_once_and_gives_up(self):
        """A 5xx is an outage, and an outage is not worth a second try."""
        with patch.object(rms_service, "post",
                          return_value=FakeResponse(status_code=503)) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1)

    def test_transport_error_is_posted_once_and_publishes_one_incident(self):
        """A failed POST is reported exactly once."""
        with patch.object(rms_service, "post",
                          side_effect=rms_service.RequestException("boom")) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(rms_service.Incidents.publish.call_count, 1)

    def test_each_send_costs_exactly_one_post(self):
        """
        No send is ever repeated, and no send is skipped either.

        The second half matters as much as the first: a message is sent on
        its own merits, and an earlier failure does not make the sender skip
        the next one.
        """
        with patch.object(rms_service, "post",
                          side_effect=rms_service.RequestException("down")) as post_mock:
            _post_message(self.PAYLOAD)
            _post_message(self.PAYLOAD)

        self.assertEqual(post_mock.call_count, 2)

    def test_abort_prevents_any_attempt(self):
        """A send started while stopping does not reach the network."""
        abort = Event()
        abort.set()

        with patch.object(rms_service, "post") as post_mock:
            result = _post_message(self.PAYLOAD, abort=abort)

        self.assertIsNone(result)
        post_mock.assert_not_called()

    def test_redirects_are_not_followed(self):
        """
        A redirected POST arrives without its body, so it must not be followed.

        requests converts a redirected POST into a GET and drops the body,
        then reports the final 200 -- which would look like a stored record.
        """
        with patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            _post_message(self.PAYLOAD)

        self.assertFalse(post_mock.call_args.kwargs["allow_redirects"])

    def test_redirect_is_reported_as_a_configuration_fault(self):
        """A redirect is a URL misconfiguration: final, and worth saying out loud."""
        redirect = FakeResponse(status_code=301,
                                headers={"Location": "https://rms.example/v1/records"})

        with patch.object(rms_service, "post", return_value=redirect) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result, "a redirect must not read as success")
        self.assertEqual(post_mock.call_count, 1, "the same request earns the same redirect")

        logged = " ".join(str(call) for call in self.log.error.call_args_list)
        self.assertIn("https://rms.example/v1/records", logged, "log where it points")

    def test_captive_portal_answering_with_200_is_not_delivery(self):
        """
        A 2xx only says something answered.

        A device on a WiFi network it has not signed into gets a good 200
        carrying a login page; counting that as a stored record loses the
        message with nothing said.
        """
        portal = FakeResponse(text="<html>Please sign in</html>", body=False)

        with patch.object(rms_service, "post", return_value=portal) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result, "a portal reply must not read as delivery")
        self.assertEqual(post_mock.call_count, 1)

    def test_json_from_something_other_than_rms_is_not_delivery(self):
        """A proxy or gateway answering in JSON still is not RMS."""
        with patch.object(rms_service, "post",
                          return_value=FakeResponse(body={"status": "ok"})) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1)

    def test_rms_reporting_failure_in_a_200_is_not_delivery(self):
        """RMS's own envelope says whether the record was taken."""
        refused = FakeResponse(body={"success": False, "message": "quota exceeded"})

        with patch.object(rms_service, "post", return_value=refused) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 1)

        logged = " ".join(str(call) for call in self.log.error.call_args_list)
        self.assertIn("quota exceeded", logged, "say what RMS objected to")

    def test_rms_envelope_is_accepted(self):
        """The reply RMS actually sends must pass on the first attempt."""
        stored = FakeResponse(body={"success": True, "data": {"stored": True}})

        with patch.object(rms_service, "post", return_value=stored) as post_mock:
            result = _post_message(self.PAYLOAD)

        self.assertIs(result, stored)
        self.assertEqual(post_mock.call_count, 1)

    def test_server_url_and_key_are_read_at_post_time(self):
        """
        Both are looked up when the request is built, not captured at import.

        The stand-alone fault-injection options work by rebinding these
        module globals to a dead address or a bad key. That only reaches the
        sender thread because the lookup happens per POST; binding either at
        import would leave the toggles silently doing nothing.
        """
        with patch.object(rms_service, "RMS_SERVER_URL", "https://elsewhere.example/api"), \
             patch.object(rms_service, "RMS_SERVER_KEY", "other-key"), \
             patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            _post_message(self.PAYLOAD)

        kwargs = post_mock.call_args.kwargs
        self.assertEqual(kwargs["url"], "https://elsewhere.example/api")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "other-key")

    def test_attached_logs_are_streamed_as_multipart(self):
        """With logs to attach, the request carries a multipart body, not form fields."""
        self.write_log("oradio.log", b"content\n")

        with patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            _post_message(self.PAYLOAD, attach_log_files=True)

        kwargs = post_mock.call_args.kwargs
        self.assertIsInstance(kwargs["data"], _MultipartBody)
        self.assertIn("multipart/form-data; boundary=", kwargs["headers"]["Content-Type"])


class TestRotationResend(RmsTestCase):
    """A log rotated mid-send is sent again, scoped and marked."""

    PAYLOAD: ClassVar[dict] = {"serial": "abc123", "type": INCIDENT, "message": "runaway"}

    @staticmethod
    def body_stub(shrunk):
        """Return a stand-in body reporting the given shrunk base names."""
        body = MagicMock(spec=_MultipartBody)
        body.shrunk = shrunk
        return body

    def test_shrunk_log_is_sent_a_second_time_scoped_and_marked(self):
        """The repeat carries resend=1 and only the family that was rotated."""
        builds = []

        def fake_build(payload_info, only_bases=None):
            builds.append((dict(payload_info), only_bases))
            # Rotation is seen on the first send only
            return self.body_stub({"oradio"} if len(builds) == 1 else set()), "multipart/x"

        with patch.object(rms_service, "_build_multipart_body", side_effect=fake_build), \
             patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            result = _post_message(self.PAYLOAD, attach_log_files=True, context="incident")

        self.assertIsNotNone(result)
        self.assertEqual(post_mock.call_count, 2, "the rotated send must be repeated")

        first_fields, first_bases = builds[0]
        second_fields, second_bases = builds[1]

        self.assertNotIn("resend", first_fields, "the first send is not a resend")
        self.assertIsNone(first_bases, "the first send carries every log")
        self.assertEqual(second_fields["resend"], 1)
        self.assertEqual(second_bases, {"oradio"}, "the repeat is scoped to the rotated log")

    def test_callers_payload_is_not_modified(self):
        """resend=1 goes on a copy; the dictionary the caller passed stays as it was."""
        payload = dict(self.PAYLOAD)

        with patch.object(rms_service, "_build_multipart_body",
                          return_value=(self.body_stub({"oradio"}), "multipart/x")), \
             patch.object(rms_service, "post", return_value=FakeResponse()):
            _post_message(payload, attach_log_files=True)

        self.assertEqual(payload, self.PAYLOAD)

    def test_repeated_rotation_stops_at_the_limit(self):
        """If every send is rotated, the padded copy is kept rather than looping."""
        with patch.object(rms_service, "_build_multipart_body",
                          return_value=(self.body_stub({"oradio"}), "multipart/x")), \
             patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            _post_message(self.PAYLOAD, attach_log_files=True)

        self.assertEqual(post_mock.call_count, MAX_ROTATION_SENDS)

    def test_no_resend_without_attachments(self):
        """A message with no logs has nothing that can rotate."""
        with patch.object(rms_service, "post", return_value=FakeResponse()) as post_mock:
            _post_message(self.PAYLOAD)

        self.assertEqual(post_mock.call_count, 1)


class TestGetTemperature(unittest.TestCase):
    """Either a valid reading or "Unsupported platform" - nothing else."""

    def run_with_output(self, stdout):
        """Return what _get_temperature() makes of the given vcgencmd output."""
        completed = MagicMock(stdout=stdout, returncode=0)

        with patch.object(rms_service.subprocess, "run", return_value=completed):
            return _get_temperature()

    def test_readings_of_every_magnitude_are_parsed(self):
        """One, two and three digit readings all come back as the number alone."""
        cases = {
            "temp=42.8'C": "42.8",
            "temp=8.4'C": "8.4",        # a cold boot
            "temp=100.0'C": "100.0",    # a thermal event
            "temp=-2.3'C": "-2.3",
        }

        for output, expected in cases.items():
            with self.subTest(output=output):
                self.assertEqual(self.run_with_output(output), expected)

    def test_output_without_a_number_is_unsupported(self):
        """No reading in the answer means no reading."""
        for output in ("", "not a temperature", "temp=abc'C"):
            with self.subTest(output=output):
                self.assertEqual(self.run_with_output(output), UNSUPPORTED)

    def test_missing_binary_is_unsupported(self):
        """Off a Pi there is no vcgencmd, which raises rather than returning a status."""
        with patch.object(rms_service.subprocess, "run",
                          side_effect=FileNotFoundError("vcgencmd")):
            self.assertEqual(_get_temperature(), UNSUPPORTED)

    def test_failing_call_is_unsupported(self):
        """A non-zero exit raises CalledProcessError thanks to check=True."""
        error = rms_service.subprocess.CalledProcessError(1, "vcgencmd")

        with patch.object(rms_service.subprocess, "run", side_effect=error):
            self.assertEqual(_get_temperature(), UNSUPPORTED)

    def test_hanging_call_is_unsupported(self):
        """A wedged vcgencmd must not hold the sender thread indefinitely."""
        error = rms_service.subprocess.TimeoutExpired("vcgencmd", 5)

        with patch.object(rms_service.subprocess, "run", side_effect=error):
            self.assertEqual(_get_temperature(), UNSUPPORTED)


class TestRemoteCommand(RmsTestCase):
    """
    A command from RMS must not be able to stall message delivery.

    Reaches into the module's private runners deliberately: what is being
    pinned down is how the command is started and where its output goes,
    which is exactly what those functions are.
    """
    # pylint: disable=protected-access

    @staticmethod
    def response_with(command):
        """Return a stand-in response carrying a pending command."""
        response = MagicMock(spec=Response)
        response.json.return_value = {"success": True, "data": {"command": command}}
        return response

    def test_command_does_not_run_on_the_calling_thread(self):
        """
        Delivery must not wait for the command.

        _handle_response_command() is called from the sender thread, which
        every message to RMS passes through: a command that blocks it stops
        all reporting for as long as it runs.
        """
        running = Event()
        finished = Event()
        release = Event()

        def blocking_run(_command):
            running.set()
            release.wait(1)
            finished.set()

        with patch.object(rms_service, "_run_remote_command", side_effect=blocking_run):
            _handle_response_command(self.response_with("sleep 300"))

            # Back here while the command is still going: had it run inline,
            # the call above could not have returned until it finished.
            self.assertTrue(running.wait(5), "the command should have started")
            self.assertFalse(finished.is_set(), "the caller waited for the command")

            release.set()
            self.assertTrue(finished.wait(5))

    def test_command_thread_does_not_outlive_the_process(self):
        """A command still running at shutdown must not keep the process alive."""
        with patch.object(rms_service, "Thread") as thread:
            _handle_response_command(self.response_with("echo hello"))

        self.assertEqual(thread.call_args.kwargs["target"], rms_service._run_remote_command)
        self.assertEqual(thread.call_args.kwargs["args"], ("echo hello",))
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_command_is_run_in_its_own_session(self):
        """
        A session of its own keeps the command off the service's own group.

        It is not what keeps the command alive across a restart of
        oradio.service -- systemd kills the whole cgroup for that, which is
        why a command that stops the service is sent detached instead.
        """
        with patch.object(rms_service.subprocess, "Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.wait.return_value = 0

            rms_service._run_remote_command("echo hello")

        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_command_runs_without_a_time_limit(self):
        """
        How long a command may take is the sender's business, not ours.

        wait() rather than communicate(timeout=...): there is no deadline to
        enforce and no pipe to drain, so there is nothing to time out.
        """
        with patch.object(rms_service.subprocess, "Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.wait.return_value = 0

            rms_service._run_remote_command("sleep 3600")

        process.wait.assert_called_once_with()
        process.communicate.assert_not_called()

    def test_command_output_is_not_read_into_this_process(self):
        """
        Reading the output would let a chatty command exhaust memory.

        The command is handed the log file itself, so its output costs disk
        rather than RAM. stderr shares the descriptor so the two interleave
        in the order they were written.
        """
        with patch.object(rms_service.subprocess, "Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.wait.return_value = 0

            rms_service._run_remote_command("echo hello")

        kwargs = popen.call_args.kwargs
        self.assertIsNot(kwargs["stdout"], rms_service.subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], rms_service.subprocess.STDOUT)

    def test_command_output_lands_in_the_command_log(self):
        """Both streams, and the exit code, are recorded for later reading."""
        with TemporaryDirectory() as folder:
            log = Path(folder) / "rms.log"

            with patch.object(rms_service, "REMOTE_COMMAND_LOG", log):
                rms_service._run_remote_command("echo out; echo err >&2; exit 3")

            recorded = log.read_text(encoding="utf-8")

        self.assertIn("out", recorded)
        self.assertIn("err", recorded)
        self.assertIn("exit code 3", recorded)

    def test_command_still_runs_when_its_log_cannot_be_opened(self):
        """
        A card that has gone read-only is when a repair matters most.

        The output is dropped rather than the command, so logging can never
        be the reason a device cannot be fixed remotely.
        """
        unwritable = Path("/nonexistent-directory-for-tests/rms.log")

        with patch.object(rms_service, "REMOTE_COMMAND_LOG", unwritable), \
             patch.object(rms_service.subprocess, "Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.wait.return_value = 0

            rms_service._run_remote_command("echo hello")

        popen.assert_called_once()
        self.assertEqual(popen.call_args.kwargs["stdout"], rms_service.subprocess.DEVNULL)

    def test_no_command_starts_no_thread(self):
        """The ordinary heartbeat response carries nothing to run."""
        response = MagicMock(spec=Response)
        response.json.return_value = {"success": True, "data": {"stored": True}}

        with patch.object(rms_service, "Thread") as thread:
            _handle_response_command(response)

        thread.assert_not_called()


class TestRmsSender(RmsTestCase):
    """The queue between send_message() and the network."""

    def setUp(self):
        super().setUp()
        self.wifi = True
        self.posted = []
        self.submitted = 0
        self.wifi_checks = 0

        # Record what reaches the network without going near it
        self.post_patcher = patch.object(
            rms_service, "_post_message",
            side_effect=lambda payload_info, **kwargs: self.posted.append(dict(payload_info))
        )
        self.post_patcher.start()
        self.addCleanup(self.post_patcher.stop)

        self.sender = _RmsSender("serial123", self.check_wifi)
        self.assertTrue(self.sender.safe_start())
        self.addCleanup(self.sender.stop)

    def check_wifi(self):
        """
        Stand in for the handler's WiFi state, counting the reads.

        The worker reads this once per message, at the start of delivering
        it, which is what drain() uses to tell when a message is done.
        """
        self.wifi_checks += 1
        return self.wifi

    def submit(self, job):
        """Submit a message and keep count of how many were accepted."""
        self.submitted += 1
        return self.sender.submit(job)

    def drain(self, timeout=5.0):
        """
        Block until everything submitted so far has been delivered.

        Works by submitting one more message and waiting for the worker to
        reach it: the queue is FIFO and drained by a single thread, so the
        marker being picked up proves the messages before it are finished.
        """
        self.submit(_SendJob(DRAIN_MARKER, "drain"))
        deadline = time.monotonic() + timeout

        while self.wifi_checks < self.submitted:
            if time.monotonic() > deadline:
                self.fail("sender did not drain in time")
            time.sleep(0.01)

    def wait_for(self, count, timeout=5.0):
        """Block until count messages have been posted."""
        deadline = time.monotonic() + timeout

        while len(self.posted) < count:
            if time.monotonic() > deadline:
                self.fail(f"expected {count} posts, saw {len(self.posted)}")
            time.sleep(0.01)

    def test_submit_returns_without_waiting_for_the_post(self):
        """The caller hands the message over and carries on."""
        self.assertTrue(self.submit(_SendJob(HEARTBEAT, "2026-01-01 00:00:00")))
        self.wait_for(1)

        self.assertEqual(self.posted[0]["type"], HEARTBEAT)
        self.assertEqual(self.posted[0]["serial"], "serial123")

    def test_messages_are_posted_in_submission_order(self):
        """One at a time, first in first out."""
        for index in range(5):
            self.submit(_SendJob(SYS_INFO, f"2026-01-01 00:00:0{index}"))

        self.wait_for(5)
        self.assertEqual([post["generated"] for post in self.posted],
                         [f"2026-01-01 00:00:0{index}" for index in range(5)])

    def test_full_queue_drops_rather_than_blocks(self):
        """A server that is not keeping up must not let the backlog grow without limit."""
        # Hold the worker on the first message so the queue fills up
        release = Event()
        self.post_patcher.stop()

        with patch.object(rms_service, "_post_message",
                          side_effect=lambda *a, **kw: release.wait(5)):
            accepted = [self.sender.submit(_SendJob(HEARTBEAT, "t"))
                        for _ in range(SEND_QUEUE_SIZE + 10)]
            release.set()

        self.post_patcher.start()

        self.assertTrue(any(accepted), "some messages must be queued")
        self.assertIn(False, accepted, "the queue must refuse once full")
        self.assertLessEqual(sum(accepted), SEND_QUEUE_SIZE + 1)

    def test_message_queued_before_wifi_dropped_is_not_posted(self):
        """WiFi is checked again just before the POST, not only at submit time."""
        self.wifi = False
        self.submit(_SendJob(HEARTBEAT, "2026-01-01 00:00:00"))
        self.drain()

        self.assertEqual(self.posted, [])

    def test_incident_carries_its_source_message_and_logs(self):
        """An INCIDENT posts the incident fields and asks for the logs."""
        incident = rms_service.IncidentMessage(LED_SOURCE, "something broke")

        with patch.object(rms_service, "_post_message") as post_mock:
            self.sender.submit(_SendJob(INCIDENT, "2026-01-01 00:00:00", incident))

            deadline = time.monotonic() + 5
            while not post_mock.call_count:
                if time.monotonic() > deadline:
                    self.fail("incident was not posted")
                time.sleep(0.01)

        payload = post_mock.call_args.args[0]
        self.assertEqual(payload["type"], INCIDENT)
        self.assertEqual(payload["message"], "something broke")
        self.assertTrue(post_mock.call_args.kwargs["attach_log_files"])

    def test_rms_own_incident_is_never_posted(self):
        """
        Reporting an RMS failure to RMS would only fail again.

        A failed POST publishes RMS_POST_FAILED, and posting that would fail
        in turn and publish another. Dropping it here is what stops the loop.
        """
        incident = rms_service.IncidentMessage(rms_service.RMS_SOURCE, "post failed")

        self.submit(_SendJob(INCIDENT, "2026-01-01 00:00:00", incident))
        self.drain()

        self.assertEqual(self.posted, [])

    def test_incident_details_are_posted(self):
        """The context captured where the incident was raised travels with it."""
        incident = rms_service.IncidentMessage(
            LED_SOURCE, "fault", details="Traceback (most recent call last): ..."
        )

        self.submit(_SendJob(INCIDENT, "2026-01-01 00:00:00", incident))
        self.drain()

        self.assertEqual(self.posted[0]["details"],
                         "Traceback (most recent call last): ...")

    def test_incident_without_details_omits_the_field(self):
        """An incident that suppressed its context posts no empty field."""
        incident = rms_service.IncidentMessage(LED_SOURCE, "fault", details="")

        self.submit(_SendJob(INCIDENT, "2026-01-01 00:00:00", incident))
        self.drain()

        self.assertNotIn("details", self.posted[0])

    def test_every_incident_carries_the_logs(self):
        """A repeating fault gets fresh logs with each report, not just the first."""
        attached = []
        self.post_patcher.stop()

        with patch.object(rms_service, "_post_message",
                          side_effect=lambda payload, **kwargs: attached.append(
                              kwargs.get("attach_log_files"))):
            for index in range(5):
                self.submit(_SendJob(INCIDENT, f"t{index}",
                                     rms_service.IncidentMessage(LED_SOURCE, "same fault")))
            self.drain()

        self.post_patcher.start()

        self.assertEqual(attached, [True] * 5)

    def test_queue_depth_is_readable(self):
        """
        The backlog can be measured through _jobs.

        The stand-alone flood option reads the depth this way because there
        is no public view of the queue. Renaming the attribute would leave
        that option broken with nothing to catch it, since code under
        __main__ is never imported by the tests.
        """
        # pylint: disable=protected-access; the point of the test is the
        # attribute the menu reaches for.
        self.assertEqual(self.sender._jobs.qsize(), 0)

    def test_stop_discards_the_backlog(self):
        """Shutdown is not held open by messages still waiting to go out."""
        for index in range(SEND_QUEUE_SIZE):
            self.sender.submit(_SendJob(HEARTBEAT, f"t{index}"))

        self.sender.stop()

        self.assertFalse(self.sender.is_alive())
        self.assertLess(len(self.posted), SEND_QUEUE_SIZE)


class TestWifiMessageHandlerSendMessage(RmsTestCase):
    """
    Validation and gating done on the caller's thread.

    The handler is built without __init__ so that send_message() can be
    exercised on its own, without a subscription queue, a worker thread or
    a sender behind it. That means setting the attributes __init__ would
    have set, hence the protected access below.
    """
    # pylint: disable=protected-access

    def setUp(self):
        super().setUp()

        # Exercise send_message() without starting the real worker threads
        self.handler = rms_service.WifiMessageHandler.__new__(rms_service.WifiMessageHandler)
        self.handler._serial = "serial123"
        self.handler._wifi_connected = True
        self.handler._sender = MagicMock()

    def submitted(self):
        """Return the job handed to the sender, or None if there was none."""
        if not self.handler._sender.submit.call_count:
            return None
        return self.handler._sender.submit.call_args.args[0]

    def test_message_is_queued_not_posted(self):
        """send_message() hands over and returns; it does not touch the network."""
        with patch.object(rms_service, "post") as post_mock:
            self.handler.send_message(HEARTBEAT)

        post_mock.assert_not_called()
        self.assertEqual(self.submitted().msg_type, HEARTBEAT)

    def test_timestamp_is_taken_when_the_event_happened(self):
        """generated reflects the call, not the moment the sender gets to it."""
        self.handler.send_message(SYS_INFO)

        # Trailing [+-]NNNN: every posted timestamp carries its UTC offset, so a
        # reader never has to know which zone the device was in.
        self.assertRegex(
            self.submitted().generated,
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}$"
        )

    def test_incident_reports_when_it_was_raised(self):
        """
        An incident carries its own timestamp, set where it was raised.

        By the time it reaches here it has crossed the incident bus and its
        queue, so the current time would report when RMS was told rather
        than when the fault happened.
        """
        raised = datetime(2026, 1, 2, 3, 4, 5)
        incident = rms_service.IncidentMessage(
            LED_SOURCE, "fault", timestamp=raised.timestamp()
        )

        self.handler.send_message(INCIDENT, incident)

        # Wall clock asserted separately from the offset. raised is a naive local
        # datetime, so the epoch round-trip gives those digits back whatever zone
        # the test runs in, while the offset itself is whatever that zone was --
        # +0000 on a CI box, +0100 on a device in winter. Pinning the whole string
        # would make this pass in one zone and fail in another.
        generated = self.submitted().generated
        self.assertTrue(
            generated.startswith("2026-01-02 03:04:05"),
            f"expected the time it was raised, got {generated}"
        )
        self.assertRegex(generated, r"[+-]\d{4}$")

    def test_every_periodic_message_is_queued(self):
        """A reconnect burst queues each heartbeat and system info it asks for."""
        for _ in range(5):
            self.handler.send_message(HEARTBEAT)
            self.handler.send_message(SYS_INFO)

        queued = [call.args[0].msg_type
                  for call in self.handler._sender.submit.call_args_list]

        self.assertEqual(queued, [HEARTBEAT, SYS_INFO] * 5)

    def test_incidents_are_never_rate_limited(self):
        """Every incident is queued, however fast they arrive."""
        for index in range(5):
            self.handler.send_message(INCIDENT,
                                      rms_service.IncidentMessage(LED_SOURCE, f"fault {index}"))

        self.assertEqual(self.handler._sender.submit.call_count, 5)

    def test_unknown_type_is_rejected(self):
        """An unsupported type is reported to whoever made the mistake."""
        self.handler.send_message("NOT_A_TYPE")

        self.assertIsNone(self.submitted())

    def test_incident_without_a_message_is_rejected(self):
        """INCIDENT needs an IncidentMessage to report."""
        self.handler.send_message(INCIDENT)

        self.assertIsNone(self.submitted())

    def test_nothing_is_queued_while_wifi_is_down(self):
        """With no network there is nothing to send and no point queueing."""
        self.handler._wifi_connected = False
        self.handler.send_message(HEARTBEAT)

        self.assertIsNone(self.submitted())


if __name__ == "__main__":
    unittest.main(verbosity=2)
