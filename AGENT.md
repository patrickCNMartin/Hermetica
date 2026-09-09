# AGENT.md — Hermetica

## How to keep this file

**Lean. Rules, not essays.**

- **A rule is one or two lines.** If it needs a paragraph to justify it, the rule stays
  here and the justification goes in `docs/`.
- **Write only what the code cannot say.** Invariants, traps, decisions and costs. If
  reading the file would tell you, cut it.
- **No history, no dates, no "decided on", no record of what a rule replaced.** This is
  the current state only. Mixed-in history makes the file contradict itself.
- **Every line must be true today.** A stale rule is worse than a missing one.
- **Prune on sight.** When a section grows an essay, cut it back that session.

**One file per job:**

| file | holds | read it |
|---|---|---|
| `AGENT.md` | what to do, and the state now | every session, first |
| `docs/protocols_io_findings.md` | measurements, probe transcripts, superseded beliefs | when a rule here looks arbitrary |
| `docs/protocols_io_open_questions.md` | what we asked protocols.io and the admin | before assuming an API limit is ours to fix |
| `docs/protocols_io_api.md` | upstream docs mirrored (the site 403s a default UA). **Where it contradicts findings, findings wins.** | to look up an endpoint |
| `docs/audit_log.md` | when and why each decision was taken — appended, never rewritten | **never by default** |

---

## What Hermetica is — *work in progress, nothing deployed*

**Content-addressable version control and composition for lab protocols.** An allowlist of
fields defines a protocol and is hashed (SHA256): identical content collapses to one
version, any change produces a new verifiable one. Traffic stats, publication flags and
signed URLs are discarded before hashing.

**Not tied to one platform.** protocols.io is the only adapter; nothing outside it reads a
protocols.io field name. A new platform is one directory under `sources/`.

Acquisition is **two-stage**: ask the source what exists, then fetch each id. The response
becomes a frozen `ProtocolArtefact` — **that is the platform boundary**, and it is what
hashing and the write path consume, never a raw API dict.

**Discovery yields ids and nothing else.** What a discovery route reads gates an id or
names it in a warning, then is discarded.

| Module | Role | State |
|---|---|---|
| `chronos` | the nightly loop — pull, write, log, report, mail | works |
| `sources` | adapters; one platform's bytes → a `ProtocolArtefact` | protocols.io only |
| `seal` | hashing, version intervals, lock files, lifecycle | works |
| `compose` | pipeline templates, pinned instances, DAG versioning | storage only, no validation |
| `scribe` | a lock back into something a human reads | works, fidelity partial |
| `utils` | mechanics — canonical form, hashing, dates, sqlite, intervals | works |
| *(planned)* | prose generation from a lock | **a sixth module, not part of `scribe`** |
| *(planned)* | HTTP/JSON API over the query and compose ports — the transport for outside tools | **thin; no raw SQL, stable ids only, sole opener of both `.db` files** |

**`chronos` decides *when* to look; an adapter knows *what a platform's bytes are*; `seal`
decides *what they mean*.** A rule about identity is seal's even if cron calls it. A rule
about a response shape is the adapter's even if hashing depends on it.

**`utils` holds mechanics, never rules.** The test: can the function be read without
knowing what a protocol is?

**Nothing is deployed, so hashes are still free to change.** That stops at the first
issued lock.

---

## Architecture — the invariants

**Three levels of the same idea:** stable identity on top, content-addressed version
underneath, append-only.

| Level | Object | Identity | Version |
|---|---|---|---|
| 1 | protocol version | `protocol_uid` / `protocol_guid` | `hash` |
| 2 | manifest at an instant | — | `manifest_hash` |
| 3 | pipeline graph | `pipeline_guid` | `hash` |

- **Title is display only.** It is hashed (a retitle is a real change) but nothing resolves
  by it.
- **Lineage is not identity — content is.** A copy and a fork are both new protocols. A
  parent retires only when tagged. `version_class` gates nothing.
- **A source is an input; a lock is an output.** A lock can never be read back as a source.
- **Identity is source-qualified: `protocol_uid` = `"<source>:<id>"`.** The bare id
  collides — two platforms can both number a protocol `88578`.
- **`source` is hashed**, so one protocol mirrored on two platforms is two records that
  drift independently. `check_source_name` keeps the name free of the separator.
- **Absence is computed inside one source's partition**, never over the whole table:
  `active_hashes` takes a `(column, value)` scope, and unscoped, everything another
  platform holds looks absent. **An empty pull cannot read its own source off its rows and
  must be told** — that is the case that would deprecate a whole platform.

### Hashing

- **`HASH_FIELDS` is an allowlist**, never a denylist — a denylist lets a new upstream
  field silently enter the hash.
- **`METADATA_FIELDS` is stored, never hashed.** Re-attribution is not a new version. It
  also generates the metadata columns.
- **`canonical_json` is NFC-normalized**, sorted, ASCII-escaped, `allow_nan=False`. `café`
  has two encodings that look identical and hash differently. **Altering the serializer
  invalidates every hash on disk.**
- **Serialize once.** `build_protocol_entry` feeds both the hash and the stored text from
  one serialization, so they cannot drift. `protocol_hash` re-serializes and the write path
  does not use it.
- **Changing what is hashed is a schema migration, not a tweak.**

### The artefact

- **`seal` defines it; the adapter builds it.** The scrub, step trimming, chain and unit
  map are one platform's knowledge. `parse_rich_text` is the one exception kept in `seal` —
  moving it would make `scribe` import from `sources`.
- **Frozen** — mutating after hashing would desync blob and hash.
- **`scrub_signed_urls` runs once, inside the build**, so hash and stored blob are covered
  together. Only *values* are blanked; the URL and filename stay hashed, so swapping the
  file is a version change. A leading `?` or `&` is required so prose like
  `Expires=2026-01-01` is untouched. **Known limit:** a re-upload under the same slug with
  different bytes is invisible.
- **Everything comes from the top level of the by-ID response.** `versions` is the version
  *family* keyed on the root, so for a non-root record it returns the **ancestor** —
  nothing reads it. `doi` never falls back to `reserved_doi`; that would make an unissued
  DOI look issued.
- **`last_modified_on` is not a change signal. Do not reintroduce it.** It is absent for
  most of our protocols, and narrowing a pull by modification time makes "unchanged and
  present" indistinguishable from "gone". Same reason a lock is not keyed on upstream's
  `(version_class, version_id)`.
- **`steps` is trimmed to content; ordering lives in `chain`.** Both hashed, so a reorder
  and a rewrite are distinguishable. **`number` is a string with dotted hierarchy** —
  sorting it raw is the obvious thing and it is wrong; a malformed number **raises**.
- **Every hashed rich-text field must be in `RICH_TEXT_FIELDS`.** Hashed but unscanned
  destroys the unit name at pull time. Enforced by a test.
- **The upstream `units` catalog is not content** — hashing it whole would re-fork every
  protocol when protocols.io edits it. An unresolvable id is omitted, never guessed.
- **Reagent and equipment entities stay in the blob, catalog state and all.** Which machine
  was used is load-bearing provenance.
- **`executor` is hashed.** The same steps run by a human and by a Biomek are two
  protocols, not one protocol with an attribute — that is why it sits on the artefact and
  **not on the pipeline**, where a graph of mixed executors could not describe itself.
  Declared, never inferred: `""` means nobody said so, and is not "human".

### Time and intervals

- **All timestamps are unix epoch integers (UTC).** Human forms are produced at the call
  boundary. `get_timestamp` is the single clock read. Seconds, not days. (`to_epoch`
  rejects `bool` — it subclasses `int`, so `True` would become epoch 1.)
- Each version carries `[valid_from, deprecated_at)`, so "active at T" is answerable as a
  query rather than a stored snapshot. Only "active **now**" is implemented
  (`active_hashes`); resolving an arbitrary date lives on `feature/versions-by-date`.
- **`valid_from` backdates to `created_on`** — but **only for a `protocol_uid`'s
  first-ever version**, and "first-ever" means no history at all, not "no live version".
- **Invariant: at most one active version per `protocol_uid` at any instant**, and per
  `pipeline_guid` in `compose.db`. A partial unique index on `deprecated_at IS NULL` enforces
  the live case on both history tables. It cannot see overlap between *closed* intervals, so
  the real check remains pairwise interval overlap.
- **deprecate-on-change**: a new hash closes the prior interval and opens a new one.
- **deprecate-on-absence**: a protocol missing from its own source's pull is deprecated
  by set difference within that source. Content addressing cannot see absence.
- **A blob is never deleted.** Old content stays resolvable by hash forever.

### Lifecycle — declared, never inferred

Upstream exposes no retirement state. Two triggers — **in the Trash**, or a `deprecated`
token in `keywords` — both meaning *never sealed*, both closing the interval by absence.
Neither is stored as a reason.

- **Trashed protocols are never fetched by id.**
- **`keywords` itself is never hashed**, so flagging cannot mint a version. This is why
  lifecycle never goes in `description`. **The `executor:<name>` token is the one
  exception** — it is *parsed out* of `keywords` into a hashed field, so declaring an
  executor **does** mint a version, deliberately. Everything else in `keywords` stays
  metadata.
- **`executor:<name>` is casefolded** (`split_keywords` already does it), so `Biomek` and
  `biomek` are one executor rather than two hashes. **Two declarations raise** — the value
  is hashed, and picking one would mint an identity nobody asked for.
- **Token matching is exact, against an alias table.** `depreciated` and `depreceated` are
  aliased deliberately. Close-but-unrecognised is a **warning**, never acted on.
- **Removing a flag opens a NEW interval, never reopens the closed one.**
- **A folder named `Old` is not a signal.** Folder position is diagnostic only, except
  Trash itself. **We track and make visible; we do not decide.**
- **Planned — `protocol_pins`,** a *policy* list (so it must be editable, so it cannot be a
  column on append-only history), subtracted in `diff_entries`:
  `absent = active − incoming − skipped − pinned`. `skipped` = we failed to read it.
  `pinned` = policy says keep. Trash/deprecated = retire.

### Lock files

A **self-contained, verifiable contract on disk** — recomputing the hashes verifies it.
Two flavours: protocols-only, and pipeline (adds the pinned graph).

- **`manifest_hash` covers `entries` alone** — not `created_at`, `provenance` or display
  fields. Two locks pinning the same protocols are the same manifest a year apart.
- **`as_of` is recorded, never used to resolve.** Resolving a day to a manifest is not
  written, and the day-to-version query it would build on is parked on
  `feature/versions-by-date`.
- **Written human-readable, not canonical bytes.** Verification re-canonicalizes, so
  reformatting cannot break a lock.
- **`export_lock` raises if built `with_bodies=False`** rather than writing a file that
  silently cannot reproduce.
- **`verify_lock` returns the drift rather than raising** — a verifier that stops at the
  first problem cannot report the whole picture. Four lists, all empty meaning verified.
  **It reads no database.**
- Two hashes resolving to one id raise `DuplicatedIdError` — **at lock generation only.**
  The write path does not re-check.
- **`hydrate_pins` does not re-check the rebuilt `manifest_hash`, deliberately.**
  `protocol_uid` is `f"{source}:{id}"` and `source`, `id` and `guid` are all hashed, so every
  field in `entries` is a function of the hash: an honest store cannot rebuild them
  differently, and a dishonest one is refused by the content triggers. The DB is still the
  only possible source — protocols.io serves current versions only.

### Rendering

- **`order` must be an exact permutation of `entries`** — a subset silently drops a
  protocol from a document claiming to be the manifest.
- **Steps follow `chain`, never stored list order**; disagreement raises.
- **Everything hashed is rendered.** A hashed field that never reaches the page is content
  the lock claims to carry and no reader sees.
- **Fidelity is deliberately partial.** Unknown entities render as a `[type]` marker —
  **never silently dropped**.
- **PDF look is config, not code** (`scribe/pandoc.yaml`). The engine must be `lualatex` —
  `pdflatex` stops on `≥`, `µ`, `°C`. **A missing glyph does not fail, it leaves a gap**,
  so verify by grepping the LaTeX log for `Missing character`, never by the PDF appearing.
  A YAML block in the markdown would *override* this file, not merge with it, so the
  rendered `.md` carries none and does not render correctly alone.

### Pipelines

- **`pipeline_guid` is identity; the hash is the version.** Minted once in the template,
  survives every edit. **Reading never mints** — a silent re-mint orphans everything
  stored under the old guid.
- **A node is not a protocol.** `DAG` is keyed on the pipeline's own node ids, and `nodes`
  maps each node id to the protocol it runs. That separation is what lets one protocol run
  at several points in one graph — `{wash_1: 568614, wash_2: 568614}` is two steps, one
  protocol. Keyed on protocols directly, a repeat is inexpressible and an attempt at one
  silently becomes a cycle.
- **Three fields, all hashed: `DAG`, `nodes`, `node_hashes`.** The first two are what a
  person writes and edits; `node_hashes` is node id -> the protocol hash active when
  `hydrate_pipeline` ran. The graph is stored **once**, in node space — `node_hashes` is a
  flat map, not a second graph, so the two cannot disagree about topology.
- **`hydrate_pipeline` resolves against *active* versions only**, so a deprecated protocol
  has no hash to give and **raises** (`UnresolvedProtocolError`) rather than pinning
  something stale. Re-hydrating after a protocol moves on is therefore a new pipeline
  version, not a silent edit — which is the whole reason `node_hashes` is hashed.
- **A bare `protocol_id` that answers for two active protocols raises**
  (`AmbiguousProtocolError`), naming the candidates. This is the cross-source collision
  `protocol_uid` exists to prevent; resolving it by picking one would reintroduce it.
- **A fork is parallel and conditional**, so which branch was written first is not
  information. `normalize_dag` sorts every successor list and lifts a bare string into a
  one-item list, so one graph written several ways is one hash. **It is a plain function
  called by `pipelines_from_template`, deliberately not `__post_init__`** — normalization
  stays visible at the call site instead of happening inside construction. An artefact
  built by hand is therefore not normalized: call `normalize_dag` yourself.
- **A graph that cannot run never gets a hash.** `validate_dag` checks that the node set of
  `DAG` equals the keys of `nodes` (`NodeMismatchError`, naming both sides) and that the
  graph is acyclic (`PipelineCycleError`, naming the cycle) via `graphlib.TopologicalSorter`
  — fed successors where it expects predecessors, which reverses the order it would produce
  and leaves cycle detection exactly right. It runs at template read **and** at hydration,
  so a hand-built artefact cannot slip past.
- **The template's keys are `nodes` and `dag`, and both are required.** `protocol_dag` was
  renamed rather than reused: it keyed protocols, `dag` keys nodes, so an old template left
  to default would parse and pin a graph that means something else.
- **Pinned to hashes**, so a pipeline reproduces even after a protocol is deprecated.
- **A pipeline has no executor.** It once did, which said every node in the graph ran on
  the same thing; a real pipeline hands off between a human and two robots. The executor
  is the protocol's, and hashed there.
- **No graph database** — violates the local/sovereign/no-heavy-dep principles.
- **Not built:** validation against the read-only VC, fork-on-edit, parent links.

### Storage

- **`utils.store.connect` is the single connection helper** — `PRAGMA foreign_keys=ON`,
  commit or rollback, **close in a `finally`**. Plain `with sqlite3.connect(...)` commits
  but leaks the handle.
- **`get_protocols` / `get_pipelines` return entries in the order asked for** and raise
  `MissingHash` if *any* hash is absent — a pin silently dropping out of a lock is the
  failure this prevents.
- **`fetch_entry` returns only what it found; `fetch_entries` raises `MissingHash`** — the
  one error `utils` owns, because the absence is its own lookup failing.
- **`active_hashes` takes a connection, not a path** (`write_version_control` needs it
  inside its own transaction) and sets `row_factory` on a **cursor it opens itself** —
  connection-wide would change the row type every other read gets.
- **Both stores name their own tables** and pass them into utils, so `scribe` and `chronos`
  never learn a table name.
- **The store takes artefacts, not dicts.**
- **Columns are derived from `METADATA_FIELDS`, never restated**, so drift is loud: a
  missing entry field is a `TypeError`, a missing column a `ProgrammingError`, and a
  reorder is harmless because binding is by name.
- **`compose` reads `chronos.db`, never the reverse.** `hydrate_pipeline` takes the
  protocol store as an argument and reads `protocol_content` joined to the live rows of
  `protocol_history`. The two files stay separate; only this direction crosses.
- **Six hashed fields are also columns** (`source`, `title`, `doi`, `reserved_doi`,
  `uri`, `executor`) — a denormalized copy for display and for scoping, never
  authoritative.
- **Two SQLite files, deliberately separate.** `chronos.db` is append-only with the **cron
  writer as sole writer**; `compose.db` uses the same interval machinery. `snapshots` is
  schema only.
- **The store refuses to overwrite or delete history, in the schema.** `append_only_triggers`
  puts it on both history tables: no `DELETE` ever, and the only legal `UPDATE` is closing an
  open interval — `deprecated_at` NULL → a timestamp, every other column frozen. Reopening,
  re-stamping and repointing all `RAISE(ABORT)`.
- **`immutable_triggers` makes both content tables insert-only** — no `UPDATE`, no `DELETE`.
  A row addressed by the hash of its own bytes cannot be edited into still being itself.
- **Why triggers and not the FK.** `connect` sets `PRAGMA foreign_keys = ON`, but a PRAGMA is
  *per connection* and defaults to off, so `sqlite3 chronos.db` has none of our protection. A
  trigger is in the schema and binds whoever opens the file. Verified against the bare CLI.
- **Access model:** read-only on the VC, read-write on graphs, never raw SQL — a **query
  port** and a **compose port**. Transport swappable; the planned transport is a thin
  HTTP/JSON API (see the module table), and it is the only process that opens either
  `.db` file. Outside tools talk to it, never to a shared mount.
- **`compose.db` gains a second writer** — the pipeline portal writes user-composed
  pipelines back through the compose port. One API instance, WAL, serialised writes,
  never on a network filesystem. This is why the history-protection triggers stop being
  optional before deploy.
- **A lock file is an export, not the integration bus.** Outside systems work live
  through the ports; a lock is the reproducible receipt they archive or hand on.

---

## protocols.io — the rules that cost us data

Each line cost a silent data loss. **Do not "simplify" any of it without a live probe.**
Measurements: `docs/protocols_io_findings.md`. All of it lives in
`hermetica/sources/protocols_io/` and nowhere else.

### Acquisition
- **The workspace walk is the default.** It uses endpoints upstream marks `[Archived]`
  because they are the only ones that enumerate a private workspace — the v4 replacements
  return 400 — and unlike `/v3/protocols` they **do not collapse a version family**.
- **`[Archived]` does not mean going away** — the dev team confirmed support continues,
  with advance notice and a replacement promised.
- **`/v3/folders/<guid>/ids` is 1-indexed; `/v3/protocols` is 0-indexed.** Page 0 on the
  folder pager returns an empty array *with* a populated `next_page`, so a pager written
  against the other endpoint finds nothing and exits cleanly.
- **The walk publishes no global total** — the only completeness check is per folder,
  raising `IncompleteWalkError`.
- **Selection is `discovered − trash − not-a-protocol`.** **How a protocol was discovered
  qualifies nothing** — a gate reading provenance can only shrink a pull for a reason we
  invented.
- **`filter=shared_with_user` returns 0 and cannot be trusted** — every protocol it omits
  still fetches by id on the same token.
- **The `filter` fallback is degraded four ways:** stores trashed protocols, misses other
  members' published ones, misses older family members, and retires a versioned protocol's
  predecessor by absence. Kept, not maintained.
- **`order_field` must be a unique key — use `id`.** Sorting by `date` or `name` lets the
  page window shift: a measured 51 pulled, 29 distinct.
- **`key` is required** — omitting it or passing `""` returns HTTP 400; a single space works.
- **Drive the loop from `pagination`, not page length** — a full page can be the last, a
  short page can have more after it.
- **No page ceiling by default.** A fixed cap is silent truncation; the count check makes
  an unbounded walk safe.
- **Rate limit: 100 req/min/user**; `call_api` carries `ratelimit` + `backoff`, giving up
  on 4xx other than 429. The PDF endpoint is far stricter (5/min).

### Response shape
- **`steps` is `null`, not absent**, for at least one protocol — `.get("steps", [])`
  returns `None` because the key exists. Use `.get("steps") or []`. Same for `versions`.
- **The list response is thinner than by-ID**: `version_class` and `fork_id` are `None`.
- **`in_trash` exists only on the File Manager item**, never on the protocol payload.
- **`version_data` is `None` for all 61.**
- **A published record carries TWO short codes** — the citable one is `version_uri`, not
  `uri`. `version_uri` is `""` while private.

### Authentication
- Hermetica uses `CLIENT_ACCESS_TOKEN` as `Authorization: Bearer <API_KEY>`.
  `CLIENT_ID`/`CLIENT_SECRET` are dead config kept for the day OAuth is needed.
- **Neither token is a "see everything" key.** The v3 filters are defined by **authorship
  and sharing, not permission** — a workspace-admin service account that authored nothing
  returns **0 from every filter**. Swapping in admin credentials may be worse.
- **The walk is not filter-scoped**, so it returns the whole workspace on the same token
  that returns 0 from `shared_with_user`. That gap is why the walk is the default.
- **Open thread: the v4 workspace search.** Every form we constructed returns
  `{"status_code":3,"invalid params"}`, including one with a deliberately invalid guid, so
  it fails before path validation. **Untested with a real `workspace_uri`** — no API route
  returns one for a private workspace, but it is readable from the browser address bar.

---

## Principles

**1. Energy and resource cost is a primary concern.** Stream rather than load everything.
Hash and dedupe before persisting; trashed and excluded protocols are never fetched.
Choose the complexity class before micro-optimizing. No dependency without a reason.

**2. Progressive enhancement, not graceful degradation.** A correct minimal core, then
optional capability above it. The base path never depends on the enhancement.

**3. Reproducible, runs anywhere.** The Nix flake and `uv.lock` are load-bearing. Config
comes from `env/.env` — never hardcode URLs, keys or paths. Prefer pure functions; isolate
network and DB I/O. Fail loudly and early — no sentinels a caller could mistake for data.

**Locality — the measure is how far you must travel to know what code does.** Structure is
welcome where the constraint is visible where it acts (a frozen dataclass, an allowlist, a
unique index) and a cost where the meaning sits elsewhere (a wrapper that renames a call, a
constant read out of the module frame). One maintainer, exploratory code: the arithmetic
that justifies indirection does not apply here.

**A function is a capsule — everything it needs is in its arguments.**
- Config is read once at the edge and passed down. Nothing below `__main__` touches
  `os.getenv`.
- **Anything sourced from `env/.env` must be an argument.** A constant is not a licence to
  reach outside the capsule.
- **A long signature beats a config object.** Nine named arguments are honest; a bag hides
  which fields are used.
- **The tell is untestability.** A function that reads its own module can only be tested by
  patching module attributes, so it quietly never gets a test.

**Indirection must earn its link.**
- A named indirection must **enforce an invariant or build state**. If it only renames a
  call, inline it.
- **Constants are exempt.** Repetition of a *decision* is a hazard; repetition of a *call*
  is typing.
- **Never put *commentary* in a data structure.** A field holding the reason the code did
  something is a comment that learned to travel. Signal the one bit (`"degraded": True`)
  and let the explanation live in the docs.
- **An error message is not commentary — it is the failure's only output**, so it is
  spelled out in full and read by a human at the worst moment. Prose belongs there.

**An exception takes data, not a sentence.** Constructor arguments in, attributes stored,
message formatted in `__init__` — so a caller reads `error.missing` instead of parsing a
string, one wording lives in one place, and every raise site is the fault and nothing else.
Two raise sites that cannot share a message are two errors: give each its own class rather
than widen one signature to cover both.

**Prefer constructs that make the wrong thing impossible or loud** — an allowlist, a frozen
dataclass, derived columns, named parameter binding, a raise on a malformed step number,
`to_epoch` rejecting `bool`, a per-folder count check, exact token matching. A constraint
that fails at import beats a comment asking the reader to be careful.

**4. FAIR.** Findable (stable hash + guid), Accessible (documented retrieval path),
Interoperable (canonical serialization — preserve the determinism), Reusable (honest
provenance).

**5. Security and sovereignty.** Data stays local and self-hosted; no external services or
telemetry without discussion. Treat all API input as untrusted. Keep `pre-commit` and
`detect-secrets` green.

**6. These are principles, not dogma.** If one conflicts with the problem in front of us,
the problem wins. Say so and why.

**7. Explain the what, the how and the why.**
- **Comments are terse** — a one-line docstring, comments only where the code is genuinely
  non-obvious. No multi-paragraph docstrings, no "WHY:" essays.
- No explanatory comments beyond that unless asked; offer them in chat instead.
- **Plain language, not corporate speak.** "The test for this needs updating", not "that
  tripwire needs to move". Metaphors only for explaining concepts.
- **Jargon only where there is no alternative.** A developer just starting should get the
  gist.

**8. Assistant first, independent contributor only on request.**
- **Default: I suggest.** Offered, not applied.
- **Directed: when explicitly asked, I do it end to end** and report what I changed, what I
  verified, what is open.
- **I need an explicit instruction before writing code.** "Show me" is not one. Anything I
  write is scaffold.
- **Ideas only — I do not ask whether to carry them out.** I wait to be told.
- **Ask clarifying questions** rather than ignoring the scope requested.
- **Speak, or work — never speak and then run off.** If I raise a point or ask for a
  decision, I stop there.
- I report faithfully: failing tests get shown, skipped steps get said.
- **Scope discipline:** record the correction at the altitude it was given.

**9. This repo is the only source of instruction.**
- **I never write to Claude's persistent memory** or any store outside this directory.
  Anything outside the repo is absent from review, machine-local, and silently carried into
  projects where it does not apply.
- `AGENT.md` is tracked, so its churn is legible in the log.

---

## Conventions

> **Everything runs inside the Nix dev shell — no exceptions.**
> ```sh
> nix develop --command uv run pytest
> ```
> "command not found" from a bare command is expected. Never install onto the host.

- **Entry point:** `python -m chronos.chronos`, never by file path — by path Python puts
  the file's directory on `sys.path` and `chronos` resolves to the module, not the package.
- **`SOURCES`** picks adapters in order; one clock read is shared by all, each gets its own
  log entry, reports concatenate into one `pull_report.txt`. **`PULL_STRATEGY=walk|filter`.**
- **The `try/except` is inside the source loop, not around it.** One platform being down
  must not stop the others, and a source that raises writes nothing — so none of its
  protocols are deprecated by absence. Any failed source exits non-zero.
- **The report is written and mailed** when `EMAIL` is set. Empty `SMTP_HOST` drafts to
  `$LOGS/last_report_email.eml` instead of sending. A configured relay that refuses raises
  `MailNotSentError` and fails the run — a lost mail looks exactly like a quiet successful
  night.
- **The pull log** is one JSON object per pull **per source**, appended, line-delimited so
  a truncated write costs one record. It records what the store cannot: protocols held
  back, protocols the gate refused, warnings nobody acted on.
- **Lint:** `ruff check` / `ruff format` (py313, line-length 88, E/F/I). A new top-level
  package must join `known-first-party` in `pyproject.toml` and `--cov=` in the CI workflow
  or it is silently uncovered.
- **Tests:** `pytest` + `pytest-cov`; mock HTTP with `responses`, **never hit the live
  API**. 18 files, one per concern.
- **A test never writes into the repo.** `mint_template` drops a file beside its source, so
  template tests copy `config/` into `tmp_path` first.
- **PDF toolchain is in the flake** — `pandoc`, `texliveSmall` + `dejavu` +
  `lualatex-math`. `oci_deps` is declared but **nothing builds an image yet**.
- **CI:** `test`, `lint` (including `detect-secrets-hook` over **tracked files only**), and
  `nix` (the suite through `nix develop`). The `uv` jobs are the contract; `nix` is a
  health check.

### Test fixtures — committed dummy data

The JSON *is* the source of truth — **no generator, no build step**. The generator was
retired because policing it forced a denylist of real terms, so the repo tracked real names
and an AWS key id in the file meant to keep them out. **The guards are allowlists:**
`LEXICON` (~50 words — every word of human-readable text), `example.org` hosts and emails
(at least one email must exist), DOI prefix `10.99999`, and `CREDENTIAL_SHAPES` (8 regexes
that must find zero matches).

- **The lexicon is load-bearing.** A pasted real protocol fails on its first word, and no
  real term is ever named to catch it. Slugs, guids and hex are excluded — prose is where
  an identifying term hides.
- **Every dataset joins `DATASETS`.** An unscanned fixture can leak.
- **The AWS row is why the fixture key id is `EXAMPLEKEYID`, not `AKIA`** — an `AKIA` value
  trips GitHub push protection, which no local baseline can waive.
- **Records are named for their structure**, never by protocol id — `dotted_steps` reaches
  step `"10"`, without which the chain-ordering bug cannot be caught.
- **Signing values are adversarial to the scrub, not credential-like.**
- **The walk fixture carries the shapes that bite:** 1-indexed pagination across two pages,
  a nested folder, an empty folder, a trashed folder holding an *unflagged* protocol, a
  protocol in two folders, a version family, and a Collection that must not be sealed.

### `.gitignore` — a denylist

**New files are tracked by default.** A file that must not ship needs a rule.

- **The guard against a leaked credential is `detect-secrets`, not this file** — it reads
  contents, which is the right axis.
- **Excluded:** `db/`, `logs/`, any `.env` at any depth, `*.lock` except `flake.lock` and
  `uv.lock`, `*.pdf`, `*.db`.
- **The risk:** real protocol data written *outside* `db/` is committable. The pull only
  writes to `DB_OUT`/`LOGS`; a hand-saved dump elsewhere is not covered. Read `git status`
  before committing.

### Showing code "as diff"

Write the proposed *after* version to the scratchpad, then
`/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code --diff <original>
<proposed>` (`code` is not on PATH). **Never modify the tracked file to render a diff.**


