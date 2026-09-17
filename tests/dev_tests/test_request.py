# -----------------------------------------------------------------------------#
# TESTS — talking to protocols.io
# -----------------------------------------------------------------------------#
"""The by-ID fetch and the throttled client underneath every call. The pager
and the workspace sweep are tested in test_workspace.py."""

import pytest
import requests
import responses

from sources.protocols_io.client import call_api, fetch_protocol

BASE_URL = "https://api.example.org"
HEADERS = {"Authorization": "Bearer test-token"}
ANY_URL = f"{BASE_URL}/v4/anything"
PROTOCOL_URL = f"{BASE_URL}/v4/protocols/"


# -----------------------------------------------------------------------------#
# 1. THE BY-ID STAGE
# -----------------------------------------------------------------------------#
class TestFetchProtocol:
    @responses.activate
    def test_unwraps_the_payload(self, record):
        raw = record("baseline")
        responses.add(
            responses.GET,
            f"{PROTOCOL_URL}{raw['id']}",
            json={"payload": raw, "status_code": 0},
        )

        assert fetch_protocol(raw["id"], PROTOCOL_URL, HEADERS) == raw

    @responses.activate
    def test_missing_payload_is_empty(self):
        """No payload key -> nothing, rather than a KeyError deep in the walk."""
        responses.add(responses.GET, f"{PROTOCOL_URL}1", json={"status_code": 1})

        assert fetch_protocol(1, PROTOCOL_URL, HEADERS) == []

    @responses.activate
    def test_the_id_is_appended_to_the_url(self):
        responses.add(responses.GET, f"{PROTOCOL_URL}12345", json={"payload": {}})

        fetch_protocol(12345, PROTOCOL_URL, HEADERS)

        assert responses.calls[0].request.url.endswith("/12345")


# -----------------------------------------------------------------------------#
# 2. THROTTLING AND RETRY
# -----------------------------------------------------------------------------#
class TestCallApi:
    @responses.activate
    def test_raises_for_status(self):
        """Fail loudly — never let an error body through as data."""
        responses.add(responses.GET, ANY_URL, json={"error": "nope"}, status=404)

        with pytest.raises(requests.exceptions.HTTPError):
            call_api(ANY_URL, HEADERS)

    @responses.activate
    def test_client_errors_are_not_retried(self):
        """A 400 will still be a 400 five attempts later — give up immediately."""
        responses.add(responses.GET, ANY_URL, json={}, status=400)

        with pytest.raises(requests.exceptions.HTTPError):
            call_api(ANY_URL, HEADERS)

        assert len(responses.calls) == 1

    @responses.activate
    def test_server_errors_are_retried(self, monkeypatch):
        """A 5xx is transient — back off and try again rather than lose the pull."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        responses.add(responses.GET, ANY_URL, json={}, status=503)
        responses.add(responses.GET, ANY_URL, json={"items": []}, status=200)

        assert call_api(ANY_URL, HEADERS).status_code == 200
        assert len(responses.calls) == 2

    @responses.activate
    def test_rate_limited_requests_are_retried(self, monkeypatch):
        """429 is the one 4xx worth retrying — it means slow down, not stop."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        responses.add(responses.GET, ANY_URL, json={}, status=429)
        responses.add(responses.GET, ANY_URL, json={"items": []}, status=200)

        assert call_api(ANY_URL, HEADERS).status_code == 200
        assert len(responses.calls) == 2

    @responses.activate
    def test_headers_are_forwarded(self):
        responses.add(responses.GET, ANY_URL, json={"items": []})

        call_api(ANY_URL, HEADERS)

        assert responses.calls[0].request.headers["Authorization"] == (
            "Bearer test-token"
        )
