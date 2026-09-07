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
| 1 | protocol version | `protocol_id` / `protocol_guid` | `hash` |
| 2 | manifest at an instant | — | `manifest_hash` |
| 3 | pipeline graph | `pipeline_guid` | `graph_hash` |

- **Title is display only.** It is hashed (a retitle is a real change) but nothing resolves
  by it.
- **Lineage is not identity — content is.** A copy and a fork are both new protocols. A
  parent retires only when tagged. `version_class` gates nothing.
- **A source is an input; a lock is an output.** A lock can never be read back as a source.

> **DANGER — do not add a second source to `chronos.db` until identity is source-aware.**
> Identity is the bare `protocol_id`, there is no `source` column, and `_diff` computes
> absence as a set difference over every row in the table. A protocols.io-only pull
> against a two-source database would deprecate **every other source's protocols** by
> absence. The `elif` in `build_sources` refusing an unknown name is the only guard.

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
- **Frozen and slotted** — mutating after hashing would desync blob and hash.
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

### Time and intervals

- **All timestamps are unix epoch integers (UTC).** Human forms are produced at the call
  boundary. `get_timestamp` is the single clock read. Seconds, not days. (`to_epoch`
  rejects `bool` — it subclasses `int`, so `True` would become epoch 1.)
- Each version carries `[valid_from, deprecated_at)`. "Active at T" is a query, not a
  stored snapshot.
- **`valid_from` backdates to `created_on`** — but **only for a `protocol_id`'s first-ever
  version**, and "first-ever" means no history at all, not "no live version".
- **Invariant: at most one active version per `protocol_id` at any instant.** Checking "one
  row with `deprecated_at IS NULL`" does **not** catch violations — the real check is
  pairwise interval overlap.
- **deprecate-on-change**: a new hash closes the prior interval and opens a new one.
- **deprecate-on-absence**: a protocol missing from a pull is deprecated by set difference.
  Content addressing cannot see absence.
- **A blob is never deleted.** Old content stays resolvable by hash forever.

### Lifecycle — declared, never inferred

Upstream exposes no retirement state. Two triggers — **in the Trash**, or a `deprecated`
token in `keywords` — both meaning *never sealed*, both closing the interval by absence.
Neither is stored as a reason.

- **Trashed protocols are never fetched by id.**
- **`keywords` is never hashed**, so flagging cannot mint a version. This is why lifecycle
  never goes in `description`.
- **Token matching is exact, against an alias table.** `depreciated` and `depreceated` are
  aliased deliberately. Close-but-unrecognised is a **warning**, never acted on.
- **Removing a flag opens a NEW interval, never reopens the closed one.**
- **A folder named `Old` is not a signal.** Folder position is diagnostic only, except
  Trash itself. **We track and make visible; we do not decide.**
- **Planned — `protocol_pins`,** a *policy* list (so it must be editable, so it cannot be a
  column on append-only history), subtracted in `_diff`:
  `absent = active − incoming − skipped − pinned`. `skipped` = we failed to read it.
  `pinned` = policy says keep. Trash/deprecated = retire.

### Lock files

A **self-contained, verifiable contract on disk** — recomputing the hashes verifies it.
Two flavours: protocols-only, and pipeline (adds the pinned graph).

- **`manifest_hash` covers `entries` alone** — not `created_at`, `provenance` or display
  fields. Two locks pinning the same protocols are the same manifest a year apart.
- **`as_of` is recorded, never used to resolve.** Resolving a day to a manifest is not
  written.
- **Written human-readable, not canonical bytes.** Verification re-canonicalizes, so
  reformatting cannot break a lock.
- **`export_lock` raises if built `with_bodies=False`** rather than writing a file that
  silently cannot reproduce.
- **`verify_lock` returns the drift rather than raising** — a verifier that stops at the
  first problem cannot report the whole picture. Four lists, all empty meaning verified.
  **It reads no database.**
- Two hashes resolving to one `protocol_id` raise `DuplicateProtocolIdError`.
- **`hydrate_pins` cross-checks the rebuilt `manifest_hash`** against the file's: hashes can
  all resolve and still map to a different `protocol_id`. The DB is the only possible
  source — protocols.io serves current versions only.

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
- **Pinned to hashes**, so a pipeline reproduces even after a protocol is deprecated.
- **No graph database** — violates the local/sovereign/no-heavy-dep principles.
- **Not built:** validation against the read-only VC, fork-on-edit, parent links.

### Storage

- **`utils.store.connect` is the single connection helper** — `PRAGMA foreign_keys=ON`,
  commit or rollback, **close in a `finally`**. Plain `with sqlite3.connect(...)` commits
  but leaks the handle.
- **`get_content` returns entries in the order asked for** and raises if *any* hash is
  absent — a pin silently dropping out of a lock is the failure this prevents.
  `utils.store.fetch_rows` returns only what it found: **naming an absence is the caller's
  job, because utils owns no error vocabulary.**
- **`active_hashes` takes a connection, not a path** (`write_pull` needs it inside its own
  transaction) and sets `row_factory` on a **cursor it opens itself** — connection-wide
  would change the row type every other read gets.
- **Both stores name their own tables** and pass them into utils, so `scribe` and `chronos`
  never learn a table name.
- **The store takes artefacts, not dicts.**
- **Columns are derived from `METADATA_FIELDS`, never restated**, so drift is loud: a
  missing entry field is a `TypeError`, a missing column a `ProgrammingError`, and a
  reorder is harmless because binding is by name.
- **Three hashed fields are also columns** (`doi`, `reserved_doi`, `uri`) — a denormalized
  copy for display, never authoritative.
- **Two SQLite files, deliberately separate.** `chronos.db` is append-only with the **cron
  writer as sole writer**; `compose.db` uses the same interval machinery. `snapshots` is
  schema only.
- **The store must refuse to overwrite or delete history** — to be enforced by sqlite
  triggers so the rule holds whoever opens the file. **Not implemented.**
- **Access model:** read-only on the VC, read-write on graphs, never raw SQL — a **query
  port** and a **compose port**. Transport swappable.

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
- **Rate limit: 100 req/min/user**; `_call_api` carries `ratelimit` + `backoff`, giving up
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
- **Never put prose in a data structure.** Signal the one bit (`"degraded": True`) and let
  the explanation live in the docs.

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


