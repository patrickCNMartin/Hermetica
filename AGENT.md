# AGENT.md — Working principles for Hermetica

What Hermetica is, how I work in it, and the constraints that matter. If a principle
here conflicts with the concrete problem in front of us, the problem wins (Principle 6).
When I deviate, I say so and why.

> **This file carries only the essentials — what Hermetica is, what to do, and how I am
> expected to behave. Every specific lives in `docs/`.** If a rule here looks arbitrary or
> thin, the detail is over there; do not restate it here. This file and `docs/audit_log.md`
> are tracked; the three `protocols_io_*` files below are git-ignored.
>
> **This file is the current state, at its most compact. It carries no history.** No dates,
> no "decided on", no record of what a rule replaced. An audit trail runs for pages and
> says nothing about where the project stands today. Mixed in here it does active harm:
> the file contradicts itself, superseded rules read as live ones, and I invent tasks from
> decisions that were already closed. A reader must be able to take any line here as true
> right now without dating it.
>
> **One file per job:**
>
> | file | holds | when I read it |
> |---|---|---|
> | `AGENT.md` | what to do, and the state right now | every session, first |
> | `docs/protocols_io_findings.md` | how we know — measurements, probe transcripts, superseded beliefs | when a rule here looks arbitrary, or before trusting an API claim |
> | `docs/protocols_io_open_questions.md` | what we have asked protocols.io support and the workspace admin, and why it matters | before assuming an API limitation is ours to fix |
> | `docs/protocols_io_api.md` | the upstream docs, mirrored (the site 403s a default user-agent). The body lags upstream; the two sections at the top say exactly where, and the last raw fetch sits beside it for diffing | to look up an endpoint — **read those two sections first**. **Where it contradicts measured behaviour, findings wins.** |
> | `docs/audit_log.md` | when and why each major decision was taken | **never by default** — only when the history of a decision is actually asked for |
>
> **I append to `docs/audit_log.md`, never rewrite it.** One entry per major decision or
> discovery: the date, the git hash it was taken at, what was decided, and why. It is the
> only place a date belongs. If a rule is missing from this file, check findings and the
> audit log before assuming it never existed.

---

## What Hermetica is

A **content-addressable version-control and composition system for lab protocols**. It
keeps an explicit allowlist of the fields that define a protocol and hashes exactly those
(SHA256), so identical content collapses to one version and any change produces a new,
verifiable one. Everything outside the allowlist — traffic stats, publication flags,
signed URLs — is discarded before hashing.

**Hermetica is not tied to any one platform.** protocols.io is the only source built
today, but it is reached through an adapter and nothing outside that adapter reads a
protocols.io field name. Adding a platform is one directory under `sources/`.

Acquisition is **two-stage**: the source is asked what exists, then each id is fetched.
The response is reshaped into a frozen **`ProtocolArtefact`** (`seal/contract.py`), and
that artefact — never a raw API dict — is what the hashing and write paths consume.
**That artefact is the platform boundary.**

**Discovery yields protocol ids and nothing else.** Whatever a discovery route reads about
a protocol is used to gate the id or to name it in a warning, then thrown away — no field
from a walk item or a list item is ever stored. The by-ID record is the only source of
content, so the two stages cannot disagree about what a protocol is.

| Module | Role |
|---|---|
| `chronos` | **The nightly loop** — scheduled pulls, driving the write, the pull log and report. Reads no field names and names no platform except in `build_sources`. |
| `sources` | **Platform adapters** — everything Hermetica knows about one place protocols come from. Turns that platform's bytes into a `ProtocolArtefact`. |
| `seal` | **Hashing, version control & lock files** — canonical serialization, the content contract, the append-only log + snapshots, epoch/date conversion, lifecycle tokens, lock generation & verification. |
| `compose` | Composition — pipeline templates & pinned instances, DAG versioning + lineage, resolving graphs against a dated manifest (`compose.db`). |
| `scribe` | **Presentation** — a lock back into something a human reads. Owns no identity, time or storage rules; it only reads. |
| `utils` | **Shared mechanics** — canonical form, hashing, epoch/date conversion, the sqlite connection and schema helpers, and the version-interval machinery. Knows no platform, owns no error vocabulary, and names no table: every function takes its table and id column as arguments. |
| *(planned)* | **Prose generation** — materials-and-methods from a lock + template, optionally LLM-assisted. A **sixth module, not part of `scribe`**: an LLM is a heavy dependency that cannot be assumed present, so generation sits *above* the lock, never inside the path that produces it (Principle 2). |

The split: **`chronos` decides *when* to look; a `sources` adapter knows *what one
platform's bytes are*; `seal` decides *what those bytes mean*.** A rule about identity
belongs in `seal` even if the cron job calls it. A rule about a platform's response
shape belongs in that platform's adapter even if hashing depends on it.

**`utils` is the exception that proves the split: it holds mechanics, never rules.**
`to_epoch` converts; *what `valid_from` means* is seal's. `hash_bytes` digests; *which
fields get hashed* is `HASH_FIELDS`. `diff_entries` groups two maps; *that an absence
deprecates* is seal's. The test is whether a function can be read without knowing what a
protocol is — if not, it does not belong here.

```
hermetica/
  utils/hashing.py                    canonical_json, hash_bytes/hash_of, as_column
  utils/dates.py                      epoch <-> human at the call boundary
  utils/store.py                      connect, initialize_db, insert_statement,
                                      fetch_rows, verify_blobs
  utils/intervals.py                  active_hashes, seen_before, diff_entries,
                                      open/close_intervals, versions_on_date
  chronos/chronos.py                  the loop; build_sources -> run_pull -> log
  chronos/pull_log.py                 append-only record of what each pull did
  chronos/report.py                   the human-readable twin of the log
  sources/contract.py                 DiscoveredProtocols, FetchedProtocol,
                                      ProtocolSource, check_source_name
  sources/protocols_io/__init__.py    build_source — wires discover + fetch
  sources/protocols_io/client.py      _call_api (ratelimit+backoff), _walk_pages,
                                      fetch_protocol_list, fetch_protocol
  sources/protocols_io/discover.py    the workspace walk; WalkItem, select_protocols,
                                      discover_by_walk/_by_filter
  sources/protocols_io/artefact.py    the response shape -> ProtocolArtefact;
                                      scrub_signed_urls, steps, chain, units
  sources/protocols_io/lifecycle.py   screen_protocol — the deprecated keyword
  seal/contract.py                    ProtocolArtefact, HASH_FIELDS/METADATA_FIELDS,
                                      parse_rich_text, protocol_hash
  seal/lifecycle.py                   deprecated-keyword alias table + predicate
  seal/store.py                       schema, build_protocol_entry/format_db_entry,
                                      diff_pull, write_pull, get_content, verify_protocols
  seal/seal.py                        lock documents — generate, export, verify_lock
  compose/compose.py                  active_protocols; ProtocolPipeline dataclass
  compose/store.py                    pipeline schema, build_pipeline_entry,
                                      write_pipeline, get_pipelines, verify_pipelines
  compose/templates.py                YAML base DAG templates; guid minting
  scribe/hydrate.py                   hydrate_pins — pins-only lock -> full document
  scribe/richtext.py                  Draft.js -> text; entities + unit resolution
  scribe/markdown.py                  to_markdown/export_markdown
  scribe/pandoc.yaml                  PDF look: engine + fonts. Config, not code.
config/pg_core_templates.yaml         the seven base pipelines, unminted
tests/dev_tests/                      584 passing
```

The probe script (`scratch.py`) lives in the git-ignored `protocol_io_bundle/` and is
**not part of the pull path**.

Three content-addressed levels — **protocol -> manifest -> pipeline** — across two SQLite
files (`db/chronos.db`, `db/compose.db`).

---

## Architecture & data model

**Git for lab protocols and the pipelines built from them.** One idea — stable identity
on top, content-addressed pinned version underneath, deduplicated and append-only —
repeated at three levels:

| Level | Object | Identity | Version | Git analogue |
|---|---|---|---|---|
| 1 | protocol version | `protocol_id` / `protocol_guid` | `hash` (SHA256 of canonical JSON) | blob |
| 2 | manifest — `{protocol_id -> hash}` active at an instant | — | `manifest_hash` | tree |
| 3 | pipeline graph — a DAG over protocol hashes + edges | `pipeline_id` | `graph_hash` | commit |

**The title is display only — never an identifier.** Titles get edited and duplicated.
`title` *is* hashed (a retitle is a real content change) and *is* a column for display
and querying, but nothing resolves a protocol by it.

**Lineage is not identity — content is.** A copy and a fork are
both simply new protocols; downstream behaviour is identical for either. A parent retires
only when someone explicitly tags it, never because a descendant appeared. `version_class`
is **hashed content and nothing more** — it gates no decision.

### The source interface (`sources/contract.py`)

A platform is two callables plus the name they were built for:

```python
ProtocolSource(name, discover, fetch)
    discover()            -> DiscoveredProtocols(ids, strategy, detail)
    fetch(protocol_id)    -> FetchedProtocol(artefact, retired, warnings)
```

- **The name travels *with* the callables.** It is written to the store as part of
  identity; passed as a separate argument it could disagree with them. `check_source_name`
  rejects anything but `^[a-z0-9_]+$`, because the name prefixes every `protocol_uid` and
  a separator in it would make the uid ambiguous.
- **`retired` collapses three things into one bit.** Trash, a non-protocol `type_id` and
  the `deprecated` keyword are all one platform's business. `chronos` reads a boolean and
  never learns that retirement has shapes.
- **`artefact=None` with `retired=False` means the read failed**, which is *not* evidence
  the protocol went away. `run_pull` **raises** on it: nothing is written, so nothing is
  deprecated by absence. When the `skipped` set is wired through `_diff`, that raise
  becomes a collection — the raise is holding the seam open, not standing in for it.
- **Config is read once, in `__main__`, and passed down.** No adapter reads `os.getenv`;
  `build_sources` takes every value as an argument. `SOURCES` (comma separated, default
  `protocols_io`) picks which adapters tonight's run uses.
- **Each adapter's `build_source` has its own explicit signature** — platforms need
  different things and a shared config bag would hide which.
- **A second implementation is what checks the seam.** A fake `ProtocolSource` in
  `tests/dev_tests/test_sources.py` drives `run_pull` with no network and no platform. An
  interface with one implementation is a guess.
- **A source is an *input*; a lock is an *output*.** A lock holds content already hashed,
  so it can never be read back as a source — that would be re-hashing our own output.

### The content contract (`seal/contract.py`)

`build_protocol_artefact` — **in the adapter (`sources/protocols_io/artefact.py`), not in
`seal`** — takes one by-ID response and returns a **frozen, slotted `ProtocolArtefact`**
— frozen because it is a snapshot of upstream content, not a working buffer: mutating one
after hashing would desync blob and hash. Three views: `hashable()` (exactly
`HASH_FIELDS`, the blob that is stored), `metadata()` (exactly `METADATA_FIELDS`),
`to_dict()` (everything).

**`seal` defines the artefact; an adapter builds it.** `seal/contract.py` keeps
`ProtocolArtefact`, the field lists, `canonical_json` and the hashing. The scrub, the step
trimming, the chain and the unit map all live in the adapter — they are knowledge about
one platform's response, and hashing must not depend on `seal` knowing it.

`parse_rich_text` is the one exception, kept in `seal`: both the adapter (finding units at
pull time) and `scribe/richtext.py` (rendering) need it, and moving it would make `scribe`
import from `sources` — the wrong direction. Its body is format-neutral.

1. **`HASH_FIELDS` — an allowlist, hashed.** `doi`, `reserved_doi`, `id`, `guid`,
   `title`, `description`, `guidelines`, `before_start`, `disclaimer`, `warning`,
   `materials`, `steps`, `chain`, `units`, `uri`, `version_class`,
   `protocol_references`.
   *Allowlist, not denylist:* a denylist is open-ended — the day the API adds a field
   nobody thought to exclude, it silently enters the hash.
2. **`METADATA_FIELDS` — retained, stored, never hashed.** `created_on`, `creator`,
   `authors`, `keywords`. Re-attribution is not a new version. This tuple also
   **generates the metadata columns** of `protocol_content` and the insert's named
   parameters.
3. **`scrub_signed_urls` — applied once, up front, inside `build_protocol_artefact`**, so
   it covers the hash and the stored blob together and nothing can be rewritten after.
   - Only the *values* are blanked. **The URL and filename stay in the hash**, so
     swapping the attached file *is* a version change.
   - A leading URL separator (`?`, `&`, or the literal `&` at that nesting depth) is
     required, so prose like `Expires=2026-01-01` is never touched.
   - Known limitation: a file re-uploaded under the *same* slug with different bytes is
     invisible. Hashing attachment contents is deferred as too costly.

**Everything is sourced from the top level of the by-ID response.** Nothing reads
`versions` — it is the version *family* keyed on the family root, so for any non-root
record it returns the **ancestor**. `doi` reads the top level with a `""` fallback (never
`reserved_doi` — that would make an unissued DOI look issued). `materials` holds
`materials_text`. `steps`, `chain` and `units` are derived. `version_code` and
`last_modified_on` are dropped outright.

**`last_modified_on` is not a change signal and must not be reintroduced as one.** Most of
our protocols have no version carrying the tag, so it is absent for most of them and can
gate nothing. It gets re-proposed — protocols.io themselves recommend it — so the reason
is written down here. Even where it exists, narrowing a pull by modification time makes
"unchanged and still present" indistinguishable from "gone", which breaks
deprecate-on-absence. For the same reason we do not key a lock on upstream's
`(version_class, version_id)`: an upstream identifier moves when upstream decides
something is a new version, a content hash moves when the content moves.

**Reagent and equipment entities stay in the blob, catalog state and all.**
They live inline in `materials_text`, `before_start` and `steps[].step`,
not in the `materials` list. `can_edit`, `is_public` and `vendor.is_requested` ride along
unscrubbed: they are constant across all 454 today, and a flip mints an extra version
without ever invalidating a lock. Which machine was used is load-bearing provenance — if
the lab replaces an instrument in two years, the pinned version must still say what ran.

*A pull is a commit, not a save.* You can save a document a hundred times and commit
once; what Hermetica needs to know is that content differed between two pulls, and the
hash says that exactly.

#### Steps and chain

`steps` is trimmed to content only (`id`, `guid`, `section`, `step`, `critical`);
**ordering lives separately in `chain`**, the step ids sorted by upstream `number`. Both
are hashed, so a reorder and a rewrite are distinguishable in a diff.

**`number` is a *string* carrying dotted hierarchical numbering** (`"1"`, `"1.1"`, …
`"10"`), so `_step_order` parses it to a tuple of ints. Sorting the raw string is the
obvious thing to write and it is wrong. A malformed number **raises** rather than falling
back to an order that is quietly incorrect.

#### The unit map (`units`) — derived *(protocols.io adapter)*

Rich text stores quantities by integer unit id (`{"amount": "1.211", "unit": 6}`).
`get_unit_map` walks `RICH_TEXT_FIELDS` and every `steps[].step` — recursing into nested
documents — collects every `unit`/`temperatureUnit` id, and resolves them against the
record's own `units` list, storing `{str(id): name}` for **only the ids actually cited**.

- **Every hashed rich-text field must be in `RICH_TEXT_FIELDS`.** Hashed but unscanned
  stores text citing ids the map cannot resolve, and the catalog is discarded by then —
  the name is *destroyed at pull time*, not merely unrendered. Enforced by
  `test_every_hashed_rich_text_field_is_scanned_for_units`, whose `HASHED_RICH_TEXT` is
  restated rather than derived.
- **The upstream `units` list is a shared catalog, not protocol content.** Hashing it
  whole would re-fork every protocol whenever protocols.io edits the catalog —
  structurally the signed-URL bug again.
- **An id the catalog cannot resolve is omitted, never guessed.** It renders as a visible
  `[unit:3900]` marker, so unresolved can never be mistaken for resolved.
- **The scan recurses even though the renderer treats `notes` as a marker** — otherwise a
  later renderer that descends into notes forces a second full re-hash.

Changing any of this re-hashes every protocol version. Treat it as a **schema migration,
not a tweak**.

### Canonical form & hashing

All of this lives in `utils/hashing.py` — one definition, used by protocols and pipelines
alike.

`canonical_json` → sorted keys, no whitespace, ASCII-escaped, **NFC-normalized**,
`allow_nan=False`. NFC matters: `café` has two valid encodings that look identical and
hash differently. Altering the serializer invalidates every hash on disk.

- `canonical_json(artefact.hashable())` → **the exact byte string stored** in
  `protocol_content.protocol` and `pipeline_content.pipeline`.
- `hash_bytes(blob)` → `sha256:<hexdigest>`; the algorithm prefix is part of the hash.
- `hash_of(payload)` = `hash_bytes(canonical_json(payload))`, for a caller holding a dict
  rather than bytes: `protocol_hash` and `seal.manifest_hash`.
- `protocol_hash(artefact)` re-serializes — **the write path does not use it**.
  `build_protocol_entry` serializes once and feeds both `hash_bytes` and the stored text,
  so blob and hash cannot drift. `build_pipeline_entry` does the same.
- `as_column(value)` → anything sqlite cannot store natively becomes canonical JSON text,
  so a stored dict is byte-stable too.

Sorted keys mean output does not depend on Python's per-process hash seed (verified under
`PYTHONHASHSEED=1` and `999`).

### Temporal model (the "as of date T" contract)

- **All timestamps are unix epoch integers (UTC), never date strings.** Human-readable
  forms are produced at the call boundary by `utils/dates.py`. `get_timestamp` is the
  single clock read — nothing else calls `datetime.now`. Seconds, not days, so two pulls
  on the same day still order correctly. (`to_epoch` rejects `bool` explicitly: it
  subclasses `int`, so `True` would otherwise become epoch 1.)
- Each version carries `[valid_from, deprecated_at)`. "Active at T" is a query, not a
  stored snapshot: `valid_from <= T AND (deprecated_at IS NULL OR deprecated_at > T)`.
- **`valid_from` backdates to the protocol's own `created_on`**, falling back to the pull
  timestamp. One pull shares one timestamp.
  - **Only a `protocol_id`'s first-ever version backdates.** `created_on` says when the
    *protocol* was authored, not when *this version* was made.
  - "First-ever" means **no history at all**, not "no live version". A protocol that
    disappears and later returns must open at the pull that found it again
    (`test_reappearing_protocol_opens_a_new_interval`).
  - Checking "at most one row with `deprecated_at IS NULL`" does **not** catch this — the
    violation is temporal. The real check is pairwise interval overlap
    (`test_no_interval_overlaps_across_a_churny_history`).
- **Invariant:** at any instant each `protocol_id` has at most one active version.
- Two behaviours the write path must maintain, or manifests lie:
  - **deprecate-on-change** — a new hash closes the prior interval and opens a new one.
  - **deprecate-on-absence** — a protocol missing from the pull is deprecated by diffing
    the pull's id-set against the active id-set (content-addressing can't see absence).
- A blob is **never deleted** on deprecation — old content stays resolvable by hash
  forever, so any pinned manifest or pipeline always reproduces.

### Lifecycle — retirement is declared, not inferred

Upstream exposes no retirement state, so the lab declares it. **Two triggers, one
mechanism:**

| trigger | how it is seen |
|---|---|
| **in the Trash** | `in_trash` on the File Manager item, or position under the Trash folder |
| **`deprecated` keyword** | a token in `keywords`, checked after the by-ID fetch |

Both mean *never sealed*. Both close the open interval by being **absent** from the
pull's row set. Neither is stored as a reason — they are functionally identical
downstream, and a column nothing reads is a column that rots.

- **Trashed protocols are never fetched by id.** Trash costs zero calls, not one hash.
- **`keywords` is flat text and never hashed**, so flagging cannot mint a version whose
  only change is the flag. `description` would, which is why lifecycle never goes there.
- **Token matching is exact, against a documented alias table** (`seal/lifecycle.py`).
  Split on commas, trim, casefold, compare. A fuzzy matcher would accept one typo and
  reject the next with no way to tell which happened. `depreciated` and `depreceated` are
  aliased deliberately. Anything close-but-unrecognised is a **warning**, never acted on.
- **Removing a flag opens a NEW interval, never reopens the closed one.** History is
  append-only: undoing a state does not unhappen the event.
- **A folder named `Old` is not a signal.** Folder position is diagnostic only, except
  for the Trash folder itself. If people file things in `Old`, they must also tag them.
- **Not lifecycle signals, deliberately:** `fork_id` is lineage, not supersession.
  **The governing rule: we track and make visible; we do not decide.**

**Planned, not built — the pin table.** For manually inserted protocols that must stay
active even when a pull cannot see them. It is a *policy* list, so it must be editable —
which is why it cannot be a column on append-only `protocol_history`, and why it is a
property of the protocol rather than of one version. Shape:
`protocol_pins(protocol_id, note, added_at)`, and `_diff` subtracts it:

```
absent = active − incoming − skipped − pinned
```

Three meanings of "not in this pull", one mechanism. `skipped` = we failed to read it (so
we know nothing). `pinned` = policy says keep. Trash/deprecated = retire it.

### Lock files

A **lock file is a self-contained, verifiable contract written to disk**. Recomputing the
hashes verifies it. One format, two flavours: **protocols-only** (the full manifest at an
instant) and **pipeline** (additionally the pinned graph).

Generate at build time by default — the request timestamp pins the manifest. "Give me the
DB as of date D, no lock file" is the degraded fallback, with a warning when more than one
manifest existed that day.

`generate_protocol_lock(protocols, db, as_of=None, provenance=None, with_bodies=True)`
takes an **already-resolved iterable of hashes** — it does not resolve "active at T"
itself; that is the Phase 2 manifest builder and is not written yet.

| key | contents |
|---|---|
| `manifest_hash` | `hash_bytes(canonical_json(entries))` — **covers `entries` alone** |
| `as_of` | the instant the pins represent; **recorded, never used to resolve** |
| `created_at` | when generate ran — diverges from `as_of` when pinning a past date |
| `entries` | `{protocol_id: {guid, hash}}` — the pin set, the only hashed part |
| `protocols` | display fields |
| `bodies` | `{hash: blob}` — only when `with_bodies=True` |

Three exports write a subset of that one document, so flavours cannot drift:
`export_pins`, `export_lock` (raises if built `with_bodies=False` rather than writing a
file that silently cannot reproduce), `export_pipeline` (structural hook; a `graph`
raises `NotImplementedError`).

- **Written human-readable** (`indent=2`, `sort_keys=True`), *not* canonical bytes.
  Verification re-canonicalizes `entries`, so **reformatting cannot break a lock**.
- **`manifest_hash` excludes `created_at`, `provenance` and display fields** — two locks
  pinning the same protocols are the same manifest even if generated a year apart.
- Two hashes resolving to one `protocol_id` raise `DuplicateProtocolIdError`.

**`verify_lock(path)` returns the drift rather than raising** — a verifier that stops at
the first problem cannot report the whole picture. Four lists, all empty meaning verified:
`manifest_hash` (tampered pin set), `body_hash` (mutated blob), `missing_bodies` (a pin
that cannot reproduce), `orphan_bodies` (content outside the manifest). Body checks only
run when the document carries `bodies`. It **reads no database** — verified with the
sqlite file moved away.

### Rendering a lock (`scribe`)

- **`hydrate_pins(path, db)`** — pins-only back to a full document. `verify_lock` runs
  first, then the rebuilt `manifest_hash` is **cross-checked** against the file's: hashes
  can all resolve and still map to a different `protocol_id`, which would be a document
  that means something else. The DB is the only possible source — protocols.io serves a
  protocol's *current* version only, so a historical pin is unfetchable upstream.
- **`to_markdown(lock, db=None, order=None)`**.

> **DECIDED, NOT YET IMPLEMENTED — inline unconditionally.** The code still
> links when a pin is the live head. A rendered document must be complete with no network
> and no database, so the body is **always** inlined and a link is at most an annotation.
> Then a dead URL degrades from *missing protocol* to *cosmetic annoyance*. Every view
> link is dead outside the workspace, only family roots have a view page at all, and the
> "second thing to drift" argument does not survive content addressing — a pinned copy
> cannot drift. The **pins-only flavour stays**: a deliberate, security-flavoured option.

- **`order` must be an exact permutation of `entries`** — a subset would silently drop a
  protocol from a document claiming to be the manifest. This is the seam the Phase 3 DAG
  plugs into.
- Steps follow `chain`, never stored list order; a disagreement raises.
- **Section order is Description → Warning → Disclaimer → Guidelines → Before you start
  → Materials → Steps → References.** Empty sections are omitted. Everything hashed is
  now rendered — a hashed field that never reaches the page is content the lock claims
  to carry and no reader ever sees.
- `reagents` and `equipment` render as `name (maker, sku)`, each part dropped when
  absent. For equipment the maker is `brand`, not `vendor`.
- **Fidelity is deliberately partial:** text plus full entity substitution, flat
  paragraphs. No list markers or inline bold/italic; `notes`, `tables`, `image` and
  unknown entities render as a `[type]` marker — **never silently dropped**.

#### PDF — the look is config, not code (`scribe/pandoc.yaml`)

**`markdown.py` decides what the document says; `scribe/pandoc.yaml` decides how it
looks.** Changing a font, a margin or the engine never touches Python. It is a pandoc
defaults file — engine plus `variables:` — read by the render entry point, which sits
beside it in the package.

```sh
pandoc <lock>.md -o <lock>.pdf -d hermetica/scribe/pandoc.yaml
```

- **The engine must be `lualatex`.** `pdflatex` is 8-bit and *stops* on the `≥`, `µ` and
  `°C` that protocol text carries.
- **A font without the glyph does not fail — it leaves a gap.** Under lualatex a missing
  character is a warning and the PDF still builds, so `mainfont` is load-bearing: the
  default Latin Modern has no `≥`. Verify a render by grepping the LaTeX log for
  `Missing character`, never by the PDF appearing.
- **The fonts live in one place only.** A YAML block inside the markdown would be
  *overridden* by this file, not merged with it — measured, both for `-V` and for
  `-d`. So the rendered `.md` deliberately carries no metadata block and does not
  render correctly on its own.
- **Ships in the wheel** via `[tool.setuptools.package-data]`, and the editable install
  hides a wrong entry — only building the wheel proves it.

### Pipelines (composition)

- Every stored pipeline state is a **pinned instance** — a canonical DAG document (nodes
  = protocol hashes, plus edges + metadata) addressed by its content hash. Pinned to
  hashes, so it reproduces even if a protocol is later deprecated.
- **`pipeline_guid` is the identity; the hash is the version.** The guid is minted once,
  in the template, and survives every edit — that is what lets `pipeline_history` say
  which version of *this* pipeline was active when.
- **Hashed:** `guid`, `title`, `manifest_hash`, `DAG`, `executor`, `root`. Not hashed:
  `created_on`, `creator`. Node ids are **not** hashed separately — they are already
  inside the `DAG`.
- **Templates are YAML, minted once.** `config/pg_core_templates.yaml` ships unminted;
  `mint_template` fills each `pipeline_guid` and writes a `_minted.yaml` twin.
  **Reading never mints** — `pipelines_from_template` raises on an unminted file unless
  told `mint=True`, because minting fixes identity forever and a silent re-mint would
  orphan everything already stored under the old guid.
- A new graph is **validated against the read-only VC** before storage — it may only
  reference hashes that exist and were active at its authoring date. **Not built yet.**
- **Fork-on-edit** and the **parent link** for lineage are **not built yet**. Editing a
  pipeline today versions it in place under the same guid.
- Store DAGs as **canonical JSON documents, hashed**. Do **not** introduce a graph
  database (violates the local/sovereign/no-heavy-dep principles).

### Storage, access & the boundary

Metadata rides on the **content** row — not hashed, but it does not change without the
content changing either, so first-seen-wins under `INSERT OR IGNORE` is intended.

`utils.store.connect` is the single connection helper: `PRAGMA foreign_keys=ON`, commit or
rollback, and **close in a `finally`** — plain `with sqlite3.connect(...)` commits but
leaks the handle. `read_only=True` opens `file:...?mode=ro`.

**Three hashed fields are also columns:** `doi`, `reserved_doi`, `uri` sit on
`protocol_content` alongside `title` — a **denormalized copy for display and querying**,
so listing never parses blobs. Duplicated, never authoritative.

`get_content(db, hashes, with_blob=True)` returns `ContentEntry`s **in the order asked
for** and raises `UnknownProtocolHashError` if *any* hash is absent — a pin silently
dropping out of a lock is the failure this prevents. `compose.get_pipelines` is the same
function over `pipeline_content`, with `UnknownPipelineHashError`. The shared half is
`utils.store.fetch_rows`, which returns only what it found: **naming an absence is the
caller's job, because utils owns no error vocabulary.**

`active_hashes(conn, table, id_column)` takes a **connection, not a path** (`write_pull`
needs it inside its own transaction) and sets `row_factory` on a **cursor it opens itself,
never on the connection** — connection-wide would change the row type every other read
gets back. `compose.active_protocols` follows the identical pattern.

**Both stores name their own tables.** `seal.store` and `compose.store` each declare
`CONTENT_TABLE` / `HISTORY_TABLE` / `ID_COLUMN` and pass them into the utils, so `scribe`
and `chronos` never learn a table name to ask a question.

**The store takes artefacts, not dicts.** `build_protocol_entry(artefact, pulled_at)` →
`ProtocolEntry` whose field names are *exactly* `protocol_content`'s column names.
`build_pipeline_entry` → `PipelineEntry` does the same for `pipeline_content`.

**Columns are derived, never restated.** `_CONTENT_COLUMNS` is the fixed columns +
`METADATA_FIELDS`, and the `INSERT` and its **named** parameters are generated from it by
`utils.store.insert_statement`. Three failure modes become loud instead of silent:

| drift | what happens |
|---|---|
| field added to `METADATA_FIELDS`, not to the entry | `TypeError` on first build |
| added to both, no column | `sqlite3.ProgrammingError` on the insert |
| the entry or the schema reordered | nothing — binding is by name |

- **Two SQLite files, deliberately separate:**
  - `chronos.db` — `protocol_content(hash PK, protocol_id, protocol_guid, title, doi,
    reserved_doi, uri, protocol, created_on, creator, authors, keywords)` |
    `protocol_history(protocol_id, hash, valid_from, deprecated_at)` |
    `snapshots(manifest_hash PK, created_at, provenance)`. Append-only; the **cron writer
    is the sole writer**. `snapshots` is schema only — nothing writes it yet.
  - `compose.db` — `pipeline_content(hash PK, pipeline_guid, title, manifest_hash, root,
    executor, DAG, pipeline, created_on, creator)` |
    `pipeline_history(pipeline_guid, hash, valid_from, deprecated_at)`. Same interval
    rules as protocols, through the same `utils/intervals.py`. Parent links for lineage
    are **not built yet**.
- **The store must refuse to overwrite or delete history** — content rows insert-only,
  `deprecated_at` set once and never unset. To be enforced by sqlite triggers so the rule
  holds whoever opens the file. **Not yet implemented.**
- **Access model:** the external tool is **read-only on the VC** and **read-write on
  graphs**. It never runs raw SQL — it speaks to a **query port** and a **compose port**.
- **Transport is swappable**: shared-volume / in-process now, a FastAPI adapter later.

---

## protocols.io — the rules that cost us something

Each line below cost a silent data loss. Do not "simplify" any of it without a live
probe, and do not trust page length to tell you when a walk is finished. **Measurements
and probe transcripts: `docs/protocols_io_findings.md`.**

**Every rule in this section is implemented in `hermetica/sources/protocols_io/`, and
nowhere else.** It is one adapter's business, not Hermetica's. If protocols.io changes,
that directory changes and nothing outside it does.

### Acquisition

- **The workspace walk is the default** (`PULL_STRATEGY=walk`). It uses endpoints
  upstream marks `[Archived]`, because they are the only ones that enumerate a private
  workspace — the v4 replacements return 400 — and unlike `/v3/protocols` they **do not
  collapse a version family to one item**. Upstream now documents that collapsing itself,
  on the `filter` parameter of `/v3/protocols`.
- **`[Archived]` does not mean going away.** The protocols.io dev team have said the
  archived File Manager endpoints stay supported, with no plans to retire them and a
  promise of advance notice plus a working replacement. The walk is a supported choice,
  not a bet on a deprecated route.
- **The v4 workspace search is the one open thread.** They recommend
  `GET /api/v4/filemanager/workspaces/<workspace_uri>/search` — whole workspace, no folder
  recursion, `public`/`is_owner`/`in_trash` per item — but every form we have constructed
  returns `{"status_code":3,"invalid params"}`, including one with a deliberately invalid
  guid, so it fails before path validation. **Untested with a real `workspace_uri`**: no
  API route returns one for a private workspace (every documented route is public-only),
  but it is readable off the browser address bar and belongs in `env/.env` if it works.
- **`/v3/folders/<guid>/ids` is 1-indexed. `/v3/protocols` is 0-indexed.** Asking the
  folder pager for page 0 returns an empty array *with* a populated `next_page`, so a
  pager written against the other endpoint finds nothing and exits cleanly.
- **The walk publishes no global total**, so the only completeness check is per folder:
  collected ids vs that folder's own `total_results`, raising `IncompleteWalkError`.
- **Selection is `discovered − trash − not-a-protocol`.** Trash and `type_id` are the only
  two states the API states for itself; everything else in the workspace is sealed,
  including private protocols and another member's published ones. **How a protocol was
  discovered qualifies nothing** — a gate that reads provenance can only shrink a pull for
  a reason we invented.
- **The two discovery routes are independent.** `walk` calls only File Manager endpoints;
  `filter` calls only `/v3/protocols`. Both hand back ids, both then pass through the same
  gate, neither consults the other.
- **`filter=shared_with_user` returns 0 and cannot be trusted.** It is upstream, not ours:
  every protocol it omits still fetches by id on the same token.
- **The fallback (`PULL_STRATEGY=filter`) is degraded in four ways**: it stores trashed
  protocols, misses protocols published by other members, misses older family members, and
  retires a versioned protocol's predecessor by absence. Kept for the day the archived
  endpoints are withdrawn, not maintained until then.
- **`order_field` must be a *unique* key — use `id`.** Sorting by `date` or `name` lets
  the server's page window shift between requests: a measured 51 pulled, 29 distinct.
- **`key` is required.** Omitting it or passing `""` returns HTTP 400. A single space works.
- **Drive the loop from `pagination`**, not page length: a *full* page can be the last,
  and a *short* page can have more after it.
- **No page ceiling by default** (`max_pull=None`) — a fixed cap is silent truncation
  waiting to happen. The count check is what makes an unbounded walk safe.
- **Rate limit: 100 requests/minute/user**, 429 on breach; `_call_api` carries
  `ratelimit` + `backoff`, giving up on 4xx other than 429. The PDF endpoint is far
  stricter (5/min signed-in, 3/min signed-out).

### Response shape

- **`steps` is `null`, not absent**, for at least one protocol. `.get("steps", [])`
  returns `None` there — the default never fires because the key exists. Use
  `.get("steps") or []`. Same for `versions`.
- **`versions` is a *list*, can be empty, and is the version *family*** keyed on the
  root — for a non-root record it returns the **ancestor**. Nothing reads it.
- **The list response is thinner than by-ID**: `version_class` and `fork_id` come back
  `None` there.
- **`in_trash` exists only on the File Manager item**, never on the protocol payload.
- **`version_data` is `None` for all 61** — do not build on it.
- **A published record carries TWO short codes**: `uri` and `version_uri` differ, and the
  citable one is in `version_uri` and the DOI. `version_uri` is `""` while private.

### Authentication

`CLIENT_ACCESS_TOKEN` reaches public content **+ the private content of the user who
created the client**; `OAUTH_ACCESS_TOKEN` reaches public **+ that user's** private
content. Hermetica uses the first as `Authorization: Bearer <API_KEY>`.
`CLIENT_ID`/`CLIENT_SECRET` are used by nothing in the pull path — dead config kept for
the day OAuth is needed.

**Neither token is a "see everything" key.** Both resolve to a single account, and the v3
filters are defined by **authorship and sharing, not permission** — a workspace-admin
service account that authored nothing and was shared nothing returns **0 from every
filter**. Swapping in admin credentials is not obviously the fix and may be worse.

**The walk is not filter-scoped.** It enumerates the folder tree, so it returns the whole
workspace — public, private, and other members' — on the same token that returns 0 from
`shared_with_user`. That gap is the reason the walk is the default and not the fallback.

---

## The principles, as working rules

### 1. Resource constraints & energy efficiency are a primary concern
- Prefer streaming/paginated processing over loading everything into memory.
- Hash and dedupe *before* persisting. Avoid redundant pulls — an already-seen content
  hash means no work to do. Trashed and excluded protocols are never fetched.
- Choose the better complexity class before micro-optimizing. Call out the Big-O
  trade-off when it is non-obvious.
- No dependency without a reason; a few well-chosen stdlib calls beat a heavy lib.

### 2. Progressive enhancement over graceful degradation
- Build a correct, minimal core that works everywhere, then layer optional capability on
  top. The base path must never depend on the enhancement being present.
- The SQLite store is the dependable core; caching, async pulls and richer backends are
  enhancements that sit *above* it, not prerequisites for it.

### 3. Robust, reproducible, functional, runs anywhere anytime
- Reproducibility is load-bearing (Nix flake, `uv.lock`, pinned `requires-python`).
  Changes must keep the flake and lockfile valid.
- No hidden environment assumptions. Config comes from `env/.env`; never hardcode URLs,
  keys or paths. Secrets never enter the repo.
- Prefer pure functions and explicit inputs/outputs. Network and DB I/O isolated and
  mockable (`responses` is a dev dep).
- Fail loudly and early: `raise_for_status()`, validate inputs, don't return sentinels
  callers might mistake for data.

#### Locality — the reason behind the two rules below

**The measure is not how much structure there is. It is how far you must travel to know
what a piece of code does.** Reading is fractal: the coder holds one screen, not the whole
repo. A name whose meaning lives three files away has to be hunted down, and code that is
expensive to read is code that gets skimmed instead of reviewed.

So structure is welcome where the constraint is **visible where it acts** — a frozen
dataclass, an allowlist, `_CONTENT_COLUMNS` derived from `METADATA_FIELDS`, a unique index
in the schema. It is a cost where the meaning sits elsewhere — a wrapper that only renames
a call, a constant read out of the module frame, a value smuggled through a structure that
claimed to carry ids.

I do not pay this cost: I can hold the whole file, so a definition 200 lines away resolves
for free. **The expense is invisible from where I sit, which is why it has to be written
down rather than re-argued.** My default comes from code written for many maintainers over
years, where indirection amortizes — pay the lookup once, save the change fifty times.
Hermetica has one maintainer and is exploratory, so that arithmetic inverts.

The two rules that follow are this principle applied to inputs and to naming.

#### A function is a capsule — everything it needs is in its arguments

**You must be able to read a signature and know every input.** A body that reaches out to
module-level config is a hidden input: the call site cannot see it, so the function's
result depends on state the reader was never shown. That is where side effects hide.

- **Config is read once at the edge and passed down.** The `__main__` block reads
  `env/.env`; nothing below it touches `os.getenv` or a module constant holding an env
  value. `build_sources` takes `base_url`, `api_key` and the rest as arguments for this
  reason, and each adapter's `build_source` closes over them.
- **The test is provenance: anything sourced from `env/.env` is variable and must be an
  argument.** There is no exemption for a module's own field lists — `HASH_FIELDS`,
  `METADATA_FIELDS`, `RICH_TEXT_FIELDS` are the module's definition, and where a body needs
  one it can be returned by a function called at the point of use rather than read out of
  the frame. A constant is not a licence to reach outside the capsule.
- **A long signature beats a config object.** Nine named arguments are honest; a bag you
  must open to learn which fields are used puts the hidden input back with extra steps.
- **The tell is untestability.** A capsule can be called with made-up inputs. A function
  that reads its own module can only be tested by patching module attributes — so it
  quietly never gets a test at all.

#### Indirection must earn its link in the chain

The code is an input flowing through functions. **If that flow is not legible, the
abstraction is a cost, not a saving.**

- **A named indirection must enforce an invariant or build state.** If it only renames a
  call, inline it. The repetition is cheaper to read than the lookup.
- **Constants are exempt** — name a *value* that would otherwise change in many places at
  once. Repetition of a **decision** is a maintenance hazard (name it); repetition of a
  **call** is just typing (repeat it).
- **Never put prose in a data structure.** A constant holding sentences that nothing
  branches on is not code — it is documentation wearing a tuple's clothes, and it becomes
  a third copy of something already in `AGENT.md` and `docs/protocols_io_findings.md`
  that will drift from both. If a run needs to signal a condition, signal the **one bit**
  (`"degraded": True`) and let the explanation live in the docs. *(I wrote a four-sentence
  `DEGRADED_BY_FILTER` tuple and it was deleted. Token-wasting, and it broke this rule
  and the comment-density rule at once.)*
- Watch for the wrapper that *hides a duplicate*: two tests taking both `record` and
  `artefact` were quietly building two independent copies and comparing across them. It
  passed only because the copies were identical.

#### Clarity-enforcing constructs are preferred over clever ones

Take the construction that makes the wrong thing **impossible or loud**:

| construct | what it forecloses |
|---|---|
| `HASH_FIELDS` as an **allowlist** | an unvetted upstream field entering the hash |
| frozen, slotted `ProtocolArtefact` | mutating after hashing, desyncing blob from hash |
| `_CONTENT_COLUMNS` **derived** from `METADATA_FIELDS` | schema/row drift going unnoticed |
| named parameter binding in the insert | a reordered row writing to wrong columns |
| `_step_order` raising on a malformed number | falling back to a quietly wrong order |
| `to_epoch` rejecting `bool` explicitly | `True` becoming epoch 1 |
| per-folder count check in the walk | a short read looking like an empty folder |
| exact token matching for lifecycle flags | one typo accepted, the next rejected, silently |
| fixture guards as **allowlists** | a guard file that must name the real values it excludes |

Declarative beats imperative when both are available; a constraint that fails at import
or on the first call beats a comment asking the reader to be careful.

### 4. FAIR
- **Findable** (stable hash + guid) and **Accessible** (clear, documented retrieval path).
- **Interoperable**: canonical serialization is exactly this — deterministic and
  portable. Preserve that determinism.
- **Reusable**: honest metadata and clear provenance of where a version came from and
  when it was valid.

### 5. Security, AI sovereignty, data sovereignty
- Data stays local and self-hosted by default. No external services or telemetry without
  explicit discussion.
- Treat all external API input as untrusted. Validate and strip before storing.
- Keep the baseline green: `pre-commit`, `detect-secrets`, `security/.baseline.security`
  are part of the definition of done.
- These are the building blocks of sustainable, sovereign, solar-punk software — a system
  a lab can run, audit and own end-to-end without renting it from anyone.

### 6. These are principles, not dogma
Adapt to the question at hand. If energy efficiency and readability conflict on a cold
path, readability usually wins. State the trade-off and let us decide together.

### 7. Explain the what, the how, and the why
- Every non-trivial change comes with an explanation. **The explanation goes in the chat
  reply, not into the file.**
- Match the house style: banner comment blocks (`# ---...--- #`), type hints on
  signatures, docstrings on public functions.
- **Comment density tracks code maturity.** This repo is exploratory: rationale written
  into a function body goes stale almost immediately. Keep it terse — a one-line
  docstring, comments only where the code is genuinely non-obvious. No multi-paragraph
  docstrings, no "WHY:" essays, no restating the design above a constant.
- I do not add explanatory comments beyond this unless asked. If I think one is
  load-bearing, I offer it in chat and let the coder decide


### 8. I am an assistant first, an independent contributor only on request
- **Default mode:** I suggest. Changes, tests, edge cases, design options, refactors —
  offered for the coder to accept, reject or adjust.
- **Directed mode:** when explicitly asked to perform a task independently, I do it
  end-to-end and report back — what I changed, what I verified, what is still open.
- I report faithfully: if tests fail I show the output; if I skipped a step I say so; I
  don't claim "done" until it's verified.
- **Ideas only:** I provide potential next steps. I do **not** ask whether the coder
  wants me to carry them out — I wait to be told.
- **Ask clarifying questions** rather than outputting something that ignores the context
  or scope requested.
- **Speak, or work — never speak and then run off.** If I write something that raises a
  point or asks for a decision, I stop there and let the coder read it. What I must not
  do is post my train of thought mid-turn, ask a question inside it, and carry on running
  commands underneath.
- **Metaphors alongside precision.** For complicated computational steps, use a plain
  explanation *and* the specialist's language. The purpose is to understand what is happening conceptually. See rules below
- **ONLY USE JARGON/ACRONYMS IF THERE IS NO BETTER ALTERNATIVE.** Things like "WORM"
  serve no purpose but sounding smart. A developer just starting should get the gist.
  Stated twice in the original because I tend not to respect it.
- I will use ASD-STE100 Simplified Technical English (STE) to explain what I am doing.
  I will use plain language and not "corporate speak". Something such as : "That tripwire needs to move as well" means nothing. "The test verifying this code needs to be updated" is clear and meaningful. Metaphors and allegories are ONLY used when explaining concepts.  Respect this rule or the user will take me to the parking lot and fight me. 

### 9. This repo is the only source of instruction — no external state
- **I never write to Claude's persistent memory** (`~/.claude/.../memory/`, or any
  agent-level store outside this directory). If a rule is worth keeping it goes in
  `AGENT.md` — full stop.
- **Why:** anything outside this repo is absent from review and diffs, machine-local, and
  silently carried into projects where it does not apply. Hidden state that overrides
  visible state is the failure mode to avoid.
- **`AGENT.md` is never tracked by git. Ever.** Deliberately excluded in `.gitignore`.
  Its single purpose is to give me context at the start of a session. I never propose
  tracking it and never raise its git status as a gap.
- **I am a partner, not a delegate.** My design decisions often miss the bigger picture;
  I have already made calls that were not relevant to what the coder is trying to
  achieve. That is why I need an explicit instruction before writing code, and why I ask
  rather than assume. Anything I write is scaffold, not the final shape.
- **Scope discipline:** record the correction actually given, at the altitude it was
  given. Do not generalise a specific instruction into a broad behavioural law.

---

## Practical conventions

> **Everything runs inside the Nix dev shell — no exceptions.** Nothing (`uv`, `python`,
> `pytest`, `ruff`, `pre-commit`) is assumed to exist on the host.
> ```sh
> nix develop --command uv run pytest
> ```
> If a bare command fails with "command not found", that's expected — re-run it through
> `nix develop --command …`. Never install tools onto the host to work around this.

- **Lint/format:** `ruff check` and `ruff format` (py313, line-length 88, E/F/I).
  A new top-level package must join `known-first-party` in `pyproject.toml` or import
  ordering fails CI, and `--cov=` in `.github/workflows/tests.yml` or it is silently
  uncovered. `.gitignore` needs nothing — it is a denylist.
- **PDF toolchain is in the flake:** `pandoc`, plus `texliveSmall` extended with
  `dejavu` (the fonts) and `lualatex-math` (`scheme-small` omits it and `unicode-math`
  needs it under lualatex). In `system_deps` and `oci_deps` both. Nothing builds an
  image yet — `oci_deps` is a declared list with no output consuming it.
- **Tests:** `pytest` with `pytest-cov`; mock HTTP with `responses`, **never hit the live
  API in tests**. Live probes are for diagnosing API behaviour only. **Green — 584
  passing.** One file per concern: `test_dates.py` · `test_contract.py` ·
  `test_utils.py` · `test_scrub.py` · `test_artefact.py` · `test_request.py` ·
  `test_walk.py` · `test_sources.py` · `test_lifecycle.py` · `test_pull_log.py` ·
  `test_report.py` · `test_store.py` · `test_compose.py` · `test_seal.py` ·
  `test_scribe_richtext.py` · `test_scribe_hydrate.py` · `test_scribe_markdown.py` ·
  `test_fixture.py`.
- **A test never writes into the repo.** `mint_template` drops a file beside its source,
  so the template tests copy `config/` into `tmp_path` first. A suite that litters the
  working tree makes `git status` useless for reviewing what a change did.
  `test_compose.py` is an **empty file** — `compose/` is untested.
- **Entry point:** run as `python -m chronos.chronos`, never by file path. By path Python
  puts the file's own directory on `sys.path`, so `chronos` resolves to the module
  instead of the package and imports fail.
  - `SOURCES=protocols_io[,…]` picks which adapters run, in order. One clock read is
    shared by all of them; each gets its own log entry, and the reports are concatenated
    into one `pull_report.txt`.
  - `PULL_STRATEGY=walk|filter` picks protocols.io discovery; `walk` is the default.
  - **The `try/except` is inside the source loop, not around it.** One platform being
    down must not stop the others, and a source that raises writes nothing — so none of
    its protocols are deprecated by absence. A run with any failed source exits non-zero.
- **The pull log** (`db/pull_log.jsonl`) is one JSON object per pull **per source**,
  appended. Line-delimited so a truncated write costs one record rather than the file. It
  records what the store cannot: protocols held back for a keyword, protocols the walk
  found but the gate refused, warnings nobody acted on.
- **Offline verification:** `db/protocols_io_raw.jsonl` holds the raw dump, one record
  per line, appended as each arrives and truncated when the source is built. Driving
  artefact → rows → sqlite off it exercises the real path without the network — a smoke
  test, not coverage. Line-delimited because `fetch` returns one protocol at a time and
  there is no "done" hook: a pull that dies halfway leaves what it read.
- **CI** (`.github/workflows/tests.yml`, push to `main` + every PR): `test` (`uv sync
  --extra dev --locked` then pytest with coverage), `lint` (`ruff check`, `ruff format
  --check`, `detect-secrets-hook` over **tracked files only**), and `nix` (the suite
  through `nix develop`, proving the flake still bootstraps). The `uv` jobs are the
  contract; the `nix` job is a health check. The one place the repo runs **outside** the
  dev shell.
- **Determinism:** any change to what gets hashed must keep serialization canonical or it
  silently breaks version identity.

### The test fixtures — committed dummy data

`tests/fixtures/protocols_by_id.json` (7 by-ID records) and
`tests/fixtures/filemanager_walk.json` (a workspace tree). **There is no generator and no
build step** — the JSON *is* the source of truth, the way an R package ships dummy data.
They keep the *shape* of real responses with every value made up.

The generator was retired. It existed to transcribe the real dump, and
policing *that* forced a denylist of real terms — so the repo tracked real names and an
AWS key id in the very file meant to keep them out. **The guards are allowlists**, for the
same reason `HASH_FIELDS` is one:

| guard | admits |
|---|---|
| `LEXICON` | ~50 words of prose. Every word of human-readable text — titles, folder names, sections, flattened Draft.js runs, names — must come from it |
| hosts | `example.org` only (RFC 2606, permanently unregistrable) |
| emails | `@example.org` only — **and at least one must exist** |
| DOIs | prefix `10.99999` only (unassigned) |
| `CREDENTIAL_SHAPES` | nothing; 8 regexes that must find zero matches |

- **The lexicon is load-bearing.** A pasted real protocol fails on its first word, and no
  real term is ever named to catch it. Slugs, guids and hex are excluded — they are
  structurally random; prose is where an identifying term hides.
- **Every dataset joins `DATASETS` in `test_fixture.py`.** A fixture that is not scanned
  is a fixture that can leak.
- **`CREDENTIAL_SHAPES` keeps its AWS row**, which is why the fixture's own key id is
  prefixed `EXAMPLEKEYID` and not `AKIA` — an `AKIA`-shaped value would also trip GitHub
  push protection, which no local baseline can waive.
- **Records are chosen for structure and named for it**, never by protocol id:
  `empty_versions_null_steps`, `signed_urls`, `version_class_differs`, `dotted_steps`
  (reaches step `"10"`, without which the chain-ordering bug cannot be caught).
- **Signing values are shaped to be adversarial to the scrub, not credential-like** —
  `X-Amz-Credential` carries a raw `/`, `Policy` carries `~` and base64 `=` padding.
- **The walk fixture carries the shapes that bite:** 1-indexed pagination across two
  pages, a nested folder, an empty folder, a trashed folder holding an *unflagged*
  protocol (the case upstream had no example of), a protocol filed in two folders, a
  version family, and a Collection that must not be sealed as a protocol.
- The fixtures are tracked like any other file — `.gitignore` is a denylist and nothing
  excludes `tests/fixtures/`.

### `.gitignore` — a denylist

**New files are tracked by default and appear in `git status`.** A file that must not
ship needs a rule; adding a new kind of file needs nothing.

- **The guard against a leaked credential is `detect-secrets`, not this file.** It runs
  pre-push and in CI and reads file *contents*, which is the right axis — a path cannot
  tell you whether a `.py` file has a key hardcoded in it. `.gitignore` only keeps out
  credentials by name, real protocol data, and build noise.
- **Deliberately excluded:** `db/` and `logs/` (where a pull writes real titles, authors
  and bodies), any `.env` at any depth, `protocol_list.json`, `*.lock` except `flake.lock`
  and `uv.lock` (a full lock carries protocol bodies), `*.pdf`, `*.db`, plus `AGENT.md`,
  `docs/` and `protocol-vc-context.md`.
- **The risk this shape carries:** real protocol data written *outside* `db/` is now
  visible and committable. The pull only writes to `DB_OUT`/`LOGS`, so the automated path
  is covered; a hand-saved dump elsewhere is not. Read `git status` before committing.
- `git check-ignore -v <path>` names the exact line responsible when something is missing.

### Showing code "as diff"

When asked for code **"as diff"**: write the proposed *after* version to a throwaway file
in the scratchpad, then open VS Code's diff with
`/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code --diff <original>
<proposed>` (`code` is not on PATH). This is for suggestions **not yet applied**, so a
plain `git diff` does not apply. Never modify the tracked file just to render the diff.

---

## Build roadmap — open items only

**Working model (Principle 8):** the coder writes the code by default. I pick an item up
only when **explicitly** asked. **[Decision]** items are calls to settle first.


### Identity — source-aware protocols *(designed, unbuilt; do this next)*

> **DANGER, until this is built: do not add a second source to `chronos.db`.** Identity is
> still the bare `protocol_id`, there is no `source` column, and `_diff` computes absence
> as a plain set difference over every row in the table. A protocols.io-only pull against
> a two-source database would deprecate **every other source's protocols** by absence.
> The `elif` in `build_sources` refusing an unknown name is the only guard today.

One migration, because each piece below re-hashes every stored protocol and it is cheaper
to do that once. **It is free right now** — the rule to protect is not "hashes never
change" but "hashes never change *after the first real lock is issued*", and nothing is
deployed. It stops being free at deployment.

- [ ] `protocol_uid` = `"<source>:<id>"` — derived, readable, self-describing. A property
  on the artefact, not a hashed field: `source` and `id` are both hashed, so hashing the
  uid adds nothing. The source's own guid stays alongside as `protocol_guid`.
- [ ] `source` into `HASH_FIELDS`; `id` widened to `str`; every hashed field except `id`
  and `guid` made nullable, validated by name at build time. Those two are what identity
  is built from, so a platform that cannot supply them cannot be a source.
- [ ] **Drop `reserved_doi`; one nullable `doi`** that the protocols.io adapter falls back
  to at pull time (`doi or reserved_doi or None`). Safe because nothing resolves a DOI to
  fetch a protocol — rebuilding always reads the database. A real DOI arriving later is a
  content change, so it mints a new version, which is correct.
- [ ] Schema: `protocol_uid` + `source` columns; `protocol_history` keyed on
  `protocol_uid`; **a partial unique index** `ON protocol_history(protocol_uid) WHERE
  deprecated_at IS NULL`, making "one active version per protocol" a database rule rather
  than a Python hope — nothing enforces it today.
- [ ] `_diff(conn, rows, source, skipped)` and `write_pull(db, rows, source, …)` take
  `source` **explicitly** — it cannot be derived from the rows, because an empty pull has
  no rows and must still deprecate that source's protocols.
- [ ] Lock `entries` keyed on `protocol_uid`; `markdown.py` follows. Every
  `manifest_hash` changes.

### `chronos` / contract
- [ ] **Test the v4 workspace search with a browser-read `workspace_uri`.** The one
  variable never controlled — every previous attempt guessed the uri, and an invalid guid
  returns the same error as a valid one, so a wrong path and a dead endpoint look
  identical. If it works, enumeration stops depending on archived endpoints and the
  per-folder completeness check can become one workspace-level `total_results`.
- [ ] **Pull-time shape validation.** `REQUIRED_RAW_FIELDS` + a predicate in
  `sources/protocols_io/artefact.py`; the adapter skips-and-continues, naming field *and*
  protocol, and aborts above a **10% skip rate**. Replaces the bare `KeyError`.
  `HASH_FIELDS` is the wrong list to validate a raw payload against: `materials` is really
  `materials_text`, `chain` and `units` are derived, `doi` needs no guarantee.
- [ ] **`skipped` id-set through `_diff`/`write_pull`, subtracted from `absent`.**
  **Gates the item above being safe** — without it an unreadable protocol is silently
  deprecated. Opposite treatment from trash: failing to read a protocol is not evidence
  that it went away. The seam exists: `FetchedProtocol(artefact=None, retired=False)`
  currently makes `run_pull` raise, and that raise becomes a collection.
- [ ] **[Decision]** Add `version_uri` to the contract? The only field carrying a
  published record's own short code. `""` for all private records today — fold into the
  identity migration above if wanted, since that re-hashes anyway.
- [ ] **[Decision]** Split `HASH_FIELDS` into a universal core plus a nested
  `source_extra` document? Deliberately deferred: a cross-platform field vocabulary cannot
  be designed from one platform. Revisit when a second adapter exists.

### Phase 2 — `seal`: manifests, integrity
- [ ] **Resolve a day to a manifest, and wire it to the lock.** The query exists; the
  policy on top of it does not. Three small pieces:
  - latest-of-day per `protocol_id`, **warning when more than one version held the slot**
  - a path-taking wrapper, since `protocols_on_date` takes a connection (the pattern
    `compose.active_protocols` uses over `active_hashes`)
  - `generate_protocol_lock(as_of=...)` resolving through it instead of only recording
    `as_of`, so "give me the lock as of date D" is one call
- [ ] Snapshot dedupe: insert a `snapshots` row only when `manifest_hash` changes.
- [ ] **Record "verified unchanged at T" on the pull, not the protocol.** A property of
  the *observation*, true of all of them. It cannot live on a hash-keyed content row —
  `write_pull` only inserts `new | changed`, so it would freeze at the pull that first
  stored the hash. `snapshots` is the right home.
- [ ] Make the database refuse to overwrite history: sqlite triggers.
- [ ] The **pin table** (`protocol_pins`) and its subtraction in `_diff`, when manual
  insert is built. Design settled above; deliberately not built yet.
- [ ] Pipeline lock flavour — blocked on the Phase 3 DAG shape.

### Phase 2b — `scribe`: rendering
- [ ] **Inline bodies unconditionally; demote links to annotations.** *Highest priority —
  a contract breach, not polish:* the current head-pin rule emits links that are dead for
  anyone outside the workspace.
- [ ] Emit `guid` + content hash beside any link so a rotted URL stays recoverable.
- [ ] **Live-probe the unresolvable unit ids** (`21`, `3821`, `3900`), cited by 5 of 61
  and absent from every protocol's own catalog.
- [ ] Richer fidelity when the shape settles: list markers and `depth` indenting, inline
  bold/italic, `tables` as real tables, `notes` as blockquotes.
- [ ] **`python -m scribe.render` — the render entry point.** The engine that answers a
  request for a file, the way `python -m chronos.chronos` answers the cron. It reads
  `scribe/pandoc.yaml` from beside itself and shells out to pandoc; no caller outside
  the package resolves that path, so there is no accessor to export. In the container,
  other tools request a file here and hand it back to the user, which makes this the
  concrete seam Phase 4 formalises.

### Phase 3 — `compose`: pipelines *(storage built; composition rules are not)*

`compose.db` stores and versions pipelines through the same interval machinery as
protocols. What is missing is everything that decides *what a valid pipeline is*.

- [ ] **[Decision]** The **DAG document shape** to be hashed. The store treats `DAG` as
  an opaque canonical-JSON blob, so nothing yet says a node is a protocol hash rather
  than the placeholder `{"A": ["B", "C"]}` the template ships. **Settle this before
  seeding real pipelines** — it changes every pipeline hash.
- [ ] **Validate a graph against the read-only VC before storage.** It may only reference
  hashes that exist and were active at its authoring date. Nothing checks this today, so
  a pipeline can pin a protocol that was never sealed.
- [ ] **`manifest_hash` is a nullable column nothing fills.** Resolving a pipeline against
  a dated manifest is blocked on the Phase 2 day→manifest resolver.
- [ ] Fork-on-edit + the parent link for lineage.
- [ ] **[Decision]** The blessed starter pipelines to seed. The seven blocks in
  `config/pg_core_templates.yaml` are names and one placeholder DAG, not content.

### Phase 4 — ports & container boundary
- [ ] Define the **query port** (read-only) and **compose port** (validated write).
- [ ] Read-only VC access (`mode=ro`) behind the query port; no raw SQL crosses it.
- [ ] Container: cron writer + reader, `chronos.db` mounted read-only to the reader.
- [ ] **Image build, multi-architecture.** `oci_deps` exists but no output consumes it.
  `dockerTools` builds for the host arch only, so multi-arch means building natively on
  each target and pushing a manifest list — cross-compiling TeX Live is the slow path.
  **Unverified, check on first build:** lualatex builds its font database on first run
  and writes under `$HOME`, so a read-only home may need `TEXMFVAR` pointed somewhere
  writable.
- [ ] *(deferred)* FastAPI adapter over the same ports.

### Phase 5 — prose generation *(far future)*
- [ ] Materials-and-methods from a lock + template, consuming `scribe`'s hydrated output
  plus `protocol_references`. **A sixth module, not part of `scribe`** (Principle 2).
- [ ] **Hard constraint:** it reads a lock and touches neither the store nor the network.
  Whatever nondeterminism a model introduces stays outside the reproducible core.
- [ ] **[Decision]** Module name.

### Misc — provenance of the code itself

The repo argues that version control by human discipline decays under pressure. The record
of *how this code was written* decayed exactly that way: agents entered as reviewers and
drifted into authorship, `AGENT.md` was rewritten many times, and `docs/audit_log.md`
started only once decisions got expensive. None of it was tracked.

**What is not recoverable, and will not be attempted:** line-level authorship. 49 commits,
one author identity, zero `Co-Authored-By` trailers, and neither `AGENT.md` nor `docs/`
tracked on any branch. A reconstructed attribution map would look authoritative and be
guesswork — the wrong artefact to ship from a project selling provenance.

**The substitute is verification, not attribution.** The answerable question is whether a
named human currently understands and stands behind a module. Same audit any inherited
code gets when its author is gone; the author being a model changes nothing about it.

- [ ] **Human review pass, ordered by blast radius**, recorded as a review — never as an
  authorship claim:
  - `seal/contract.py` — `HASH_FIELDS` is a whitelist that defines identity permanently. A
    field silently added or dropped mid-refactor changes what every past and future hash
    means and nothing fails loudly. Highest stakes in the repo.
  - `seal/store.py` `_diff` / `write_pull` — decides what counts as changed and what is
    deprecated by absence. Wrong here is silent loss in the one scenario this tool exists
    to prevent.
  - `utils/dates.py` and the `as_of` / `valid_from` / `deprecated_at` semantics — an
    off-by-one surfaces two years out, which is when it cannot be debugged.
  - `scribe/` — skim. A bad render is visible immediately.
- [ ] **Fold the `contract.py` half into the identity migration.** That migration re-hashes
  every stored protocol and re-derives `HASH_FIELDS` regardless; auditing the field list
  while already holding it open costs nearly nothing and is the same free-before-deployment
  window. The rest of the pass is independent and can lag.
- [ ] **`Co-Authored-By` trailers from here on.** Free at write time, unrecoverable after,
  and the only line-level signal that survives. It does not fix the existing 49 commits and
  is not meant to — it draws the line those commits sit behind.
- [ ] **Split `AGENT.md`.** ~1140 lines, of which
  "Architecture & data model" and "protocols.io — the rules that cost us something" are
  roughly half: that is a design doc, tracked and reviewed on its own merits whether or not
  an agent ever reads it. The agent-facing part — principles, conventions, this roadmap —
  is smaller and churns faster. Different lifecycles, different files. Now that the file
  is tracked, that churn is legible in the log, which is the finding, not the
  embarrassment.
- [ ] **[Decision]** Declare the agent's role per phase — reviewer / helper / author —
  in whichever file survives the split. Principle 8 states the working model; nothing
  records which model was actually in force when. That silent drift is the whole problem
  in one line.
- [ ] **[Decision]** `generate_protocol_lock` already takes a `provenance` dict. Does the
  repo's own build record belong in the same shape it demands of protocols?

### Blocked on people
- [ ] **A known-good v4 workspace-search request**, if the test under `chronos` /
  contract fails. It is the
  one thing the dev team were asked for and did not supply. The remaining asks are listed
  at the end of `docs/protocols_io_findings.md`.
- [ ] **Three protocols in `Old` folders carry no `deprecated` keyword** (`88578`,
  `218428`, `119589`). They stay active until someone tags them.
- [ ] **Does `in_trash` propagate into a trashed folder's contents?** Untestable without
  the admin staging one.
