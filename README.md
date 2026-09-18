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

The project works on a multi-tier level for development purposes. All four are now provided. 

1. `uv` only. All python dependencies are stated in the `pyproject.toml` and can be run using the `uv` / `venv` virtual environments. Certain system dependencies are required but that's on you to add them (`pandoc` for example). 

2. `docker` + `uv`. The `Dockerfile` builds a lean image with the runtime dependencies resolved from `uv.lock`. See **Building a container image**. 

3. `nix` + `uv`. All code can be run in a `nix develop` shell which handles system dependencies and will install python dependencies with `uv`

4. `nix` + `OCI` + `uv`. The `nix flake` contains development shell instructions and an OCI image build (`nix build .#oci`), pinned by `flake.lock`. See **Building a container image**.

NOTE: A `PIXI` approach is also provided for those who prefer a conda like environment — see **The pixi environment**.

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


## Building a container image

Two paths to the same thing. **nix pins the whole closure** — C libraries included — and
is what should build a release. **The Dockerfile pins the Python packages** from
`uv.lock` and needs no nix knowledge, which makes it the one to reach for day to day and
the only one that builds on a Mac.

Both contain the API, its five runtime dependencies and the source, and nothing else.
Neither ships pandoc or TeX: `scribe` has no entry point yet and they cost about 500 MB.
Both serve the API by default and take a pull as a command override.

**Neither is ever tagged `latest`.** This project exists to make versions explicit; an
image named `latest` reintroduces exactly the problem it solves. The version comes from
`pyproject.toml` and nowhere else.

### With nix

```sh
nix build .#oci                  # this machine's architecture, Linux
nix build .#oci-x86_64-linux     # a typical server
nix build .#oci-aarch64-linux    # an arm server

docker load < result             # loads hermetica:0.1.0
```

Say nothing and you get your own architecture; name a target and you get that one. The
tag is read from `pyproject.toml`, so bumping the version there is the only edit.

**Every target is Linux**, whatever you build on. The flake builds from Linux nixpkgs even
when evaluated on darwin, so on a Mac without a Linux builder nix stops with:

```
error: Cannot build '/nix/store/...-hermetica.tar.gz.drv'.
       Required system: 'aarch64-linux'
```

That is correct, not broken — an image full of Mach-O binaries would be useless. Unlike
`docker buildx --platform`, there is no emulation to fall back on: buildx works because
the Docker VM is already Linux, and nix on darwin has no Linux kernel to run anything in.
Give it one, with **nix-darwin**:

```nix
nix.linux-builder.enable = true;
```

That is the whole setup. It runs a small NixOS VM as a launchd service, installs its key,
writes the SSH and `nix.conf` wiring for you, and survives reboots. Then:

```sh
nix build .#oci-x86_64-linux     # works on a Mac
```

The VM's disk is a sparse qcow2 capped at 20 GB — it starts around 200 KB and grows only
with what it actually builds, so the cap is a ceiling, not a cost.

A remote Linux machine works too, and is faster than a VM if you have one. In
`nix.settings.builders`, or `~/.config/nix/nix.conf` if you are not on nix-darwin:

```
builders = ssh-ng://you@buildhost x86_64-linux - 8 - big-parallel
```

The image is layered, so Python and its dependencies stay in one cached layer and the
source is its own — a code change re-pushes kilobytes.

### With docker

```sh
VERSION=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
docker build --build-arg VERSION=$VERSION -t hermetica:$VERSION .
```

Ordinary two-stage build, nothing nix-flavoured about it. The first stage installs
dependencies on their own cached layer, then installs the project with `--no-editable` so
the virtualenv is self-contained. The second stage copies that virtualenv and nothing
else — uv, pip's caches and the source tree never reach the shipped image. It runs as a
non-root user and writes only to `/app/db`.

For a Linux image from a Mac, this is where `buildx` earns its keep:

```sh
docker buildx build --platform linux/amd64 -t hermetica:$VERSION .
```

### Running the image

The layout inside the container mirrors the repo: **`/app/db` and `/app/logs`**, because
the code already defaults `DB` to `db` and `LOGS` to `logs` relative to its working
directory. Neither variable is set in either image — the defaults do the work.

The API **refuses to start without `chronos.db`**, which only a pull creates, so mount a
directory that has one:

```sh
# serve the API
docker run --rm -v "$PWD/db:/app/db" -p 127.0.0.1:8080:8080 hermetica:0.1.0

# run a pull instead
docker run --rm -v "$PWD/db:/app/db" -v "$PWD/logs:/app/logs" \
  --env-file env/.env hermetica:0.1.0 python -m chronos.chronos
```

**The image carries no data, no `env/.env` and no config** — only code. Configuration
comes from the environment at run time: `--env-file`, `-e`, or Compose `environment:`.
The `ENV` lines in the image are defaults, not constants, and a value already in the
environment always wins over one from a file. Nothing is hardcoded.

The one variable both images do set is `API_HOST=0.0.0.0`, because the code defaults to
`127.0.0.1` and no sibling container could reach that. **It is safe only while the port
stays unpublished** — there is no authentication. Under Compose, give the Hermetica
service no `ports:` at all and let the portal reach it over the internal network. The
`-p 127.0.0.1:8080:8080` above binds to loopback for local poking; never publish it on
`0.0.0.0`.

## The pixi environment

`pixi.toml` provides a conda-flavoured environment for people who would rather not use
nix.

```sh
pixi run test        # or: api, pull, lint, audit
pixi shell -e dev    # a shell with the dev tooling
```

It follows the same rule as the flake: **`[dependencies]` is system packages only**.
Every Python package is declared once, in `pyproject.toml`, and reaches the environment
through `[pypi-dependencies]` as an editable install. Three files declaring the same
Python versions would drift; one declaration cannot.

TeX is deliberately absent — conda-forge's coverage is patchy on Apple silicon, and
nothing needs it until `scribe` is wired up. The nix dev shell is the reference for that
toolchain.


# AI Use

Claude Opus 4.8/5 and Qwen3.8-27B.  