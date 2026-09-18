# HERMETICA

Protocol Version Control and Pipeline Composition. 

Protocols.io is great but it does have one slight little issue: it does not explicitely enforce version control. Protocol history is tracked and you can see differences but all of this is silent. Someone makes a change and doesn't tell you? You are now using a different protocol than the one you thought you were. A project gets parked for a year and the client comes back asking for the protocol you used for their Materials section? How certain are you that the version that is in your workspace is the one you actually used without manually checking the change history? 

Protocol versioning is based on human discipline which starts to break apart the moment your team is underpressure to deliver or they have to handle to many requests to keep track of what is going on upstream.

Hermetica provides a simple system to keep track of protocol changes by regularly checking upstream protocol and seeing if anything has changed. In addition and more importantly, Hermetica provide a `lock` file which tells you exactly which version of the protocol you used. No more guessing, you now have a contract that can be used 2 years down the line to get the exact protocol you used. 

In addition, protocols are more often than not part of a workflow which are composed of multiple protocols. Hermetica also tracks workflow composition and provide the workflow was part of the `lock` file. Not only do you know which protocols you used but also in which order.

NOTE: There is a protocols.io MCP server. Not sure what to do with this information at the moment but worth keeping in mind.

# Guardrails

## Agent Rules

Will add some agent rules and context for agentic development in the future. I am still refining those rules.

## Environments

The project works on a multi-tier level for development purposes. Currently, only option 1 and 3 are provided. 

1. `uv` only. All python dependencies are stated in the `pyproject.toml` and can be run using the `uv` / `venv` virtual environments. Certain system dependencies are required but that's on you to add them (`pandoc` for example). 

2. `docker` + `uv`. We will provide a `docker` container for this project which will contain all the python dependencies stated in the `pyproject.toml`. 

3. `nix` + `uv`. All code can be run in a `nix develop` shell which handles system dependencies and will install python dependencies with `uv`

4. `nix` + `OCI` + `uv`. The `nix flake` contains development shell instruction but also OCI image build instruction. Image can be built directly from a version pinned nix flake.

NOTE: We will also provide a `PIXI` approach in the future for those who prefer a conda like environment.

## Pre-commit hooks

This directory contains pre-commit hooks that will trigger on a push to main. It will run `ruff` (check and lint) and `detect-secrets`. `PyTest` does not currently run in the pre-commit hook but will run through GitHub Actions. It might be added in the future. The goal is to make sure that we are confident in the code that we push and shared before doing so. 


# Usage

Everything below assumes the `nix` + `uv` route (option 3 above). Either drop into the
shell once with `nix develop` and run the bare commands, or prefix each one with
`nix develop --command uv run`. The `make` targets already do the prefixing for you.

## Configuration

All configuration lives in `env/.env`, which is **not** tracked. Nothing has a default
that reaches the network, so a missing value fails loudly rather than guessing.

| variable | what it does |
|---|---|
| `SOURCES` | comma-separated adapters to pull, in order. Currently only `protocols_io` |
| `BASE_URL`, `API_KEY`, `WORKSPACE_ID` | the protocols.io endpoint and credentials |
| `PROTOCOL_URL`, `VIEW_URL` | link prefixes written into records and rendered pages |
| `DB` | where the two SQLite stores live. Defaults to `db` |
| `LOGS` | where the pull log and reports are written |
| `API_HOST`, `API_PORT` | what the API binds. Defaults to `127.0.0.1:8080` |
| `EMAIL`, `SMTP_*` | optional. Unset means no mail; empty `SMTP_HOST` drafts a `.eml` instead of sending |

## Filling the store

```sh
python -m chronos.chronos
```

The nightly pull: ask each source what exists, fetch every id, hash it, and write a new
version only where content actually changed. It creates `db/chronos.db` on first run —
**nothing else creates it**, and the API refuses to start without it. One source failing
does not stop the others, and a source that raises writes nothing, so no protocol is
deprecated just because a request timed out.

## Running the API

```sh
make api
```

Serves on `127.0.0.1:8080`. It opens `chronos.db` **read-only** and `compose.db`
read-write, creating the latter if missing. The first start inside `nix` takes ~15 s while
the shell is prepared.

**There is no authentication.** The network is the only guard: keep it bound to localhost,
or on an internal network with its port unpublished, until auth exists.

## Talking to the API

Every route speaks JSON. `GET /openapi.json` returns the full OpenAPI 3.1 document, which
is hand-maintained and held equal to the route table by a test — so it cannot silently
drift from what the server actually does. Point a client generator at it.

**Browsing protocols** — read-only, backed by `chronos.db`:

```sh
curl localhost:8080/protocols                       # every protocol's active version
curl localhost:8080/protocols/protocols_io:568614/versions   # one protocol's history
curl localhost:8080/protocol-versions/sha256%3Aabc...        # one version, body included
```

Note the `%3A` — a hash is `sha256:<hex>`, so the colon needs encoding in a path.

**Composing pipelines** — read-write, backed by `compose.db`. A template is the *shape* of
a pipeline: a DAG of node ids, and which protocol each node runs. It stores guids, never
hashes, because which version runs is decided at lock time, not at save time.

```sh
# create — the server mints the guid, you cannot choose one
curl -X POST localhost:8080/pipelines -H 'Content-Type: application/json' -d '{
  "title": "Lysate prep",
  "dag":   {"wash": ["lyse"], "lyse": []},
  "nodes": {"wash": "<protocol_guid>", "lyse": "<protocol_guid>"}
}'

curl localhost:8080/pipelines                    # every active template
curl localhost:8080/pipelines/<guid>             # one template, each node's live status
curl localhost:8080/pipelines/<guid>/versions    # every version it has held
curl -X PUT    localhost:8080/pipelines/<guid> -d '{...}'   # save a new version
curl -X DELETE localhost:8080/pipelines/<guid>              # retire; history is kept
```

**Exporting a lock** — the point of the whole thing:

```sh
curl -X POST localhost:8080/pipelines/<guid>/lock
curl -X POST localhost:8080/pipelines/<guid>/lock -d '{"with_bodies": false}'
```

This pins the protocol versions active *right now*, pins the pipeline's own shape, and
hands the document back. **Nothing is stored** — there is no lock table, and the lock
exists only on your side once you save it. Send no body for the full document; send
`{"with_bodies": false}` for a pins-only one. How much smaller depends on how long the
protocols are — on a four-step pipeline it measured 18x, 148 KB down to 8 KB.

A lock is refused if any step's protocol is no longer active — the response names the
offending nodes, so you can swap them and save before exporting. A guarantee you cannot
honour is worse than an error.

**Errors** are JSON and carry their own data, not just a message:

```json
{"error": "UnresolvedProtocolError", "message": "...", "nodes": ["lyse"]}
```

`404` means not found, `422` means the request was understood and refused, and `500`
carries the exception class only — the traceback goes to the server log, never the
response.

## Development

```sh
pytest                     # the suite
pytest tests/dev_tests/test_server.py -q        # one concern
ruff check . && ruff format .                   # lint and format
make audit                 # regenerate docs/status.md — size, tests, coverage per module
```

Tests never hit the live API; HTTP is mocked with `responses`, and the fixtures are
committed synthetic data with no real names in them. A test never writes into the repo
either — anything that writes a file copies into `tmp_path` first.

`make audit` rewrites `docs/status.md`, which is tracked. CI runs `make audit-check` and
fails if the committed numbers no longer match the code, so quote that file rather than
counting by hand.


# AI Use

Claude Opus 4.8/5 and Qwen3.8-27B.  