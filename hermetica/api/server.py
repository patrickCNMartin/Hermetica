# -----------------------------------------------------------------------------#
# THE TRANSPORT — HTTP/JSON over the two ports
# -----------------------------------------------------------------------------#
"""A thin HTTP layer: a route table, one error mapping, and nothing else. Every
rule lives in the ports; this file only turns requests into calls and results
into JSON. `openapi.json` documents exactly the routes below — a test holds the
two to the same set.

No authentication. Inside Docker Compose, listen on 0.0.0.0 and never publish
the port: the network is the only guard until auth exists."""

import json
import os
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

from api import pipelines, query
from api.contract import InvalidRequestError
from compose.compose import (
    NodeMismatchError,
    PipelineCycleError,
    UnresolvedProtocolError,
    UnsealedProtocolError,
)
from compose.store import SCHEMA as PIPELINE_SCHEMA
from compose.store import InactivePipelineError
from seal.seal import DuplicatedIdError
from utils.store import MissingHash, initialize_db

# -----------------------------------------------------------------------------#
# CONSTANTS
# -----------------------------------------------------------------------------#
OPENAPI = Path(__file__).parent / "openapi.json"

# Request bodies are templates and hash lists — kilobytes. Anything near this is
# not a real request.
MAX_BODY = 1024 * 1024

# Which error means what to the caller. Anything not here is a 500.
NOT_FOUND = (
    MissingHash,
    query.UnknownProtocolError,
    pipelines.UnknownPipelineError,
    InactivePipelineError,
)
UNPROCESSABLE = (
    InvalidRequestError,
    UnsealedProtocolError,
    UnresolvedProtocolError,
    NodeMismatchError,
    PipelineCycleError,
    DuplicatedIdError,
)

# ponytail: one lock for every write; enough for the single API instance the
# deploy runs. Per-pipeline locks only if concurrent saves ever queue visibly.
WRITES = threading.Lock()


# -----------------------------------------------------------------------------#
# ERRORS THE TRANSPORT OWNS
# -----------------------------------------------------------------------------#
class HttpError(Exception):
    """A request refused before any port is called."""

    def __init__(self, status: HTTPStatus, message: str):
        self.status, self.message = status, message
        super().__init__(message)


# -----------------------------------------------------------------------------#
# ROUTES — each takes (path params, parsed body, protocol_db, compose_db)
# -----------------------------------------------------------------------------#
def json_object(body) -> dict:
    """A route that needs a body gets a JSON object or a refusal."""
    if body is None:
        raise HttpError(HTTPStatus.BAD_REQUEST, "this route needs a JSON body")
    if not isinstance(body, dict):
        raise InvalidRequestError(["the body must be a JSON object"])
    return body


def list_protocols(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, query.list_protocols(protocol_db)


def protocol_versions(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, query.protocol_versions(protocol_db, params["protocol_uid"])


def get_protocol(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, query.get_protocol(protocol_db, params["hash"])


def build_lock(params, body, protocol_db, compose_db):
    body = json_object(body)
    with_bodies = body.get("with_bodies", True)
    if not isinstance(with_bodies, bool):
        raise InvalidRequestError(["`with_bodies` must be true or false"])
    return HTTPStatus.OK, query.build_lock(protocol_db, body.get("hashes"), with_bodies)


def list_pipelines(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, pipelines.list_pipelines(compose_db, protocol_db)


def get_pipeline(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, pipelines.get_pipeline(
        compose_db, protocol_db, params["guid"]
    )


def save_pipeline(params, body, protocol_db, compose_db):
    """POST creates (201); PUT versions an existing template (200)."""
    payload = json_object(body)
    with WRITES:
        saved = pipelines.save_pipeline(
            compose_db, protocol_db, payload, params.get("guid")
        )
    status = HTTPStatus.CREATED if saved["status"] == "new" else HTTPStatus.OK
    return status, saved


def retire_pipeline(params, body, protocol_db, compose_db):
    with WRITES:
        return HTTPStatus.OK, pipelines.retire(compose_db, params["guid"])


def pipeline_versions(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, pipelines.pipeline_versions(compose_db, params["guid"])


def export_lock(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, pipelines.export_lock(compose_db, protocol_db, params["guid"])


def openapi(params, body, protocol_db, compose_db):
    return HTTPStatus.OK, json.loads(OPENAPI.read_text(encoding="utf-8"))


ROUTES = {
    ("GET", "/protocols"): list_protocols,
    ("GET", "/protocols/{protocol_uid}/versions"): protocol_versions,
    ("GET", "/protocol-versions/{hash}"): get_protocol,
    ("POST", "/locks"): build_lock,
    ("GET", "/pipelines"): list_pipelines,
    ("POST", "/pipelines"): save_pipeline,
    ("GET", "/pipelines/{guid}"): get_pipeline,
    ("PUT", "/pipelines/{guid}"): save_pipeline,
    ("DELETE", "/pipelines/{guid}"): retire_pipeline,
    ("GET", "/pipelines/{guid}/versions"): pipeline_versions,
    ("POST", "/pipelines/{guid}/lock"): export_lock,
    ("GET", "/openapi.json"): openapi,
}


def match(template: str, path: str) -> dict | None:
    """Path parameters if `path` fits `template`, else None. Segments are
    percent-decoded, so a `protocol_uid` may arrive as `protocols_io%3A1`."""
    want, got = template.strip("/").split("/"), path.strip("/").split("/")
    if len(want) != len(got):
        return None
    params = {}
    for expected, actual in zip(want, got):
        if expected.startswith("{"):
            if not actual:
                return None
            params[expected[1:-1]] = unquote(actual)
        elif expected != actual:
            return None
    return params


# -----------------------------------------------------------------------------#
# DISPATCH — everything but the socket, so it tests without one
# -----------------------------------------------------------------------------#
def error_body(error: Exception) -> dict:
    """An error as the caller sees it: its name, its message, and the data it
    was raised with — `nodes`, `problems`, `missing` — never a traceback."""
    return {"error": type(error).__name__, "message": str(error), **vars(error)}


def dispatch(
    method: str, target: str, body: bytes | None, protocol_db: str, compose_db: str
) -> tuple[HTTPStatus, object]:
    """One request in, one (status, JSON-ready payload) out."""
    path = urlsplit(target).path
    try:
        found = [
            (route_method, params, handler)
            for (route_method, template), handler in ROUTES.items()
            if (params := match(template, path)) is not None
        ]
        if not found:
            raise HttpError(HTTPStatus.NOT_FOUND, f"no route {path}")
        chosen = [(params, handler) for m, params, handler in found if m == method]
        if not chosen:
            allowed = sorted({m for m, _, _ in found})
            raise HttpError(
                HTTPStatus.METHOD_NOT_ALLOWED,
                f"{method} not allowed on {path}; use {', '.join(allowed)}",
            )
        params, handler = chosen[0]
        try:
            parsed = json.loads(body) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HttpError(HTTPStatus.BAD_REQUEST, f"body is not JSON: {error}")
        return handler(params, parsed, protocol_db, compose_db)

    except HttpError as error:
        return error.status, {"error": type(error).__name__, "message": error.message}
    except NOT_FOUND as error:
        return HTTPStatus.NOT_FOUND, error_body(error)
    except UNPROCESSABLE as error:
        return HTTPStatus.UNPROCESSABLE_ENTITY, error_body(error)
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "error": "InternalError",
            "message": f"{type(error).__name__} — see the server log",
        }


# -----------------------------------------------------------------------------#
# THE SOCKET
# -----------------------------------------------------------------------------#
def make_handler(protocol_db: str, compose_db: str) -> type[BaseHTTPRequestHandler]:
    """A request handler bound to these two stores."""

    class Handler(BaseHTTPRequestHandler):
        def handle_any(self):
            body = None
            # HTTP/1.1: no Content-Length and no Transfer-Encoding means no body
            # (`curl -X POST .../lock`). A chunked body has no length to cap, so
            # it is refused rather than read unbounded.
            if "Transfer-Encoding" in self.headers:
                return self.reply(
                    HTTPStatus.LENGTH_REQUIRED,
                    {
                        "error": "HttpError",
                        "message": "send Content-Length, not chunks",
                    },
                )
            length = self.headers.get("Content-Length")
            if length is not None:
                if not length.isdigit():
                    return self.reply(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "HttpError",
                            "message": "Content-Length is not a size",
                        },
                    )
                if int(length) > MAX_BODY:
                    return self.reply(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        {
                            "error": "HttpError",
                            "message": f"body over {MAX_BODY} bytes",
                        },
                    )
                body = self.rfile.read(int(length))
            self.reply(
                *dispatch(self.command, self.path, body, protocol_db, compose_db)
            )

        def reply(self, status: HTTPStatus, payload) -> None:
            # default=str: an error attribute JSON cannot encode still reaches
            # the caller as text instead of failing the reply.
            data = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = handle_any

    return Handler


def serve(
    host: str, port: int, protocol_db: str, compose_db: str
) -> ThreadingHTTPServer:
    """Build the server. The protocol store must already exist — only the pull
    creates it; the template store is created here if it is missing."""
    if not Path(protocol_db).is_file():
        raise FileNotFoundError(
            f"no protocol store at {protocol_db} — run a pull first "
            "(python -m chronos.chronos)"
        )
    initialize_db(compose_db, PIPELINE_SCHEMA)
    return ThreadingHTTPServer((host, port), make_handler(protocol_db, compose_db))


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    load_dotenv(dotenv_path=Path.cwd() / "env" / ".env")
    db_dir = os.getenv("DB", "db")
    # 127.0.0.1 unless told otherwise; Docker Compose sets API_HOST=0.0.0.0.
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8080"))

    server = serve(host, port, f"{db_dir}/chronos.db", f"{db_dir}/compose.db")
    print(f"Hermetica API on http://{host}:{port} — docs at /openapi.json")
    server.serve_forever()
