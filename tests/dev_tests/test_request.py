# -----------------------------------------------------------------------------#
# TESTS — the two-stage protocols.io pull
# -----------------------------------------------------------------------------#
"""Every assertion here cost a silent data loss. The pagination rules in
particular are load-bearing: an unstable order_field lost 22 of 51 protocols and
a 1-indexed page_id lost the first 10, both with the loop terminating normally."""

import pytest
import requests
import responses

from chronos.utils.request_utils import (
    FIRST_PAGE,
    IncompletePullError,
    _call_api,
    fetch_protocol,
    fetch_protocol_list,
)

BASE_URL = "https://api.example.org"
HEADERS = {"Authorization": "Bearer test-token"}
PROTOCOLS_URL = f"{BASE_URL}/v3/protocols"
PROTOCOL_URL = f"{BASE_URL}/v4/protocols/"


def paged(items, next_page, total, page_size=10):
    """A protocols.io-shaped response envelope."""
    return {
        "items": items,
        "pagination": {"next_page": next_page, "total_results": total,
                       "page_size": page_size},
        "status_code": 0,
    }


# -----------------------------------------------------------------------------#
# 1. HITTING THE SERVER
# -----------------------------------------------------------------------------#
class TestServerConnection:
    @responses.activate
    def test_request_is_made_to_the_endpoint(self, list_items):
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(1)})

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS)

        assert len(responses.calls) == 1
        assert responses.calls[0].request.url.startswith(PROTOCOLS_URL)
        assert result == [1]

    @responses.activate
    def test_the_list_stage_returns_ids_not_records(self, list_items):
        """Stage one asks for `fields=id`; the record comes from the by-ID call."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(3)})

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS)

        assert result == [1, 2, 3]
        assert all(isinstance(i, int) for i in result)

    @responses.activate
    def test_any_endpoint_can_be_targeted(self, list_items):
        """No path is baked in — a mirror or a different API version just works."""
        other = "https://mirror.example.org/v4/protocol-list"
        responses.add(responses.GET, other, json={"items": list_items(1)})

        assert fetch_protocol_list(other, HEADERS) == [1]
        assert responses.calls[0].request.url.startswith(other)

    @responses.activate
    def test_caller_params_are_sent(self):
        """Query params come from the caller, not from inside the function."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        fetch_protocol_list(
            PROTOCOLS_URL, HEADERS, filter="public", order_field="id",
            peer_reviewed=1,
        )

        url = responses.calls[0].request.url
        assert "filter=public" in url
        assert "order_field=id" in url
        assert "peer_reviewed=1" in url

    @responses.activate
    def test_no_params_are_invented(self):
        """A bare call sends pagination only — no hardcoded scope leaks in."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        fetch_protocol_list(PROTOCOLS_URL, HEADERS)

        assert set(responses.calls[0].request.params) == {"page_size", "page_id"}

    @responses.activate
    def test_start_page_is_caller_controlled(self):
        """page_id sets where the walk starts, so a pull can be resumed."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_id=7)

        assert "page_id=7" in responses.calls[0].request.url


# -----------------------------------------------------------------------------#
# 2. PAGES BEING PROCESSED
# -----------------------------------------------------------------------------#
class TestPagination:
    @responses.activate
    def test_walks_multiple_pages(self, list_items):
        """A full page (== page_size) triggers a next fetch; a short page stops."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(10)})
        responses.add(responses.GET, PROTOCOLS_URL,
                      json={"items": list_items(3, start=11)})

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 13
        # The second request advanced page_id (0-indexed: 0 then 1).
        assert "page_id=1" in responses.calls[1].request.url

    @responses.activate
    def test_stops_on_empty_page(self, list_items):
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(10)})
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 10

    @responses.activate
    def test_stops_at_max_page(self, list_items):
        """max_pull caps the number of pages fetched, regardless of the server."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(10)})

        result = fetch_protocol_list(
            PROTOCOLS_URL, HEADERS, page_size=10, max_pull=5
        )

        assert len(responses.calls) == 5
        assert len(result) == 50

    @responses.activate
    def test_default_has_no_ceiling(self, list_items):
        """max_pull=None walks until the server runs out, not to a fixed cap."""
        for _ in range(25):
            responses.add(responses.GET, PROTOCOLS_URL,
                          json={"items": list_items(10)})
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(1)})

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 26   # past the old 20-page ceiling
        assert len(result) == 251


# -----------------------------------------------------------------------------#
# 2b. THE SERVER'S OWN PAGINATION BLOCK
# -----------------------------------------------------------------------------#
class TestEnvelopeDrivenPagination:
    @responses.activate
    def test_walk_starts_at_page_zero(self, list_items):
        """page_id is 0-indexed upstream — starting at 1 skips ten protocols."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1), None, 1))

        fetch_protocol_list(PROTOCOLS_URL, HEADERS)

        assert f"page_id={FIRST_PAGE}" in responses.calls[0].request.url
        assert "page_id=0" in responses.calls[0].request.url

    @responses.activate
    def test_follows_next_page_not_page_length(self, list_items):
        """A full page with next_page=None ends the walk immediately."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(10), None, 10))

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 1   # would refetch on length alone
        assert len(result) == 10

    @responses.activate
    def test_continues_on_short_page_when_next_page_is_set(self, list_items):
        """Conversely, a short page is NOT the end if the server says otherwise."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1), "?page_id=1", 2))
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1, start=2), None, 2))

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 2

    @responses.activate
    def test_count_mismatch_retries_once_then_succeeds(self, list_items):
        """A transient short pull is retried whole, and the retry is trusted."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1), None, 2))     # 1 of 2 -> retry
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(2), None, 2))

        result = fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 2

    @responses.activate
    def test_persistent_mismatch_raises(self, list_items):
        """Refuse to hand back a pull the server says is incomplete.

        Better a failed cron run than a store where upstream looks like it
        deleted 60 protocols.
        """
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1), None, 61))

        with pytest.raises(IncompletePullError, match="61"):
            fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2   # original + exactly one retry

    @responses.activate
    def test_capped_walk_is_not_verified(self, list_items):
        """An intentional cap is partial by design — must not raise."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(10), "?page_id=1", 999))

        result = fetch_protocol_list(
            PROTOCOLS_URL, HEADERS, page_size=10, max_pull=1
        )
        assert len(result) == 10

    @responses.activate
    def test_resumed_walk_is_not_verified(self, list_items):
        """Starting mid-way is partial by design too."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(list_items(1), None, 999))

        result = fetch_protocol_list(
            PROTOCOLS_URL, HEADERS, page_size=10, page_id=5
        )
        assert len(result) == 1

    @responses.activate
    def test_endpoint_without_pagination_still_works(self, list_items):
        """Progressive enhancement: no pagination block -> short-page fallback."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": list_items(10)})
        responses.add(responses.GET, PROTOCOLS_URL,
                      json={"items": list_items(1, start=11)})

        assert len(fetch_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)) == 11


# -----------------------------------------------------------------------------#
# 3. THE BY-ID STAGE
# -----------------------------------------------------------------------------#
class TestFetchProtocol:
    @responses.activate
    def test_unwraps_the_payload(self, record):
        raw = record("baseline")
        responses.add(responses.GET, f"{PROTOCOL_URL}{raw['id']}",
                      json={"payload": raw, "status_code": 0})

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
# 4. THROTTLING AND RETRY
# -----------------------------------------------------------------------------#
class TestCallApi:
    @responses.activate
    def test_raises_for_status(self):
        """Fail loudly — never let an error body through as data."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"error": "nope"},
                      status=404)

        with pytest.raises(requests.exceptions.HTTPError):
            _call_api(PROTOCOLS_URL, HEADERS)

    @responses.activate
    def test_client_errors_are_not_retried(self):
        """A 400 will still be a 400 five attempts later — give up immediately."""
        responses.add(responses.GET, PROTOCOLS_URL, json={}, status=400)

        with pytest.raises(requests.exceptions.HTTPError):
            _call_api(PROTOCOLS_URL, HEADERS)

        assert len(responses.calls) == 1

    @responses.activate
    def test_server_errors_are_retried(self, monkeypatch):
        """A 5xx is transient — back off and try again rather than lose the pull."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        responses.add(responses.GET, PROTOCOLS_URL, json={}, status=503)
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []}, status=200)

        assert _call_api(PROTOCOLS_URL, HEADERS).status_code == 200
        assert len(responses.calls) == 2

    @responses.activate
    def test_rate_limited_requests_are_retried(self, monkeypatch):
        """429 is the one 4xx worth retrying — it means slow down, not stop."""
        monkeypatch.setattr("time.sleep", lambda _: None)
        responses.add(responses.GET, PROTOCOLS_URL, json={}, status=429)
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []}, status=200)

        assert _call_api(PROTOCOLS_URL, HEADERS).status_code == 200
        assert len(responses.calls) == 2

    @responses.activate
    def test_headers_are_forwarded(self):
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        _call_api(PROTOCOLS_URL, HEADERS)

        assert responses.calls[0].request.headers["Authorization"] == (
            "Bearer test-token"
        )
