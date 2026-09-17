# -----------------------------------------------------------------------------#
# TESTS — the HTTP transport
# -----------------------------------------------------------------------------#
"""The transport owns three things: which route runs, which status an error
becomes, and that the documented API is the served one. The ports' own rules are
tested in test_api.py; here they are only reached through a request."""

import copy
import http.client
import json
import re
import threading
from http import HTTPStatus
from urllib.parse import quote

import pytest

from api import server
from api.server import OPENAPI, ROUTES, dispatch, serve
from compose.store import SCHEMA as PIPELINE_SCHEMA
from seal.store import SCHEMA as PROTOCOL_SCHEMA
from seal.store import format_protocol_entry, write_protocols
from sources.protocols_io.artefact import build_protocol_artefact
from utils.dates import to_epoch
from utils.store import connect, initialize_db

WRITTEN_AT = to_epoch("2026-09-01")

# protocol_guids from the by-ID fixture: 568614 and 400843.
LYSE = "17B80155EBEA1E31DA8815594973DB85"  # pragma: allowlist secret
ELUTE = "5795146727160E82C86C56274B520277"  # pragma: allowlist secret


# -----------------------------------------------------------------------------#
# STORES AND REQUESTS
# -----------------------------------------------------------------------------#
@pytest.fixture
def stores(tmp_path, by_id_records):
    """(chronos.db holding the by-ID fixture, an empty compose.db)."""
    protocol_db = str(tmp_path / "chronos.db")
    initialize_db(protocol_db, PROTOCOL_SCHEMA)
    artefacts = [
        build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
    ]
    write_protocols(
        protocol_db, format_protocol_entry(artefacts, WRITTEN_AT), WRITTEN_AT
    )
    compose_db = str(tmp_path / "compose.db")
    initialize_db(compose_db, PIPELINE_SCHEMA)
    return protocol_db, compose_db


@pytest.fixture
def call(stores):
    """Send one request through dispatch: (status, payload)."""

    def _call(method, target, body=None):
        raw = body if isinstance(body, bytes) else None
        if body is not None and raw is None:
            raw = json.dumps(body).encode("utf-8")
        return dispatch(method, target, raw, *stores)

    return _call


def template(**overrides) -> dict:
    return {
        "title": "lyse_and_elute",
        "dag": {"lyse": ["elute"], "elute": []},
        "nodes": {"lyse": LYSE, "elute": ELUTE},
    } | overrides


# -----------------------------------------------------------------------------#
# 1. THE DOCUMENT IS THE API
# -----------------------------------------------------------------------------#
class TestOpenApi:
    @pytest.fixture
    def document(self):
        return json.loads(OPENAPI.read_text(encoding="utf-8"))

    def test_every_route_is_documented_and_nothing_else_is(self, document):
        """The docs cannot drift: add a route, and this fails until it is
        documented; document a route that does not exist, and this fails too."""
        documented = {
            (method.upper(), path)
            for path, operations in document["paths"].items()
            for method in operations
        }
        assert documented == set(ROUTES)

    def test_every_path_parameter_is_declared(self, document):
        for path, operations in document["paths"].items():
            in_path = set(re.findall(r"{(\w+)}", path))
            for method, operation in operations.items():
                declared = {
                    p["name"]
                    for p in operation.get("parameters", [])
                    if p["in"] == "path"
                }
                assert declared == in_path, f"{method.upper()} {path}"

    def test_every_operation_documents_its_success(self, document):
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                assert any(code.startswith("2") for code in operation["responses"]), (
                    f"{method.upper()} {path}"
                )

    def test_every_schema_reference_resolves(self, document):
        schemas = set(document["components"]["schemas"])
        refs = set(re.findall(r'"#/components/schemas/(\w+)"', json.dumps(document)))
        assert refs <= schemas

    def test_it_is_served(self, call, document):
        assert call("GET", "/openapi.json") == (HTTPStatus.OK, document)


# -----------------------------------------------------------------------------#
# 2. ROUTING
# -----------------------------------------------------------------------------#
class TestRouting:
    def test_an_unknown_path_is_404(self, call):
        status, payload = call("GET", "/nope")
        assert status == HTTPStatus.NOT_FOUND
        assert payload["error"] == "HttpError"

    def test_a_known_path_with_the_wrong_method_is_405_and_says_what_works(self, call):
        status, payload = call("PATCH", "/pipelines/abc")
        assert status == HTTPStatus.METHOD_NOT_ALLOWED
        assert "DELETE, GET, PUT" in payload["message"]

    def test_a_query_string_does_not_change_the_route(self, call):
        assert call("GET", "/protocols?x=1")[0] == HTTPStatus.OK

    def test_a_trailing_slash_is_the_same_route(self, call):
        assert call("GET", "/protocols/")[0] == HTTPStatus.OK

    def test_an_empty_path_segment_is_not_a_parameter(self, call):
        assert call("GET", "/pipelines//versions")[0] == HTTPStatus.NOT_FOUND

    def test_a_percent_encoded_uid_reaches_the_port_decoded(self, call):
        status, payload = call(
            "GET", f"/protocols/{quote('protocols_io:568614', safe='')}/versions"
        )
        assert status == HTTPStatus.OK
        assert payload["protocol_uid"] == "protocols_io:568614"


# -----------------------------------------------------------------------------#
# 3. BODIES
# -----------------------------------------------------------------------------#
class TestBodies:
    def test_a_body_that_is_not_json_is_400(self, call):
        status, payload = call("POST", "/pipelines", b"{not json")
        assert status == HTTPStatus.BAD_REQUEST
        assert "not JSON" in payload["message"]

    def test_a_body_that_is_not_utf8_is_400(self, call):
        assert call("POST", "/pipelines", b"\xff\xfe")[0] == HTTPStatus.BAD_REQUEST

    def test_a_missing_body_is_400(self, call):
        assert call("POST", "/pipelines")[0] == HTTPStatus.BAD_REQUEST

    def test_a_json_array_is_422(self, call):
        status, payload = call("POST", "/pipelines", ["nope"])
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert payload["problems"] == ["the body must be a JSON object"]

    def test_with_bodies_must_be_a_boolean(self, call):
        status, _ = call("POST", "/locks", {"hashes": ["sha256:a"], "with_bodies": 1})
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY


# -----------------------------------------------------------------------------#
# 4. THE QUERY PORT OVER HTTP
# -----------------------------------------------------------------------------#
class TestProtocolRoutes:
    def test_list(self, call):
        status, payload = call("GET", "/protocols")
        assert status == HTTPStatus.OK
        assert len(payload) == 7

    def test_one_version_by_hash_colon_encoded(self, call):
        digest = call("GET", "/protocols")[1][0]["hash"]
        status, payload = call("GET", f"/protocol-versions/{quote(digest, safe='')}")
        assert status == HTTPStatus.OK
        assert payload["hash"] == digest

    def test_an_unknown_hash_is_404_and_names_it(self, call):
        status, payload = call("GET", "/protocol-versions/sha256:nope")
        assert status == HTTPStatus.NOT_FOUND
        assert payload["error"] == "MissingHash"
        assert payload["missing"] == ["sha256:nope"]

    def test_an_unknown_uid_is_404(self, call):
        status, payload = call("GET", "/protocols/protocols_io:1/versions")
        assert status == HTTPStatus.NOT_FOUND
        assert payload["protocol_uid"] == "protocols_io:1"

    def test_build_a_lock(self, call):
        digest = call("GET", "/protocols")[1][0]["hash"]
        status, payload = call("POST", "/locks", {"hashes": [digest]})
        assert status == HTTPStatus.OK
        assert payload["manifest_hash"]

    def test_a_pins_only_lock_has_no_bodies(self, call):
        digest = call("GET", "/protocols")[1][0]["hash"]
        _, payload = call("POST", "/locks", {"hashes": [digest], "with_bodies": False})
        assert "bodies" not in payload


# -----------------------------------------------------------------------------#
# 5. THE COMPOSE PORT OVER HTTP
# -----------------------------------------------------------------------------#
class TestTemplateRoutes:
    def test_create_is_201(self, call):
        status, payload = call("POST", "/pipelines", template())
        assert status == HTTPStatus.CREATED
        assert payload["status"] == "new"

    def test_an_unchanged_put_is_200(self, call):
        guid = call("POST", "/pipelines", template())[1]["pipeline_guid"]
        status, payload = call("PUT", f"/pipelines/{guid}", template())
        assert (status, payload["status"]) == (HTTPStatus.OK, "unchanged")

    def test_a_put_to_a_guid_never_minted_is_404(self, call):
        status, payload = call("PUT", "/pipelines/nope", template())
        assert status == HTTPStatus.NOT_FOUND
        assert payload["guid"] == "nope"

    def test_a_malformed_template_is_422_with_every_problem(self, call):
        status, payload = call("POST", "/pipelines", {"dag": 1})
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert len(payload["problems"]) == 3

    def test_an_unsealed_guid_is_422_naming_the_node(self, call):
        status, payload = call(
            "POST", "/pipelines", template(nodes={"lyse": LYSE, "elute": "NOPE"})
        )
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert payload["nodes"] == {"elute": "NOPE"}

    def test_a_cycle_is_422_naming_it(self, call):
        status, payload = call(
            "POST", "/pipelines", template(dag={"lyse": ["elute"], "elute": ["lyse"]})
        )
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert payload["error"] == "PipelineCycleError"
        assert set(payload["cycle"]) >= {"lyse", "elute"}

    def test_read_list_and_versions(self, call):
        guid = call("POST", "/pipelines", template())[1]["pipeline_guid"]

        assert (
            call("GET", f"/pipelines/{guid}")[1]["nodes"]["lyse"]["status"] == "active"
        )
        assert [p["pipeline_guid"] for p in call("GET", "/pipelines")[1]] == [guid]
        assert len(call("GET", f"/pipelines/{guid}/versions")[1]["versions"]) == 1

    def test_retire_then_it_is_gone_from_the_list(self, call):
        guid = call("POST", "/pipelines", template())[1]["pipeline_guid"]

        assert call("DELETE", f"/pipelines/{guid}")[0] == HTTPStatus.OK
        assert call("GET", f"/pipelines/{guid}")[0] == HTTPStatus.NOT_FOUND
        assert call("GET", "/pipelines")[1] == []
        assert call("DELETE", f"/pipelines/{guid}")[0] == HTTPStatus.NOT_FOUND

    def test_export_a_lock(self, call):
        guid = call("POST", "/pipelines", template())[1]["pipeline_guid"]
        status, payload = call("POST", f"/pipelines/{guid}/lock")
        assert status == HTTPStatus.OK
        assert guid in payload["pipeline"]["entries"]

    def test_exporting_with_an_inactive_protocol_is_422_naming_the_node(
        self, call, stores
    ):
        guid = call("POST", "/pipelines", template())[1]["pipeline_guid"]
        with connect(stores[0]) as conn:
            conn.execute(
                "UPDATE protocol_history SET deprecated_at = ? "
                "WHERE protocol_uid = 'protocols_io:400843' AND deprecated_at IS NULL",
                (WRITTEN_AT + 1,),
            )
        status, payload = call("POST", f"/pipelines/{guid}/lock")
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert payload["nodes"] == {"elute": ELUTE}


# -----------------------------------------------------------------------------#
# 6. THE UNEXPECTED
# -----------------------------------------------------------------------------#
class TestUnexpectedErrors:
    def test_a_bug_is_500_and_leaks_no_detail(self, call, monkeypatch):
        def boom(*args):
            raise RuntimeError("internal path /srv/secret")

        monkeypatch.setattr(server.query, "list_protocols", boom)
        status, payload = call("GET", "/protocols")

        assert status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert payload["error"] == "InternalError"
        assert "/srv/secret" not in json.dumps(payload)


# -----------------------------------------------------------------------------#
# 7. THE SOCKET
# -----------------------------------------------------------------------------#
class TestServe:
    @pytest.fixture
    def running(self, stores):
        httpd = serve("127.0.0.1", 0, *stores)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield httpd.server_address
        httpd.shutdown()
        httpd.server_close()

    def request(self, address, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*address, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()
        finally:
            conn.close()

    def test_a_real_request_round_trips_as_json(self, running):
        status, kind, raw = self.request(running, "GET", "/protocols")
        assert status == HTTPStatus.OK
        assert kind == "application/json"
        assert len(json.loads(raw)) == 7

    def test_a_save_over_the_socket(self, running):
        body = json.dumps(template()).encode("utf-8")
        status, _, raw = self.request(
            running, "POST", "/pipelines", body, {"Content-Type": "application/json"}
        )
        assert status == HTTPStatus.CREATED
        assert json.loads(raw)["status"] == "new"

    def test_a_body_over_the_cap_is_refused_before_it_is_read(self, running):
        status, _, raw = self.request(
            running,
            "POST",
            "/pipelines",
            headers={"Content-Length": str(server.MAX_BODY + 1)},
        )
        assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert json.loads(raw)["error"] == "HttpError"

    def raw(self, address, method, path, headers):
        """A request with exactly these headers — http.client adds none itself."""
        conn = http.client.HTTPConnection(*address, timeout=5)
        try:
            conn.putrequest(method, path)
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_a_bodyless_post_needs_no_length(self, running):
        """What `curl -X POST .../lock` sends. The smoke test found this refused."""
        created = json.dumps(template()).encode("utf-8")
        _, _, raw = self.request(running, "POST", "/pipelines", created)
        guid = json.loads(raw)["pipeline_guid"]

        status, payload = self.raw(running, "POST", f"/pipelines/{guid}/lock", {})

        assert status == HTTPStatus.OK
        assert guid in payload["pipeline"]["entries"]

    def test_a_chunked_body_is_411(self, running):
        """No length means no cap — never read it."""
        status, _ = self.raw(
            running, "POST", "/pipelines", {"Transfer-Encoding": "chunked"}
        )
        assert status == HTTPStatus.LENGTH_REQUIRED

    @pytest.mark.parametrize("length", ["abc", "-1"])
    def test_a_length_that_is_not_a_size_is_400(self, running, length):
        status, _ = self.raw(running, "POST", "/pipelines", {"Content-Length": length})
        assert status == HTTPStatus.BAD_REQUEST

    def test_a_missing_protocol_store_refuses_to_start(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="run a pull first"):
            serve("127.0.0.1", 0, str(tmp_path / "none.db"), str(tmp_path / "c.db"))

    def test_the_template_store_is_created_if_missing(self, stores, tmp_path):
        fresh = tmp_path / "fresh_compose.db"
        httpd = serve("127.0.0.1", 0, stores[0], str(fresh))
        httpd.server_close()
        assert fresh.is_file()
