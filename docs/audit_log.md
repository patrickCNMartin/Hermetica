# Audit log — decisions and discoveries

Append-only. Newest last. **Not read by default** — only when the history of a decision
is actually needed. `AGENT.md` holds the current state, `protocols_io_findings.md` holds
the evidence; this file holds *when* and *why*.

One entry per major decision or discovery:

```
## YYYY-MM-DD — <hash> — <title>
**Discovered / Decided:** what.
**Why:** the reasoning, including what was rejected.
**Cost:** what it broke, re-hashed, or left open.
```

**The entry is written after the commit it describes**, so the hash names the commit that
actually contains the work — not the one before it. This file is tracked, so an entry is
committed one commit behind the work it names; that lag is what keeps the hashes real, and
it is what makes every `## YYYY-MM-DD — <hash> —` header checkable: the commit exists and
contains what the entry claims. A decision that changes no tracked file still takes the
hash of the commit it sits beside, and says so.

---

## 2026-08-14 — d4f4ff1 — `filter=shared_with_user` went to zero

**Discovered:** the nightly pull sealed 2 protocols out of 67. The walk was correct — it
found all 67. `/v3/protocols?filter=shared_with_user` returned **61 records on 2026-08-12
and 0 on 2026-08-14**, with the calling code byte-identical between the two runs
(`git diff cd2a4ba HEAD -- request_utils.py` is empty, `PULL_PARAMS` unchanged). Selection
admitted only the 2 protocols that pass the `public` clause.

**Probed:** walked the workspace and fetched every id found, with no filter. All 67
returned HTTP 200, body `status_code: 0`, a returned `id` matching the requested one, and
built a `ProtocolArtefact` without error. 9 trashed, 0 deprecated keyword tags, `type_id`
1 for all 67.

**Why it matters:** that rules out the only explanation that would have been ours to fix.
The token has not lost access to anything. The list endpoint and the by-id endpoint
disagree about one account, which is upstream. Not yet distinguished: whether protocols.io
changed what the filter means, or whether the sharing records themselves were altered.
The dev team has been asked; no reply yet.

**Cost:** the call fails silently. `fetch_protocol_list` accepts an empty reply as a true
answer, so a total loss of the list looks exactly like a workspace where nothing is
shared, and the pull still reports `outcome OK`.

---

## 2026-08-14 — d4f4ff1 — selection stops reading provenance

**Decided:** selection is `discovered − trash − not-a-protocol`. The
`(shared_with_user ∪ shared-family ∪ public)` admission clause is removed. The walk no
longer calls the list endpoint at all; `walk` and `filter` are independent routes that
both return ids and then pass through the same gate.

**Why:** the walk answers the question the project actually asks — what is in our
workspace. The shared/family/public clause existed only because the list endpoint was once
the sole discovery route and it collapses a version family to one item. With the walk
enumerating items directly, the family clause patches a blindness the walk never had. Any
gate reading provenance can only shrink a pull for a reason we invented; trash and
`type_id` are the only two states the API states for itself. Rejected alternative: keep
the list call as a warning rather than a gate — it adds a call and a failure mode for a
signal nothing acts on.

Also removed as consequences: `Selection.admitted_by` (it existed to name protocols
admitted by the family inference; with no inference every entry is the same string, and
the sealed set is already in the pull log by id via `_diff`), and `WalkItem.version_class`,
`.public`, `.guid`, `.uri` — discovery yields ids, and the by-ID record is the only source
of content. `WalkItem` keeps `title` and `path` because the two warning messages are
unreadable without them.

**Cost:** 480 tests (was 482: four selection tests removed, two added). The next real pull
writes 56 new rows, each backdating to its own `created_on` because none of those
`protocol_id`s has any history. The coder's three private PCR protocols (`317763`,
`317764`, `317766`) are in that 56 — accepted deliberately. The `filter` fallback is
untouched and stays degraded in the four documented ways; it will be worked on when the
archived endpoints actually break.

**Open:** a protocol filed in no folder is invisible to the walk and nothing reports it
missing. The per-folder count check guards each folder against a short read; there is no
check for a protocol outside the tree.

---

## 2026-08-14 — d4f4ff1 (no tracked file changed) — this file exists

**Decided:** `CLAUDE.md` carries no history — no dates, no "decided on", no record of what
a rule replaced. Those move here.

**Why:** an audit trail runs for pages and says nothing about where the project stands
today. Mixed into `CLAUDE.md` it does active harm: the file contradicts itself, superseded
rules read as live ones, and tasks get invented from decisions that were already closed.
`CLAUDE.md` must be readable as true right now without dating any line of it.

**Cost:** seven date stamps stripped from `CLAUDE.md` in the same pass. One had an argument
for staying — the re-check date on the `docs/protocols_io_api.md` mirror, which tracked how
stale that mirror is. That is operational rather than historical, and it is now recorded
nowhere. If mirror freshness needs tracking, findings is the place.

---

## 2026-08-14 — a7d2e37, a9788ec — PDF rendering: lualatex in the flake, the look in a YAML

**Decided:** rendering a lock to PDF goes through `pandoc --pdf-engine=lualatex`, and the
aesthetic lives in `hermetica/scribe/pandoc.yaml` — a pandoc defaults file carrying the
engine and the fonts — not in Python. `markdown.py` produces content only; its signatures
are unchanged from `d4f4ff1`.

**Why:** `pdflatex` is 8-bit and stops outright on the `≥` (U+2265) protocol text
contains. lualatex sets it, but only from a font that has the glyph, and there the failure
mode inverts: a missing character becomes a *warning* and the PDF builds with a gap. So
the engine and a covering font are one dependency, not two.

The look was briefly Python — a `PDF_METADATA` dict written as a YAML block at the top of
the rendered markdown (six tests). Removed the same day. Measured against a document
declaring its own `mainfont`:

```
pandoc probe.md -s -V mainfont='TeX Gyre Pagella'   ->  \setmainfont[]{TeX Gyre Pagella}
pandoc probe.md -s -d defaults.yaml                 ->  \setmainfont[]{TeX Gyre Pagella}
```

The outer setting wins in both channels, silently. So a block in the document cannot act
as a fallback behind a defaults file — keeping both means the document always loses. One
home per thing was the only coherent option, and the coder chose the config file: changing
a font must not be a code change. Cost accepted: the rendered `.md` no longer renders
correctly on its own. It is a build product in `db/`, not something handed to a
collaborator.

Rejected: a static YAML next to a surviving `PDF_METADATA` (two copies, drift on first
edit); repo-root placement (nothing to ship in the wheel, and the file belongs to the
module that reads it); an exported `pandoc_defaults()` accessor (`scribe.render` will read
the file from beside itself — no caller outside the package ever names that path).

**Cost:** `flake.nix` gains `texliveSmall` extended with `dejavu` and `lualatex-math`, in
`system_deps` and `oci_deps` both, plus `pandoc` in `oci_deps` which was missing it. Two
things came out of probing rather than reading: `scheme-small` does **not** carry
`lualatex-math`, which `unicode-math` needs under lualatex; and no TeX Live package ships
"DejaVu Math TeX Gyre", so the intended `mathfont` was dropped — `≥` in prose is set by
`mainfont`, and in math mode by Latin Modern Math, which `scheme-small` already has.
`pyproject.toml` gains `[tool.setuptools.package-data] scribe = ["*.yaml"]`;
`.gitignore` gains `!hermetica/scribe/*.yaml`. Tests return to 480 (the six were deleted
with the feature). Verified: `\setmainfont[]{DejaVu Serif}` in the generated LaTeX, **0
`Missing character`** in the LuaLaTeX log, and `scribe/pandoc.yaml` present in a built
wheel — the editable install hides a wrong `package-data` entry, so only the wheel proves
it ships.

**Open:** nothing builds an image yet — `oci_deps` is a declared list with no output
consuming it, and multi-architecture is deferred to that work. `python -m scribe.render`,
the entry point that will read the YAML, is not written.

---

## 2026-08-19 — ee551d3 — the source seam: `chronos` stops speaking protocols.io

**Decided:** the two-way split becomes three. `chronos` decides *when* to look and drives
the write; a **`sources/` adapter** turns one platform's bytes into a `ProtocolArtefact`;
`seal` decides what an artefact *means*. A platform is reached through
`ProtocolSource(name, discover, fetch)` — two callables plus the name they were built for.
Adding a platform is one directory under `sources/`, one branch in `build_sources`, and
the env vars it needs.

Prompted by outside interest in the project and the question *"is this specific to your
platform?"* Hermetica never depended on Kantele, but it did depend on protocols.io's
response shape.

**Why:** the coupling was not where it looked. `chronos/utils/` was the obvious half. The
harder half had leaked into `seal/contract.py` — `build_protocol_artefact`,
`scrub_signed_urls`, `get_steps`, `get_step_chain`, `get_unit_map` all read
`materials_text`, `version_class`, Draft.js envelopes and AWS signing parameters. So
`seal` did not decide "what the bytes mean", it decided "what a *protocols.io* response
means", and anyone evaluating the project for another platform would have found that.

The seam already existed conceptually — *the artefact, never a raw API dict, is what
hashing and writing consume*. Only the thing that **builds** the artefact was filed on the
wrong side of it.

Rejected, with reasons:

- **`sources/` under `chronos/`** (the coder's first sketch). If the adapter owns the
  artefact builder and lives in `chronos`, then `chronos` owns content shape again — the
  exact thing the architecture forbids. A third top-level directory makes the three-way
  split visible.
- **A class with `discover`/`fetch` methods.** A config bag with methods. Two callables
  closed over their own config keep the capsule rule; the name is bundled with them
  because it is written to the store as part of identity, and passed separately it could
  disagree with them.
- **A plugin registry / `entry_points`.** An indirection with two known members does not
  earn its link. An `elif` chain is honest.
- **A shipped `sources/filesystem/` adapter.** Dropped after the coder asked what it was
  for and the answer did not survive the question: a **source is an input** (content
  before Hermetica has hashed it) and a **lock is an output** (content already hashed), so
  a lock can never be a source, and a filesystem source is not storage for pulled
  protocols — it is hand-entry, which is the unbuilt manual-insert item. The interface is
  instead proven by a fake `ProtocolSource` in `test_sources.py`: second implementation,
  zero product surface, nothing to mistake for a storage location.
- **`parse_rich_text` moving with the rest.** Both the adapter (finding units at pull
  time) and `scribe/richtext.py` (rendering) use it. Moving it would make `scribe` import
  from `sources`, which is the wrong direction. It stays in `seal`; its body is
  format-neutral.

**Cost:** `seal/contract.py` went 320 → 157 lines. `screen_deprecated` became
`screen_protocol` — `fetch` handles one protocol at a time, so the list-shaped screen had
no caller left; 4 tests rewritten, the 12 on `seal.lifecycle` untouched.
`check_protocol_integrity` and `MissingHashField` were **deleted** rather than moved: zero
references anywhere, and the predicate was inverted — it raised on every key the payload
*had*. The raw dump changed from one `json.dump` of a list to `db/protocols_io_raw.jsonl`,
appended per record, because `fetch` returns artefacts one at a time and there is no
"done" hook; that also fixes what was on disk, where a debugger-aborted run had left a
3-record file where 60 were expected. `db/chronos_protocols.json` is now stale and
unwritten. `pyproject.toml` gains `sources` in `known-first-party`; CI gains
`--cov=sources`.

`build_sources` was written twice. The first version read `BASE_URL`, `API_KEY` and the
rest from module scope; coverage came back **0% on that function** — exactly the outcome
the capsule rule predicts, that such a function "can only be tested by patching module
attributes, so it quietly never gets a test at all." Rewritten to take every value as an
argument, it has four tests.

Verified: 480 → 509 tests, all passing. Phases 1 and 2 were meant to be a pure move, and
that was checked rather than asserted — the original `contract.py` was pulled out of git
and every moved function and constant compared after `ast.unparse`; all 7 functions and
all 4 constants came back identical. `sources/` coverage: `contract`, `protocols_io/
__init__`, `artefact`, `client`, `lifecycle` at 100%, `discover` at 93%.

**Open — and this is the dangerous one.** Identity is still the bare `protocol_id`, with
no `source` column and no `UNIQUE` constraint anywhere in the schema. Two platforms can
both call a protocol `88578`, and `_diff` computes absence as a plain set difference over
every row in the table. **A second source must not be added to `chronos.db` until
`protocol_uid` and the per-source absence diff exist** — a protocols.io-only pull against
a two-source database would deprecate every other source's protocols by absence, which is
silent history damage. The `elif` in `build_sources` refusing an unknown name is the only
thing standing in the way today.

Also open, all settled in design but unbuilt: `protocol_uid` = `"<source>:<id>"`,
`source` added to `HASH_FIELDS`, `id` widened to `str`, every hashed field but `id` and
`guid` made nullable, `reserved_doi` dropped in favour of one nullable `doi` that falls
back to it at pull time, and a partial unique index making "one active version per
protocol" a database rule rather than a Python hope. Together these re-hash every stored
protocol. That was accepted as **free right now**: the rule to protect is not "hashes
never change" but "hashes never change *after the first real lock is issued*", and
`db/chronos.db` holds 60 protocols with the only two `.lock` files rewritten on every run.
It stops being free at deployment.

The DOI decision reverses a rule this file recorded earlier — `doi` must never fall back
to `reserved_doi`, because an unissued DOI would look issued. That rule assumed something
would resolve the DOI to fetch the protocol. Nothing does: rebuilding always reads the
database, never protocols.io. The DOI is a reference the user may set to anything, and a
real DOI arriving later is a content change, so it mints a new version — which is correct.

---

## 2026-08-19 — 4c4cb4a — `.gitignore` from allowlist to denylist

**Decided:** the "selective git" allowlist (`*` then `!` re-includes) is replaced by an
ordinary denylist. New files are tracked by default and appear in `git status`; only
credentials by name, real protocol data, and build noise are excluded.

**Why:** the allowlist was a **path-based guard against a content-based risk**. A path
cannot tell you whether a `.py` file has a key hardcoded in it — that one was whitelisted
and sailed straight through — while `hermetica/scribe/pandoc.yaml`, which can leak
nothing, needed its own rule to ship. The thing actually protecting the repo is
`detect-secrets`, pre-push and in CI, which reads contents. That is the right axis, and it
is what caught a real key in a supposedly synthetic fixture.

What the allowlist did uniquely well was keep bulk data out. But that data lands in known
directories the coder controls (`DB_OUT`, `LOGS`), so a handful of ordinary rules cover it
completely. The open-endedness argument is strong for upstream API fields, which can
appear any night without warning; it is weak for one's own directory layout, which only
changes deliberately.

The deciding cost is collaborative: the allowlist's failure is **silent**, and it scales
with every contributor who does not know the pattern exists. Nobody the coder works with
uses it.

**Discovered while deciding:** `CLAUDE.md` claimed `git status` shows a whitelisted-out
file as `??`. **It does not.** Measured in a throwaway repo with this repo's own
`.gitignore`: once a directory holds tracked files — every directory here does — plain
`git status --short` prints *nothing* for an ignored file inside it. The `??` appears only
when git collapses a wholly-untracked directory. So the allowlist's one documented
mitigation had been inert the whole time. `git status --short --ignored=matching` and
`git check-ignore -v <path>` are the working checks, and are now in `CLAUDE.md`.

**Cost:** verified to be zero. `git ls-files` captured before and after is **identical at
58 files**, and `git add -An` would add nothing new. Checked path by path: 18 sensitive
paths report ignored (`env/.env`, every file in `db/`, `protocol_list.json`, a stray lab
PDF, `CLAUDE.md`, all of `docs/`, caches, `.venv`); 13 tracked-on-purpose paths report
clear, including both fixtures and `hermetica/scribe/pandoc.yaml`, which no longer need
their own `!` rules. Hypothetical new files (`sources/kantele/adapter.py`, a `Makefile`,
`seal/schema.sql`) now show in `git status`, which was the point. 509 tests unaffected.

**Open:** real protocol data written *outside* `db/` is now committable. The pull only
writes to `DB_OUT`/`LOGS`, so the automated path is covered; a hand-saved dump elsewhere
is not, and `git status` before committing is the only guard. Recorded in `CLAUDE.md`.

**Not a change of principle.** The allowlist instinct is correct where the space of bad
inputs is genuinely open-ended *and* the failure is irreversible — `HASH_FIELDS` (upstream
can add a field any night, and a bad hash invalidates everything on disk) and the fixture
`LEXICON` (a leaked name cannot be recalled) both qualify and both stay. A repo's own file
extensions qualify on neither count. Same tool, three cases, applied uniformly where it
should have been tested case by case.

---

## 2026-08-22 — d401d05 — protocols.io dev team reply: archived endpoints stay; `modified_on` and upstream version keys rejected

**Discovered:** the protocols.io dev team answered the asks listed at the end of
`protocols_io_open_questions.md`. The API documentation was re-fetched the same day to
check the reply against what upstream currently publishes.

**1. The archived File Manager endpoints are not going away.** Their words: the archived
methods are not actively maintained, but they keep them supported and have no plans to
retire them; if that ever changes they will give advance notice and a working
replacement.

This closes open question 4, and with it the `CLAUDE.md` item "the archived-endpoint
question decides the architecture". The workspace walk stays the default as a supported
choice rather than a bet. **No code changes.** The re-fetched documentation confirms the
three archived endpoints still carry the same `Deprecated - please use Search all
workspaces items API instead` wording they carried before — no removal date, no sunset
notice, no change of status.

**2. They recommend the v4 search we measured as broken, and did not answer why it
fails.** They point at `GET /api/v4/filemanager/workspaces/<workspace_uri>/search` and
say it returns the whole workspace, private and public together, each item flagged with
`public`, `is_owner`, `in_trash` and a last-modified timestamp. Those four flags do exist
on the documented `File manager protocol item`, so the description is consistent with the
docs — but it is open question 3 restated, not answered. Our transcript stands: every
form we can construct returns `{"status_code":3,"invalid params"}`, and a deliberately
invalid folder guid returns the identical error, so the request fails before the path is
validated. The docs still contradict themselves — the example uses `-X PUT`, the request
line says `GET` — and we measured 400 for one and a 404 HTML page for the other.

Their "one call, no recursion" is loose: the endpoint is paginated and returns a
`pagination` object. It removes the folder recursion, not the paging. If it does work it
is a real improvement on one point — completeness could be checked once against a
workspace-level `total_results` instead of per folder.

**3. Rejected — keying the lock on `(version_class, version_id)`.** Their advice presumes
upstream's notion of a version, which is the dependency Hermetica exists to avoid. An
upstream identifier moves when upstream decides something is a new version; a content
hash moves when the content moves. Content addressing is strictly stronger, and adopting
their pair would make a lock reproduce only as well as upstream's bookkeeping does.
`version_code` stays dropped. `version_class` stays hashed content that gates no
decision.

**4. Rejected — a last-modified timestamp as a change trigger.** They suggest treating it
as a signal to re-pull. **This was already tried and removed, on measured grounds: most
of our protocols have no version carrying the tag, so the timestamp is absent for most of
them and cannot gate anything.** That is why `last_modified_on` is dropped outright from
the contract. It is not a question of it being a weaker signal than the hash — it is not
present often enough to be a signal at all.

Recorded because the suggestion is reasonable-sounding and will be offered again: it was
raised in this session as a way to skip the by-id fetch for unchanged protocols, and the
coder rejected it for the reason above. A second objection stands independently even
where the tag does exist — narrowing enumeration by modification time makes "unchanged
and still present" indistinguishable from "gone", which would break deprecate-on-absence.

**5. The 22,288 they could not reproduce is `filter=public`.** It is
`GET /v3/protocols?filter=public` returning `pagination.total_results: 22288` — the whole
public protocols.io corpus, not a count of our workspace. It appeared in open question 1
as evidence the filters are user-scoped rather than workspace-scoped. To be cleared up in
the reply.

Their point about the workspace Trash inflating our counts is already handled: selection
is `discovered − trash − not-a-protocol`, and the nine trashed protocols are excluded.

**Open — not answered by the reply:**

- **Open question 6, how to obtain the `workspace_uri` for a private workspace.** Their
  whole recommendation needs one. Every documented route returns public workspaces only:
  `GET /v3/workspaces` ("public workspaces"), `GET /v3/researchers/<username>/workspaces`
  ("researcher public workspaces"), and `GET /v3/workspaces/[uri]` needs the uri you do
  not have. The uri is visible in the browser address bar, so it can be read off once by
  hand and put in `env/.env` — config read at the edge, which is the pattern already in
  use. The pull still cannot discover its own workspace.
- **Open question 3** stays open until they supply a known-good request.

**Next, by the coder:** test the v4 workspace search by hand using the browser-read
`workspace_uri`. If it works, reply raising the remaining points without pressing them. A
reply to support goes out Monday either way.

**Documentation drift, re-measured against the 2026-08-12 re-check.** The mirror body is
still the 2026-08-05 conversion and was **not** re-run; the raw HTML is now kept at
`docs/protocols_io_api_raw_2026-08-22.html` so the next comparison can be a plain diff,
which §9 of findings asked for after last time.

**Exactly one upstream change since 2026-08-12:** a new `Experiment records Discussions
API` section — five endpoints for comments and discussion threads on run records, none of
which touch the pull path. Headings went 462 to 491, and those 29 are precisely this
section. No endpoints removed; 52 of 57 paths unchanged.

**Two things this session first read as new upstream sub-tables are neither.** The inline
style vocabulary (`bold`, `italic`, `sup`, `sub`) and the workspace `stats.files`
breakdown are present upstream and were **lost by our own converter**, which flattened
nested `<childList>` blocks — already recorded in findings §9 on 2026-08-12. They are
converter bugs, not documentation drift. Likewise the `Get List` correction
(`shared_with_user` from "public" to "private" protocols, plus the note that
`user_public`, `user_private` and `shared_with_user` collapse a version family to one
item) was already caught on 2026-08-12; what is new today is only that it is still
unpatched in the mirror body.

The three recorded doc-versus-reality discrepancies — `page_id` indexing, `order_field`
uniqueness, the rate limit — are all still accurate as written, and the v4 search still
documents `GET` while its own example uses `-X PUT`.

---

## 2026-09-03 — 531d44c — an earlier rename never reached its callers

**Discovered:** the suite could not collect. Five test modules and `chronos.py` still
imported `initialize_db`, `format_entry`, `build_row` and `ProtocolRow`, which had been
renamed in the source to `initialize_protocol_db`, `format_db_entry`,
`build_protocol_entry` and `ProtocolEntry`. `chronos.py` also imported
`initialize_pipeline_db` from `compose.compose`, where it has never lived, and passed it a
`template=` argument it does not accept.

**Why it matters:** the tests were red before any of this session's work started, so
nothing could have told us whether a refactor broke behaviour. Repaired first, on its own
commit, so a bisect can separate a rename repair from a behaviour change.

**Decided:** flatten `chronos/utils/` into `chronos/`. It held two chronos-only modules,
so the directory bought nothing, and it would have collided in the reader's head with the
new top-level `utils/`.

**Cost:** none. No behaviour change; 509 tests pass either side of it.

---

## 2026-09-03 — 2717c60 — shared mechanics leave `seal` for `utils`

**Decided:** `hermetica/utils/` now holds canonical form and hashing (`hashing.py`), epoch
conversion (`dates.py`, moved out of `seal/`), the sqlite connection and schema helpers
(`store.py`), and the version-interval machinery (`intervals.py`). `seal` and `compose`
call into it, each passing its own table and id column.

**Why:** `protocol_blob` and `pipeline_blob` had identical bodies, `HASH_ALGORITHM` was
declared twice, and the whole open/close-interval machinery existed only in `seal`. The
next step — versioning pipelines — would have been a second copy of the part of the system
where a mistake is silent data loss.

**Rejected — a descriptor object or a factory returning bound callables.** Both were
considered and both shorten the call sites. Plain arguments won because a reader learns
every input from the signature; a bag of table config puts a hidden input back with extra
steps (Principle 3, "a function is a capsule").

**Rejected — a shared base class for `hashable`/`metadata`/`to_dict`.** Those are three
one-liners visible on each dataclass. Inheriting them would move their meaning to another
file to save nine lines, which is the locality cost the same principle warns about. They
stay duplicated on purpose. Only the *hashing of* their output is shared, as
`hash_of(payload)`.

**Accepted contradiction:** `AGENT.md` said "a rule about identity or time belongs in
`seal`". `dates.py` moved anyway. The distinction now written into the module table is
**mechanics versus rules**: `to_epoch` converts, but *what `valid_from` means* is still
seal's; `hash_bytes` digests, but *which fields get hashed* is still `HASH_FIELDS`.
`utils` holds no rule, knows no platform, and owns no error vocabulary — `fetch_rows`
returns what it found and leaves naming an absence to the caller.

**Cost:** every `seal.dates` and `seal.contract` import in the repo moved. `FROZEN_HASH`
in `test_contract.py` is unchanged, which is the proof canonical form did not shift — no
stored hash is invalidated.

---

## 2026-09-03 — 2717c60 — `compose` gets a history table; `DAG_ids` dropped

**Decided:** `compose.db` gains `pipeline_history(pipeline_guid, hash, valid_from,
deprecated_at)` and a valid `pipeline_content`. Pipelines are now versioned by exactly the
interval rules protocols use, through the same `utils/intervals.py`: one active version
per guid, a new hash closes the old interval and opens a new one, absence deprecates.

**Discovered:** `compose/store.py` could not run at all. Its `CREATE TABLE` was missing
four commas, `created_on` was read before assignment, and the `DAG` column was being handed
the entire hashed blob instead of the DAG. `compose/templates.py` was six runtime errors
deep — `re.find` (not a function), `uuid.uuid4().hex()` (a property, called), a dict
iterated as pairs, a `tite=` typo, and YAML keys that did not match what the reader asked
for (`guid` vs `pipeline_guid`, `executor` vs the template's `executoror`).

**Decided:** drop `DAG_ids` from `compose.HASH_FIELDS`. It named a field that does not
exist on `ProtocolPipeline`, so `hashable()` raised `AttributeError` on every call — which
is how it survived unnoticed. The ids it named are already carried inside the `DAG`, so
hashing them separately would hash the same information twice. Rejected the alternative of
adding the field: inventing a hashed field decides what every future pipeline hash means,
and there is no DAG document shape settled yet to decide it from.

**Decided:** reading a template never mints. `pipelines_from_template` now raises
`UnmintedTemplateError` unless passed `mint=True`. The `pipeline_guid` is the identity that
survives every edit, so minting fixes it forever; a silent re-mint would orphan every
pipeline already stored under the old guid. The tell was our own new tests writing
`config/pg_core_templates_minted.yaml` into the working tree as a side effect of reading.

**Cost:** no pipeline hash had ever been written, so nothing re-hashes. `manifest_hash`
ships as a nullable column nothing fills — resolving a pipeline against a dated manifest
is still blocked on the Phase 2 day-to-manifest resolver. Graph validation against the
read-only version control, fork-on-edit and lineage remain unbuilt: this commit is the
storage, not the composition rules. `pyyaml` was imported but never declared, and is now a
dependency; `uv.lock` still needs a `uv lock` refresh, which could not run here.

---

## 2026-09-08 — 984a02b — identity becomes source-aware: `protocol_uid` and the scoped diff

**Decided:** identity is `protocol_uid` = `"<source>:<id>"`, and `source` joins
`PROTOCOL_HASH_FIELDS`. `protocol_history` keys on the uid and carries `source`;
`active_hashes` takes a `(column, value)` scope so absence is computed inside one
platform's partition. This closes the DANGER that had stood since `ee551d3` — a second
source can now be added to `chronos.db`.

**Why:** the two failure modes were separate and both had to be closed. The uid fixes
*collision*: two platforms can each number a protocol `88578`, and with a bare id they are
one row identity, so a content change on one closes the other's interval. The scoped diff
fixes *absence*: `absent = active − incoming` read every live row in the table, so
`protocols_io` and `zenodo` would have retired each other's whole catalogue on alternating
nightly runs, permanently, in an append-only table. **The uid alone would not have fixed
absence** — `active_hashes` still returns `zenodo:456` during a protocols.io pull. That is
why the earlier note said `protocol_uid` *and* the per-source diff.

`source` is hashed rather than kept as unhashed provenance, so one protocol mirrored on
two platforms is two records that drift independently — one may change while the other
does not. The alternative, source as a history-only column, was rejected: `protocol_content`
is keyed on hash and carries the id as a column, so unhashed source makes two uids collide
on one content row, a larger schema change than the re-hash.

**Rejected:** naming the column `provenance`. `snapshots.provenance` is free-form TEXT that
nothing reads, describing how a *lock* came to be; this is constrained, level-1, and
load-bearing in the diff. The same word for both would make them look interchangeable.

**Also decided:** the duplicate-identity check runs once, at lock generation, not again at
write time. Both write-path tests asserting it were removed. A partial unique index
(`ON protocol_history(protocol_uid) WHERE deprecated_at IS NULL`) makes "one active version"
a database rule instead, costing no Python and unable to be forgotten.

**Cost:** every stored protocol re-hashes — accepted as free before the first issued lock,
per `ee551d3`. Lock `entries` and `protocols` are now keyed on the uid, so **every existing
`.lock` file is stale** and must be regenerated; `db/chronos.db` must be rebuilt, as the
schema change is not a migration. `scope_of` derives the source from the rows rather than
taking it on trust, so an empty pull — the one case with no rows to read, and the one where
scoping matters most — raises unless told its source. Two adapter-level renames rode along:
`build_source` now threads one name into both the artefacts and `ProtocolSource.name`, which
is what that docstring had always claimed, and `MissingHash` moved to `utils/store.py` where
it is raised, retiring `utils/error_handling.py`. Still open from the `ee551d3` list: `id`
widened to `str`, hashed fields made nullable, and `reserved_doi` collapsed into a nullable
`doi`. 570 tests pass; `test_workspace.py` does not collect, against unbuilt discover work.

---

## 2026-09-08 — a0c718e — the executor moves from the pipeline to the protocol

**Discovered:** `executor` was a hashed field on `PipelineArtefact`, which asserts that one
pipeline runs on one thing. It does not. A real pipeline hands a plate from a human to a
Biomek and back, so the field could only ever be right for the degenerate single-executor
case — and it was `null` in all seven shipped templates, so nothing had noticed.

**Decided:** `executor` leaves `compose` entirely and joins `ProtocolArtefact` as a hashed
field, defaulting to `""`. The same steps run by a human and by a robot are two protocols,
not one protocol with an attribute: different calibration, different failure modes,
different results. Hashing it is what makes those two things separately pinnable and
separately versioned. `""` means nobody declared one, and is **not** a synonym for `human`.

**Why hashed rather than metadata:** the alternative was carrying it beside `keywords` as
retained-but-unhashed provenance. Rejected — a lock that pins a protocol would then not
pin who runs it, and swapping the executor on a stored protocol would leave every existing
lock silently claiming something it no longer describes. That is exactly the drift a
content hash exists to prevent.

**Decided:** protocols.io declares it in `keywords`, as `executor:<name>`. That channel
already carries the lab's declarations (the `deprecated` tokens), needs no upstream schema
we do not control, and is editable by the people who actually know the answer. It reuses
`split_keywords`, so the value is casefolded — `Biomek` and `biomek` are one executor
rather than two hashes. **Two declarations raise** rather than picking one: the value is
hashed, so choosing on the lab's behalf would mint an identity nobody asked for.

This puts a **hashed field downstream of an unhashed one** for the first time. `keywords`
stays metadata; only the `executor:` token is lifted out of it into content. The rule in
`AGENT.md` — "`keywords` is never hashed, so flagging cannot mint a version" — now has one
deliberate exception, and declaring an executor *does* mint a version.

**Cost:** every stored protocol re-hashes — still free before the first issued lock, per
`ee551d3`. `protocol_content` gains an `executor` column (six hashed fields are now also
columns) and `pipeline_content` loses one, so **neither schema is a migration**: both
`chronos.db` and `compose.db` must be rebuilt, and every existing `.lock` file is stale.
The lock's `protocols` display block carries the executor; `pins.lock` does not — pins stay
guid and hash, and the executor is inside the hash they pin. `scribe` renders it as a fact
row, omitted when undeclared. `config/pg_core_templates.yaml` and the pipeline test fixture
drop their `executor:` keys. No protocol in the workspace declares an executor yet, so
every value on the next pull will be `""` until the lab tags them.

**Rode along:** `tests/dev_tests/test_lifecycle.py` still imported `seal.lifecycle`, which
had moved to `sources/protocols_io/`, so the whole suite failed to collect. Import fixed;
602 tests pass.

---

## 2026-09-08 — a0c718e — the outside-tool transport: a thin HTTP API, planned

**Decided:** outside tools reach Hermetica through a **thin HTTP/JSON API** over the two
ports `AGENT.md` already names — read-only `query` over `chronos.db`, read-write `compose`
over `compose.db`. Recorded as planned in the module table, same status as prose-from-lock:
designed, unbuilt. The API process is the only thing that opens either `.db` file; no
outside tool mounts the volume. No tracked file changed but this one and the `AGENT.md`
amendment beside it.

**Why:** the near consumer is a pipeline portal, deployed next to Hermetica under docker
compose, where people build pipelines from currently-active protocols (titles only, read
only), start from the shipped templates, and **save the result back** to `compose.db`.
That is three things a shared mount handles badly: a writer in a separate container, a
consumer that is not Python and differs the most between deployments, and a schema that
would then be a published interface. HTTP + JSON + an OpenAPI description is the
interoperable boundary that survives all three — it is what "transport swappable" was
holding the place for.

Rejected:

- **Shared read-only mount + `import hermetica`** — the recommendation this discussion
  started from, and still right for a co-located Python reader that only needs the query
  port. It fails the portal on every one of the three points above. Kept as the fallback
  for that narrower case, not the default.
- **Lock files as the integration format.** A lock is an output and a reproducible
  receipt, not a live CRUD surface: the portal must read the active set and write new
  versions, and a lock does neither. Locks stay what they are — the artefact the larger
  system archives or hands on — and gain nothing here. This is where the "lock files feel
  iffy as the boundary" doubt raised in the discussion landed.
- **A fat service with its own model.** The store functions exist (`active_protocols`,
  `get_protocols`, `hydrate_pipeline`, `write_pipeline`); the layer is ~7 endpoints
  binding them to HTTP, stable ids on the wire (`protocol_uid`, `hash`, `pipeline_guid`,
  `manifest_hash`), no rowids, no raw SQL. FastAPI for the OpenAPI description at no cost.
- **More than one API replica.** `compose.db` is SQLite; multiple writing processes on one
  file is the corruption case, worse on a non-local volume. One instance, WAL,
  `busy_timeout`, serialised writes. Pipeline saves are human-paced — not a throughput
  problem.

**Cost:** nothing built, nothing re-hashed. It moves one existing item off "optional":
the **history-protection triggers must land before the portal writes**, because
`compose.db` stops having a single trusted writer. `compose.active_protocols` — still dead
and still wrong per the entry above — gets fixed when the picker endpoint becomes its
first real caller, not before. No compose entry point exists yet, so the API is also what
finally exercises `hydrate_pipeline`.


---

## 2026-09-08 — 709df6b — pipelines pin protocols: node ids, `node_hashes`, and a cycle check

**Decided:** a pipeline carries two graphs and a pin set, all three hashed. `DAG` is keyed
on **node ids that belong to the pipeline, not to protocols**; `nodes` maps each node id to
the protocol it runs, named by `protocol_uid`, `protocol_guid` or the bare `protocol_id`,
whichever the template author had to hand; `node_hashes` maps each node id to the protocol
hash that was active when `hydrate_pipeline` ran. `nodes` and `node_hashes` are `NOT NULL`
columns on `pipeline_content`.

**Why a node is not a protocol:** keyed on protocols directly, a protocol can appear at
exactly one point in a pipeline. `wash → digest → wash again` is then inexpressible, and the
attempt at it is **silently accepted as a cycle** — `{568614: 319531, 319531: 400843,
400843: 319531}` resolved without complaint into a graph that cannot run. With node ids a
repeat is ordinary: two ids, one protocol name, two entries sharing a hash. No pipeline
reuses a protocol today; the change was made now because it is nearly free before anything
is stored and expensive after.

**Rejected — `hash_dag`, the graph a second time in hash space.** Built first, replaced
before it shipped. It mirrored `DAG`'s structure with protocol hashes as keys, which is
what forced one-protocol-one-node: two steps running the same protocol are inevitably one
key. Branching is what exposed it. The topology itself was fine — the fixture's diamond
fanned out, joined and round-tripped correctly — so the limit only shows up on reuse, which
no current template needed. Once nodes have ids, the pins collapse to a flat map and the
graph is stored **once**, so the two copies cannot drift into disagreeing about the shape.
The replacement is smaller than the thing it replaces.

**Why the pins are hashed:** re-hydrating after one of a pipeline's protocols has been
superseded produces different `node_hashes`, therefore a different pipeline hash, therefore
a new version under the same `pipeline_guid`. A pipeline whose protocols moved *is* a
different pipeline. Left unhashed, that change would be invisible and every lock pinning
the pipeline would quietly describe a graph that no longer exists.

**Decided:** `hydrate_pipeline(pipeline, db)` resolves against the **active** slot only —
`protocol_history` where `deprecated_at IS NULL`, joined to `protocol_content` for the
three names a node may use. A retired protocol therefore has no hash to give and raises
`UnresolvedProtocolError`, which carries `.unresolved` so a caller can read the ids rather
than parse a message. Rejected: falling back to the most recent closed interval. That would
let a template silently pin a protocol the lab has declared out of use, which is the exact
thing lifecycle exists to prevent.

**Decided:** a bare `protocol_id` that answers for two active protocols raises
`AmbiguousProtocolError`, naming the candidates. Not asked for, and added anyway: `984a02b`
introduced `protocol_uid` precisely because two platforms can each number a protocol
`88578`, and a resolver that picked one of them would reintroduce that bug one layer up,
where it would be much harder to see. The uid still resolves cleanly when the bare id does
not, so the escape hatch is to name it properly.

**Decided:** `validate_dag` refuses two things before anything is stored. The node set of
`DAG` must equal the keys of `nodes` (`NodeMismatchError`, reporting both directions — a
successor that runs no protocol, and a protocol on no node). And the graph must be acyclic
(`PipelineCycleError`, carrying the cycle). Acyclicity is `graphlib.TopologicalSorter`
rather than a hand-written walk; it is fed successors where it expects predecessors, which
reverses the order it would produce and leaves cycle detection exactly correct, since a
cycle is direction-agnostic. It runs at template read *and* at hydration, so a hand-built
artefact cannot slip past.

**Decided:** `normalize_dag` sorts successors and lifts a lone successor written as a bare
string (`"319531": "400843"`) into a one-item list. Both shapes are equivalent to a reader
and not to a serializer, and forks here are parallel and conditional, so neither branch
order nor bare-versus-list is information — yet both reached the hash. The same fork written
`["319531","201297"]` and `["201297","319531"]` produced two pipeline hashes (`de17dca…`
and `cbb1829…`) for one graph.

**It is a plain function called by `pipelines_from_template`, not `__post_init__`.** The
dataclass hook would have made every artefact canonical however it was constructed, which
is stronger; it was rejected because normalization would then happen invisibly inside
construction, and a frozen dataclass rewriting its own fields via `object.__setattr__` is
the kind of thing you have to already know about to reason about a hash. An artefact built
by hand carries whatever order it was given, and the caller normalizes.

**Decided:** the template key `protocol_dag` is renamed to `dag`, and `dag`/`nodes` are
**required rather than defaulted**. The old key keyed protocols and the new one keys nodes,
so a template left on the old name would have parsed cleanly and pinned a graph that means
something else. Renamed, it fails at read with the pipeline named.

**Cost:** every stored pipeline re-hashes and `compose.db` must be rebuilt. Free, since
nothing has been written, and this is genuinely the last free moment. `compose` now reads
`chronos.db`; the two files stay separate and only this direction crosses, passed in as an
argument rather than resolved from config. Both templates are rewritten:
`tests/fixtures/pipeline_template.yaml` gets real node names (`lyse`, `digest_human`,
`digest_biomek`, `elute`) over the by-ID fixture's protocols, and also loses a `"D"` it had
been carrying from the config placeholders, which named no protocol at all.
`config/pg_core_templates.yaml` keeps placeholder graphs and **still does not hydrate** by
design, which is now asserted rather than assumed. 629 tests pass.

**Rode along:** the re-hydration test had been writing a single edited protocol as a whole
pull, which deprecates every other protocol in that source by absence — it passed only
because its pipeline had one node, and failed the moment the pipeline had two. It now
writes the full set with one record edited, which is what a real pull looks like.

**Open:** nothing calls `hydrate_pipeline` yet — there is no compose entry point, which is
what the entry above expects the HTTP API to supply. And `compose.active_protocols` is
still dead and still wrong: it reads `pipeline_history` for hashes and then looks them up
in `protocol_content`, which cannot match. Left alone deliberately, flagged here rather
than fixed inside an unrelated change.

---

## 2026-09-08 — 69f8e0e — a measured status page instead of a written one

**Decided:** repo status is computed, not asserted. `716e6af` added the script and the
target, `69f8e0e` made the check bite. `scripts/audit.py` counts, per
top-level module, files, physical lines, module-level public functions, test functions in
files importing that module, and covered/total statements from one `pytest --cov` run over
`tests/dev_tests`; `make audit` writes that table into `docs/status.md` and nothing in that
file is written by hand. `make audit-check` regenerates and then `git diff --exit-code`s
the result, so a committed status page that no longer matches the repo fails CI, and the
step runs in the `test` job where the venv already exists (`RUN="uv run"` overrides the
Nix dev shell used locally).

**Why:** completion percentages and module summaries written as prose have been wrong here
before and there was no way to catch it — a number in a doc is only as good as the last
person who edited it. Nothing goes into `status.md` that the script did not measure, so the
"ESTIMATE" case has no place to live: if a claim matters, it becomes a column. First
measurement: 6 modules, 30 files, 3419 lines, 126 public functions, 91.4% statement
coverage, 629 tests passing.

**Cost:** `.gitignore` had `docs/*` with a single exception for `audit_log.md`, so a
staleness check on an untracked file would have silently passed forever; `!docs/status.md`
is now a second exception. The `tests` column double-counts — a test file importing two
modules is counted against both, which is why the column sums to 1176 against a 629-test
suite — so it reads as attention, not as a test census. `make audit` runs the full suite
with coverage, which makes it a slow target and adds a second pytest run to CI.

---

## 2026-09-09 — 7ffe7e8 — `compose.active_protocols` deleted rather than fixed

**Decided:** `active_protocols` is removed, not repaired. It read hashes from
`pipeline_history` scoped by `pipeline_guid` and looked them up in `protocol_content`,
which cannot match; nothing called it and no test touched it. `709df6b` flagged it as
"still dead and still wrong", and `d539f32` deferred the fix to the picker endpoint that
would become its first real caller.

**Why:** that caller does not exist, so there is nothing to keep the function honest — a
broken function waiting for its first user is a trap for whoever writes that user, who
will reasonably assume it works. `active_protocol_aliases` already does the real
resolution, against the correct tables. When the compose port needs hash → title for a
picker it is a three-line query written against a live requirement, which is cheaper than
auditing a function nobody has ever run.

**Cost:** 34 lines and four imports gone. `compose` coverage 93.1 → 98.1 — the deleted
lines were the uncovered ones. 629 tests pass unchanged.

---

## 2026-09-09 — 7ffe7e8 — an exception carries its data, not just a sentence

**Decided:** every error class in `hermetica/` now takes the *data* of the failure as
constructor arguments, stores it as attributes and formats the message in `__init__`.
`MissingHash(table, missing)`, `DuplicatedIdError(kind, identifier)`,
`MalformedLockError(path, missing)`, `IncompleteDiscoveryError(read, reported, refusing)`,
`OrderError(repeated, missing, unknown)`, `UnrenderableProtocolError(needs, identifiers)`,
`MailNotSentError(error, draft)`, `UnreadableProtocolError(source, protocol_id)`,
`LockDriftError(path, drift)`, `ManifestMismatchError(path, rebuilt, recorded)`.
`compose`'s four already worked this way; this makes the repo consistent with them rather
than with the bare-docstring form.

**Why:** the raise sites already held the structured data — `store.py` computed a sorted
`missing` list and dropped it into a string — so the only way for a caller to learn *which*
hash was absent was to parse the message. The planned HTTP compose port has to turn these
into JSON responses, and prose is the wrong wire format. Formatting in `__init__` also
gives one wording per error instead of one per raise site: `DuplicatedIdError` spelled its
sentence out twice.

**Why this is not the "no prose in a data structure" rule:** that rule bans *commentary* —
a field explaining why the code did something, which is a comment that learned to travel.
An error message is the failure's only output, read by a human at the worst moment, and it
should be explicit. `AGENT.md` now says which is which, under *Indirection must earn its
link*.

**Decided — `LockDriftError` splits into two classes.** Its two raise sites are two
failures: the file does not verify against its own bytes (`LockDriftError`, carrying
`.drift`), versus the file verifies but the store now holds a different identity for those
hashes (`ManifestMismatchError`, carrying `.rebuilt`/`.recorded`). Widening one signature
to cover both would have been the config bag the capsule rule bans. Rejected — making the
second a subclass of the first: it inherits a docstring that is not true of it, since
nothing about that lock is corrupt, and it only existed to spare one test an import.

**Rode along:** `resolve_order` reported the first fault it found and stopped. It now
computes `repeated`, `missing` and `unknown` together and raises once with all three — the
same reasoning as `verify_lock` returning its drift rather than raising, since a caller
fixing an order wants the whole picture. `Counter` replaces the `set`-length comparison,
which had no way to name the repeats it detected.

**Cost:** no message loses information; one test changed — the store-disagrees case
asserted `LockDriftError` with `match="does not match"` and now names
`ManifestMismatchError`, which is what it was always testing for. 629 pass. Nine classes
gained an `__init__` and one was added, net +42 lines over seven files in five modules.
Total coverage 91.4 → 92.1, but **`chronos` slips 69.8 → 69.7**: `MailNotSentError.__init__`
sits on the SMTP-failure path, which had no test before this change either. Any caller
constructing one of these with a bare string now breaks loudly at the call — the intended
failure mode, and with nothing deployed it costs nothing.

---

## 2026-09-09 — 658c4f8 — history protection moves from Python into the schema

**Decided:** both history tables refuse `DELETE` outright and accept exactly one `UPDATE` —
closing an open interval, `deprecated_at` NULL → a timestamp with every other column frozen.
Both content tables refuse `UPDATE` and `DELETE` outright. Enforced by sqlite triggers built
in `utils.store` (`append_only_triggers`, `immutable_triggers`) and carried in each store's
`SCHEMA`, so `initialize_db` installs them on every run with no migration step.

**Why a trigger and not the FK.** `connect` sets `PRAGMA foreign_keys = ON`, which is what
had been stopping a referenced `protocol_content` row being deleted. A PRAGMA is *per
connection* and defaults to off, so that protection was never there for anyone opening the
file any other way. Verified rather than assumed: against a database opened with the bare
`sqlite3` CLI, `PRAGMA foreign_keys` reads `0` and all four illegal statements are still
refused, while `UPDATE protocol_history SET deprecated_at = 99` succeeds and leaves the hash
untouched. That is the whole point — the rule now holds for whoever opens the file, which is
what `compose.db` needs before the portal becomes its second writer.

**Why the update rule is a `WHEN` clause and not a ban:** `close_intervals`
(`utils/intervals.py:99`) is a legitimate `UPDATE`, and deprecate-on-change and
deprecate-on-absence both route through it. The guard names the three illegal shapes —
`OLD.deprecated_at IS NOT NULL` (re-stamping a closed interval), `NEW.deprecated_at IS NULL`
(reopening one), and `NEW.<col> IS NOT OLD.<col>` over the immutable columns (repointing a
row at another hash). `IS NOT` is null-safe, so a NULL column cannot slip through.

**Decided — `pipeline_history` gains the partial unique index it was missing.**
`protocol_history` has enforced one active version per protocol in the database since
`984a02b`; the pipeline side had the same invariant in Python only. A second writer is
precisely the thing Python-only invariants do not survive.

**Decided — the `hydrate_pins` manifest cross-check is deleted**, with
`ManifestMismatchError` and its test. `protocol_uid` is `f"{source}:{id}"` (`seal/store.py`)
and `source`, `id` and `guid` are all in `PROTOCOL_HASH_FIELDS`, so every field in `entries`
is a pure function of the hash: an honest store cannot rebuild a different `manifest_hash`.
Its only reachable failure mode was hand-edited content, which is exactly what its test did
to reach it — and what the content triggers now refuse. The check was structurally
unreachable, not merely redundant. `verify_lock` and `LockDriftError` stay; those check the
*file*, which nothing in the database can vouch for.

**Rode along — the two `verify_blobs` tamper tests are rewritten to build the bad row by
`INSERT`** rather than by editing a good one. `verify_blobs` is still worth having: it
catches corruption that never came through SQL at all — bit-rot, a partial write, a restore
from a bad backup — and the rewritten tests no longer depend on rows being updatable, which
makes them better tests than the ones they replace.

**Cost:** 639 tests pass (629 + 11 new − 1 deleted). One assumption was checked before
anything was written: `conn.execute` does accept a `CREATE TRIGGER` despite the semicolons
inside `BEGIN … END`, so `initialize_db` needed no change. The frozen-column test reads
`PRAGMA table_info` and closes the interval in the same statement, so it fails if a column is
added to a history table without being added to the trigger's immutable tuple — the one real
weakness of naming those columns twice. Coverage unchanged at 92.1 total.

**Open:** the committed `db/chronos.db` still predates the `protocol_uid` rename and cannot
take this schema — it could not take the current one either, since
`CREATE UNIQUE INDEX … (protocol_uid)` already fails against it. Delete-and-rebuild, per
`984a02b`; not done here. WAL and `busy_timeout` remain deploy-time configuration for the API
layer, not schema.

---

## 2026-09-16 — a341fbe — the v3 folder walk is gone, and the open item it left behind closes

**Discovered:** three rules in `AGENT.md`'s Acquisition section were not stale but *false*.
It described the default pull as a walk over `/v3/folders/<guid>/ids`, claimed "the walk
publishes no global total — the only completeness check is per folder", and named
`IncompleteWalkError`. The code has used the v4 workspace search for some time:
`search_workspace_items` sweeps the workspace flat, opens no folder, reads a global
`pagination.total_results`, and raises `IncompleteDiscoveryError` on a short read.
`IncompleteWalkError` does not exist. Discovery and fetch are both v4; the only `[Archived]`
endpoint still called is `/v3/protocols`, and only by the degraded `filter` fallback.

The supersession itself was never given an entry — the walk was replaced and the file was
not brought with it. That is the failure mode the "every line must be true today" rule
exists to prevent, and it cost a session: the open item below was re-derived from a file
that no longer described the code.

**Closed — `d4f4ff1`'s "a protocol filed in no folder is invisible to the walk".** It was
true of the folder walk and is unreachable now: there is no traversal, so folder membership
cannot hide anything, and the global `total_results` check is strictly stronger than the
per-folder count it replaced. `AGENT.md` gains an explicit bullet saying a protocol filed
nowhere is still found, so the item cannot be re-derived a third time.

**Decided — six constants deleted** from `sources/protocols_io/config.py`:
`FOLDER_CONTENT_TYPE`, `FIRST_FOLDER_PAGE`, `FOLDER_PAGE_SIZE`, `ITEM_BATCH`,
`TRASH_DEFAULT_ID`, `TRASH_FOLDER_TITLE`. Nothing in `hermetica/` or `tests/` referenced any
of them. Their comment blocks went with them — including the note on how the Trash folder
identifies itself by `default_id`, which only mattered to code that opened folders. One
comment was kept in edited form: the `/v3/protocols` 0-indexing note lived inside the
deleted folder-pager block but still applies to `FIRST_PAGE`, so it now sits on `FIRST_PAGE`
and names both conventions, the v4 search being 1-indexed.

**Also corrected — `PULL_STRATEGY=walk|filter` never existed.** `discover` raises on
anything but `workspace` or `filter`. A documented value the code rejects is worse than an
undocumented one.

**Cost:** 639 tests pass unchanged, ruff clean, `sources` drops 17 lines and stays at 100%.
No behaviour changed; every line removed was already unreachable.

**Open — the fixture keeps the walk's shape.** `tests/fixtures/workspace_search.json` still
carries `folder_pages` and `items` from the v3 walk, and four tests in `test_fixture.py`
assert their structure. Nothing in `hermetica/` reads either key, so those tests guard a
fixture nothing consumes. Recorded in `AGENT.md` rather than deleted, because the fixture
guards are allowlist-based and thinning one deserves its own pass.

---

## 2026-09-16 — a341fbe (no tracked file changed) — the rebuild recipe, and what a nix GC does to a git hook

**Decided — rebuilding a database is delete-and-run, and it is run as a module.**

```sh
rm db/chronos.db db/compose.db
nix develop --command uv run python -m chronos.chronos
```

`initialize_db` runs every schema statement as `IF NOT EXISTS`, so it can only build a fresh
file — it cannot upgrade a stale one. Deleting first is what makes a new schema apply, which
is how `658c4f8`'s triggers and unique index reach an existing checkout.

**`python -m`, never by file path.** `python hermetica/chronos/chronos.py` puts
`hermetica/chronos/` on `sys.path`, so `chronos` resolves to the *script* and the first
import fails with "'chronos' is not a package". `pyproject.toml` sets
`package-dir = {"" = "hermetica"}`, so the modules install as top-level packages and `-m`
finds them through the install. The rule was already in `AGENT.md` under Conventions; it was
still got wrong in practice, which is an argument for the Makefile target and not for more
prose.

**Run from the repo root.** `chronos.py` reads `Path.cwd() / "env" / ".env"` and `DB_OUT`
defaults to `db`, both relative to cwd. From anywhere else the pull silently gets no API key
and writes the database somewhere new.

**Corrected — `db/chronos.db` is not committed**, contrary to `658c4f8`'s closing note.
`db/` is in `.gitignore` and `git ls-files db/` is empty. It is a local file, so the rebuild
destroys nothing shared and needs no migration story.

**Discovered — a garbage-collected nix store path can break a git hook, and it does not look
like a nix problem.** `.git/hooks/pre-commit` failed with
`/nix/store/…-pre-commit-4.5.1/bin/pre-commit: No such file or directory`. The hook was a
generated script with that absolute store path baked in and `exec`'d unconditionally, having
been installed by a `pre-commit` that was itself running from the store. **Cause confirmed by
the coder:** a `nix-collect-garbage` run against a checkout whose `flake.lock` had not been
updated in a long time. The lock has since moved nixpkgs forward and the shell's
`pre-commit` is 4.6.2, a different hash; the hook kept pointing at the collected 4.5.1.

`.git/hooks/pre-push`, generated through the venv, resolves its interpreter at *run* time and
falls back to `command -v pre-commit`, so it survived the same collection. **Install hooks
through `uv run`, never from a bare `nix develop` shell** — `nix develop --command uv run
pre-commit install -t pre-push` produces the fallback form.

**Deleted rather than repaired:** `.pre-commit-config.yaml` has `default_stages: [pre-push]`,
so no hook wanted the commit stage at all. The file was a leftover from before that switch.
`.git/hooks/` now holds `pre-push` only.

---

## 2026-09-16 — a341fbe (no tracked file changed) — the project's record lives in the repo, nowhere else

**Decided:** nothing about Hermetica is written outside this repo. `AGENT.md` and this file
are the record; scratch goes to a temp directory. No agent transcripts, memory or state in
`~/.claude/projects/`.

**Why:** that directory had accumulated 21M of session transcripts, cached tool output,
subagent state and a memory store, none of it visible from inside the repo and none of it
reviewable the way a tracked file is. Hidden state that shapes an agent's behaviour is
indistinguishable from an undocumented rule: it cannot be read in a diff, cannot be
corrected, and outlives the reasoning that produced it. The rules that matter belong in
`AGENT.md`, where every line is checkable.

**Purged:** `memory/` (two behaviour rules), every `subagents/` and `workflows/` directory,
and the persisted `tool-results/` caches. The 22 `.jsonl` transcripts could not be deleted
from inside a session — the harness refuses it as transcript tampering, a guard against an
agent erasing its own record — so they were removed by the coder directly.

**Of the two purged memory rules, one was already covered** by the Nix line under
Conventions. The other was not, and is recorded here rather than lost: *Hermetica is
pre-stable, so decide the behaviour first and rewrite or delete tests that encode superseded
behaviour, instead of picking a weaker design to keep a test green. Report plainly which
tests were removed and why. Test preservation becomes meaningful once the package ships, not
now.*

**The rule is standing and applies to every project, not only Hermetica**, so enforcement
is in `~/.claude/settings.json` rather than here: **`autoMemoryEnabled: false`**, which stops
memory being read or written at all, and `cleanupPeriodDays: 1`, which expires transcripts
daily. Both apply to IDE-extension sessions, which is how this project is actually worked on.
Backup at `~/.claude/settings.json.bak`.

**A first attempt got the mechanism wrong** and is recorded because the reasoning recurs:
`--no-session-persistence` was put in a `claude` alias in `~/.zshrc`. A shell alias reaches
interactive terminal launches only, so it does nothing for a session started from the IDE —
the common case. It also addressed transcripts while leaving the memory store, which was the
actual complaint, untouched. The alias is kept (harmless, and it does close the terminal
path); `~/.zshrc.bak-before-claude-alias` is the backup. The lesson is the one this file
keeps relearning: check where a mechanism actually applies before claiming it enforces
anything.

**Purged elsewhere, same rule:** three empty `memory/` directories under other projects.
One project (`SciLifeLab-Vaults`) holds four real memories — three feedback rules and an
agreed spec for folder summaries — left in place rather than deleted, because they have no
other home yet. By this rule they belong in that project's own tracked files; until moved,
`autoMemoryEnabled: false` means nothing reads them.

**Cost:** `--resume` and `--continue` no longer carry this project's history; continuity
between sessions is whatever `AGENT.md` and this file say. That is the intended trade — it
is also why a stale line in either file is expensive here, and why the Acquisition section
being false for weeks cost a session earlier today.

---

## 2026-09-17 — 05f0276 — the v3 walk's fixture keys are gone; the Vaults memories are closed

**Closed — `a341fbe`'s "the fixture keeps the walk's shape".** `folder_pages` and `items` are
deleted from `tests/fixtures/workspace_search.json`. Nothing in `hermetica/` read them. The
file stays sorted and newline-terminated, and all its words still pass the lexicon check.

**Three shape tests moved onto the sweep instead of being deleted.** A trashed protocol, a
shared version family, and a Collection (`type_id` 3) are what the selection gate has to
survive, and `search_pages` already carries all three. The Trash-folder test only moved,
because it already read the sweep.

**Six tests deleted, because only folder membership gave them a meaning:** a folder spanning
two pages, per-folder counts matching `total_results`, an empty folder, a trashed folder
holding an unflagged protocol, one protocol filed in two folders, and the check that both
halves described the same folders. The sweep's own pagination and `total_results` tests
already cover the pager. The trashed-folder case can't be expressed any more: a protocol in
the sweep has no parent, and the gate reads only the protocol's own `in_trash`.

**Closed — `SciLifeLab-Vaults` memories**, from the 2026-09-16 entry. The coder marked it done.

**Cost:** 633 tests pass (639 − 6), ruff clean. No production code changed.

---

## 2026-09-17 — 05f0276 — `protocol_pins` dropped from the plan

**Decided:** the planned `protocol_pins` table won't be built. It was a policy list of
protocols to keep active when a pull couldn't see them, subtracted from `absent`
alongside `skipped`.

**Why:** the case it was designed for, protocols inserted by hand, stopped needing it when
`984a02b` made identity source-aware. Absence is computed inside the pulled source's
partition, so a protocol under its own source is never marked absent. The other use,
keeping a protocol active after upstream dropped it, is the store deciding rather than
tracking. Reproduction doesn't need it either: locks and pipelines pin hashes, and blobs are
never deleted. The one visible effect is that `hydrate_pipeline` raises for a template naming
a protocol that has gone. That failure is loud, which is the outcome we want.

**Kept — `skipped`.** It is still planned: unreadable ids subtracted from `absent`, replacing
the `UnreadableProtocolError` stop in `chronos.py`, which currently blocks a whole source
over one protocol.

**Cost:** docs only. `AGENT.md` records that there is no keep-list and keeps `skipped` as
the open item.

---

## 2026-09-17 — 38fd5c2 — second stale sweep of `AGENT.md`

**Method:** every identifier, env var, file and count named in `AGENT.md` was checked
against `hermetica/`, `tests/`, `.gitignore`, `pyproject.toml` and the CI workflow.

**Corrected:**
- The Authentication section still described the retired folder walk as the default, and
  called the v4 workspace search an untested open thread that returns `invalid params`. The
  search is the default route. What stays true is kept: no API route returns a private
  `workspace_uri`, so it is read from the browser address bar into `WORKSPACE_ID`.
- `CLIENT_ACCESS_TOKEN` is not an env var anywhere. The token lives in `API_KEY`.
- "an unbounded walk" became "an unbounded sweep". "A per-folder count check" became "a global
  `total_results` check".
- The `compose` row said "no validation". `validate_dag` exists. The row now says nothing
  calls `hydrate_pipeline`.
- The pull writes to env `DB`, not `DB_OUT`. `DB_OUT` is only the Python name.
- `.gitignore` ignores `docs/*` except `audit_log.md` and `status.md`, so the
  `protocols_io_*` docs are local only. That is now stated. `docs/status.md` joins the file table.

**Found, not changed:**
- CI's `--cov=` list omits `utils`, which is exactly what the rule in Conventions warns about.
- `scribe/markdown.py` reads `VIEW_URL` with `os.getenv` at module level, which breaks the
  capsule rule.
- `PIPE_TEMPLATE` is read in `chronos.py` and used nowhere.
- `docs/protocols_io_findings.md` §4 still records the v4 search as returning 400 for
  everything. It is untracked, so it was left alone.

**Cost:** docs only.

**Closed, same session — the `shared_with_user` "returns 0" belief.** The coder reports
that the workspace search and `filter=shared_with_user` both work. The 0 came from `""`
versus `''` quoting in the probe, not from the token, authorship or permissions. Pruned from
`AGENT.md`: "`shared_with_user` returns 0 and cannot be trusted", "neither token is a see
everything key… a workspace-admin account returns 0 from every filter", and "the search
is not filter-scoped, which is why it is the default". Also pruned: the claim that any
non-uri value returns `invalid params`, because it came from the same probes. Kept: no API
route returns a private `workspace_uri`.

**Corrected, same session:** `key` is still required by `/v3/protocols`. Only the
`shared_with_user` "returns 0" belief was the quoting bug.

---

## 2026-09-17 — 38fd5c2 (the plan) — the `filter` route is to be deleted

**Decided:** discovery gets one route, the v4 workspace search. The `filter` strategy
(`/v3/protocols?filter=shared_with_user`) is deleted, not kept as a fallback.

**Why:** it no longer fits the build. It was kept as a degraded fallback for a search we
believed was broken. Both work now, and one route is simpler to get running. A platform
that needs a list-by-filter can add it as its own adapter's discovery. The measurements
that justified the `order_field`, `key` and 0-indexing rules stay in
`docs/protocols_io_findings.md` for whoever re-adds it.

**The plan, by file:**

*`sources/protocols_io/discover.py`*
- Delete `fetch_protocol_list`, `search_by_filter` and `discover`.
- `fetch_pages` loses `max_pull`, which only the filter used. It keeps the page-length
  fallback, because `test_an_endpoint_that_reports_no_total_is_not_second_guessed` depends on
  it. Without that fallback, a missing `pagination` would keep paging until an empty page.
- The retry-once on a count mismatch goes too. The workspace route already raises on the
  first short read.
- The section header "ROUTE ONE" and the comment "THE TWO DISCOVERY ROUTES" are renamed.

*`sources/protocols_io/__init__.py`*
- `build_source` loses `strategy`, `list_url`, `page_size` and `max_pull`. The
  `/v3/protocols` default goes with them.
- **An empty `workspace_id` raises in `build_source`**, not at discover time. The check
  moves from `discover` to construction, so a misconfigured source fails before any
  clock read. It matters because an empty pull deprecates a whole platform.
- `protocol_discover` calls `search_workspace(headers, workspace_url)` directly.

*`sources/protocols_io/config.py`*
- Delete `FIRST_PAGE` and its comment. The workspace search comment stays.

*`sources/contract.py`*
- `DiscoveredProtocols.strategy` either goes (every source has one discovery, so `source`
  already names the route) or stays as a free label. **Open, for the coder.**

*`chronos/chronos.py`*
- Delete `PROTOCOL_LIST_URL` and `PULL_STRATEGY`, and the "ignored by the filter one"
  comment.
- `build_sources` loses `strategy`, `list_url`, `page_size` and `max_pull`.
- The failure entry's `"strategy"`, and `strategy=` in the print and the log entry, follow
  the contract decision.

*`chronos/report.py`*
- Delete the `shared_with_user` discovery line and the `degraded` NOTE, which points at
  findings §4–5.
- The `strategy` header line follows the contract decision.

*Tests*
- `test_request.py`: delete `TestServerConnection` (6), `TestPagination` (4) and
  `TestEnvelopeDrivenPagination` (8). All 18 drive `fetch_protocol_list`. Keep
  `TestFetchProtocol` and `TestCallApi`, pointing them at a neutral URL rather than
  `/v3/protocols`. Rewrite the module docstring, which is about `order_field` and 0-indexing.
- **Port two pager guarantees** to `TestWorkspaceSweep` rather than lose them: an empty page
  stops the sweep, and a short page with a `next_page` keeps going.
- `conftest.py`: delete the `list_items` fixture.
- `test_sources.py`: `TestBuildSource.mount` serves a workspace-search envelope instead of a
  `/v3/protocols` list. `source()` passes `workspace_id` instead of `strategy="filter"`. Add
  a test that a missing `workspace_id` is refused at construction. `TestBuildSources` passes
  `workspace_id`. Delete `LIST_URL`.
- `test_workspace.py`: `TestDiscoverRouting` goes. The filter test and the
  unknown-strategy test are deleted. The two workspace tests become the `build_source` test
  above, plus the sweep tests already in section 3. Delete `LIST_URL`. Reword the module
  docstring so it no longer justifies itself against `/v3/protocols`.
- `test_report.py`: delete `test_the_degraded_fallback_is_called_out` and
  `test_a_workspace_pull_does_not_claim_to_be_degraded`.
- `test_pull_log.py` and `test_report.py` entries lose `"strategy"` if the field goes.

*Docs*
- `AGENT.md` Acquisition: delete the `/v3/protocols` `[Archived]` line, the four-ways-degraded
  line, `order_field`, `key`, and "The list response is thinner than by-ID". Also delete the
  Planned bullet itself, and `PULL_STRATEGY=workspace|filter` under Conventions. Keep the
  pagination, no-ceiling and rate-limit rules, which the sweep still needs.
- Regenerate `docs/status.md` with `make audit`. CI fails otherwise.
- The coder's local `env/.env` still sets `PROTOCOL_LIST_URL` and `PULL_STRATEGY`. Both
  become unread and should be removed by hand.

**Expected cost:** about 22 tests deleted and 3 added. No hash, schema or stored data
changes, because discovery yields ids only. Old `pull_log.jsonl` lines keep their
`strategy` key, which nothing reads back.

**Out of scope, noted:** `dry_run` in `report.py` is never set by anything in `hermetica/`.

---

## 2026-09-17 — 38fd5c2 — the `filter` route, `strategy` and `dry_run` are gone

**Done:** the plan above, with the one open item decided by the coder. **`strategy` is
removed**, not kept as a label, because it was dead weight in the contract, the log and the
report. **`dry_run` is removed too**: nothing ever set it.

**Deleted from code:**
- `discover.py`: `fetch_protocol_list`, `search_by_filter`, `discover`, the retry-once,
  and `max_pull`.
- `config.py`: `FIRST_PAGE`.
- `contract.py`: `DiscoveredProtocols.strategy`.
- `chronos.py`: `PROTOCOL_LIST_URL` and `PULL_STRATEGY`. `build_sources` also loses
  `strategy`, `list_url`, `page_size` and `max_pull`.
- `report.py`: the `strategy` header line, the `shared_with_user` line, the degraded NOTE
  and the `dry_run` branch.
- `client.py`: `read_payload`.

**Changed beyond the plan: `read_payload` is gone, not trimmed.** Coverage caught that its
top-level branch, the v3 envelope, was now unreachable. It had one caller. The pager now
reads `.json()["payload"]` directly, so a body without `payload` raises `KeyError`. Before,
it read as a page with no items, which on the first page means an empty sweep with no
total, and so a pull that deprecates the whole platform. The fetch-by-id path still uses
`.get("payload", [])` and was not touched.

**`build_source` now raises on an empty `workspace_id`**, as planned. The raise happens in
`build_sources`, before `__main__`'s per-source `try`. A missing `WORKSPACE_ID` therefore
stops the run with a traceback, not a mailed failure report. That is a configuration
error, not a night that went badly, and nothing is written either way.

**Tests:** 633 → 610.
- **Deleted, 26:**
  - `test_request.py`: 18, every `fetch_protocol_list` test.
  - `test_workspace.py`: 5, the whole `TestDiscoverRouting` section plus
    `test_it_names_its_strategy`.
  - `test_report.py`: 3, the dry-run test and both degraded tests.
- **Added, 3:**
  - Two pager guarantees ported onto the sweep: an empty page ends it, and a short page
    with a `next_page` keeps going.
  - `test_no_workspace_id_is_refused_at_construction`.
- **Rewritten:** `TestBuildSource` mocks the workspace search instead of `/v3/protocols`.
  `test_warnings_reach_the_outcome_line` finds the outcome line by label, because a
  hard-coded line index broke when the header lost a line.

**Cost:** ruff clean. `make audit` regenerated `docs/status.md`: `sources` 754 → 654
lines at 100%, total 3467 → 3335 lines, coverage 92.0%. No hash, schema or stored data
changed.

**For the coder:** `env/.env` still sets `PROTOCOL_LIST_URL` and `PULL_STRATEGY`, and
nothing reads them now. The local `docs/protocols_io_findings.md` still holds the `/v3`
measurements, for whoever re-adds a list route.

---

## 2026-09-17 — 38fd5c2 — a misconfigured source is a failure report, not a crash

**Corrected by the coder:** the previous entry accepted that a missing `WORKSPACE_ID` would
stop the run with a traceback. That was wrong. `SOURCES` plus each source's settings tell
Hermetica where to look. A source whose settings are missing or wrong is that source's
failure, and it belongs in the report with the other sources still pulled. The fix for the
operator is the env file, and the report is where they will find that out.

**Changed:**
- `chronos.build_sources(names) -> list` is now `configure_source(name) -> ProtocolSource`.
- The per-source `try` in `__main__` moved into `run_source`, which configures and pulls
  inside one `try` and returns `(log entry, report text)`.
- An unknown name in `SOURCES` also becomes a failure report now, not a crash.
- `__main__` only loops, logs and mails. The failure path used to live there untested and
  is now covered.

**Tests:** 610 → 611. Deleted 4 `TestBuildSources` tests, since the order and empty-list
behaviour now live in the `__main__` loop. Added 2 `TestConfigureSource` tests and 3
`TestRunSource` tests: a missing workspace id is reported, an unknown source is reported,
and a configured source reports its pull. Ruff clean, `docs/status.md` regenerated.

---

## 2026-09-17 — f07ee56 (plan, one AGENT.md fix) — an API over both stores, and the pipeline-absence bug it exposed

**Corrected in `AGENT.md`:** the planned API was described as "the only process that opens
either `.db` file". That was never true, because `chronos` writes `chronos.db` directly
and is meant to keep doing so. The line now says `chronos` stays the one writer and opens
the file directly. The API opens `chronos.db` read-only and `compose.db` read-write, and
outside tools reach either file only through the API.

**Decided by the coder:** the seal side also goes behind the API, as a read-only query
port, not only the compose side. The reasons:
- A direct reader couples every outside tool to table and column names.
- SQLite on a network mount is unsafe, so remote tools need a server anyway.
- `hydrate_pipeline` needs seal's data, so both ports sharing one process costs nothing.

**Discovered — blocker:** `write_pipeline` calls `write_version_control` with no scope. The
diff is therefore taken against every active pipeline, and any pipeline missing from the
write lands in `absent` and gets its interval closed. The tests never write two pipelines
in separate calls, so nothing caught it. Nothing calls `write_pipeline` outside tests
today, so no data is affected. A compose port that saves one pipeline per request would
deprecate every other pipeline on each save. Recorded as Open under Pipelines in
`AGENT.md`.

**The plan.** It is ordered so that each phase is useful and testable before the next.

*Phase 0 — prerequisites, no HTTP*
1. **Pipeline writes never deprecate by absence.** A pipeline is edited one at a time, not
   pulled as a snapshot, so absence means nothing for it. Retiring a pipeline becomes an
   explicit `retire_pipeline(db, guid)` that closes its open interval. The test is two
   pipelines written in separate calls, both still active.
2. **WAL and `busy_timeout`.** `initialize_db` sets `PRAGMA journal_mode=WAL`, which is
   stored in the file, and `connect` sets `busy_timeout` on every connection. Without these,
   the nightly write and the API's reads lock each other out.
3. **CI coverage:** add `--cov=utils`, which is missing today, and the new package.

*Phase 1 — the ports as plain functions, new top-level package `api`*

Functions take database paths as arguments and return JSON-ready dicts keyed by stable ids.
Every read uses `connect(..., read_only=True)`, which already exists.

- `api/query.py`, against `chronos.db`, read-only:
  - `list_protocols(db)`: the active version of each protocol, without the body. It needs
    **new SQL**: history's live rows joined to content, the same join
    `active_protocol_aliases` uses.
  - `protocol_versions(db, protocol_uid)`: every interval for one protocol. **New SQL.**
  - `get_protocol(db, hash)`: one version with its body, via `get_protocols`.
  - `build_lock(db, hashes)`: `generate_protocol_lock`, returned as a document and not
    written to a file.
- `api/compose.py`, against `compose.db` read-write plus `chronos.db` read-only:
  - `list_pipelines`: new SQL, like `list_protocols`.
  - `get_pipeline(guid)`.
  - `save_pipeline(payload, guid=None)`:
    1. Normalize the DAG with `normalize_dag`.
    2. Validate it with `validate_dag`.
    3. Mint a guid only when none is given, since reading never mints.
    4. Resolve node hashes with `hydrate_pipeline`.
    5. Build the entry with `build_pipeline_entry`.
    6. Write it with `write_pipeline`.
    7. Return the guid, hash and new/changed/unchanged status.

    The `dag`/`nodes` key checks now live inside `pipelines_from_template`, which reads a
    file. They move into a function that builds one pipeline from a dict, shared by the
    template reader and this port, so the two cannot drift.
  - `retire_pipeline(guid)`.
  - `pipeline_lock(guid)`: `generate_pipeline_lock`, plus `generate_protocol_lock` over its
    `node_hashes`, merged by `generate_lock`.
- **The query port is checked by a test against a `chmod 0444` database file.** A write
  added to the query port then fails that test.

*Phase 2 — transport*
- A route table mapping method and path to a port function.
- Errors map by exception class, which works because exceptions already carry data:
  - `MissingHash` → 404.
  - `UnresolvedProtocolError`, `AmbiguousProtocolError`, `NodeMismatchError`,
    `PipelineCycleError`, and malformed input → 422, with the exception's attributes as
    the JSON body.
  - Anything else → 500, with no traceback in the response.
- Binds `127.0.0.1` by default. Request bodies have a size cap, since all input is
  untrusted. JSON only.
- Entry point `python -m api.server`. Paths and port come from `env/.env`, read once in
  `__main__`.

*Phase 3 — docs*
- Update the `AGENT.md` module table and Storage section.
- Add `api` to `known-first-party` in `pyproject.toml` and to `--cov` in CI.
- Run `make audit`.

**Deferred, on purpose:**
- Verifying an uploaded lock, since `verify_lock` takes a path today.
- Versions by date, parked on `feature/versions-by-date`.
- Markdown and PDF rendering.
- `snapshots`.
- Loading templates from `PIPE_TEMPLATE`.

**Open, for the coder:**
1. **Transport.** Standard-library `http.server` with no new dependency, or a framework
   such as Starlette or FastAPI that brings request validation and OpenAPI docs for the
   portal.
2. **Authentication.** None, bound to localhost for now, or a bearer token from the first
   release.
3. **Pipeline retirement.** Explicit only, as recommended, or something else.
4. **First-cut query scope.** Whether `protocol_versions` is in, or `list_protocols` and
   `get_protocol` are enough to start.

**Decided by the coder, same session:**
1. **Transport: standard-library `http.server`.** The first deployment runs Hermetica and
   the portal as containers in one Docker Compose stack on one server. Generated API docs
   are wanted, so others can see what the API offers. FastAPI was considered and set aside
   for now as a heavy dependency: it brings Starlette, Pydantic and a server such as
   Uvicorn. **Proposed instead:** a hand-written `api/openapi.json` served at
   `GET /openapi.json`, with a test that the route table and the document list exactly the
   same paths and methods, so the docs cannot drift from the code. Any OpenAPI viewer can
   render it. Revisit FastAPI when request validation becomes enough work to justify it.
   *Awaiting the coder's confirmation of this docs approach.*
2. **No authentication during development.** It is added as a feature later. Until then the
   only protection is the network. Inside Compose the server must listen on `0.0.0.0`,
   because `127.0.0.1` in a container is unreachable from the portal container. **The API
   port must not be published to the host.** Other containers on the Compose network reach
   it, and nothing outside the stack does. This belongs in the deployment docs, and a
   published port is the thing to check before any deploy.
3. **Explicit `retire_pipeline`**, exposed to the portal as a remove action.
4. **`protocol_versions` is in the first cut.**

**Deployment note for Phase 0.2:** WAL needs every process sharing a database file to
share memory on one host. Containers mounting the same Docker volume on one machine meet
that. A bind mount onto a network filesystem such as NFS or SMB does not.

---

## 2026-09-17 — f07ee56 — API Phase 0: pipelines stop deprecating by absence, WAL, CI coverage

**Confirmed by the coder:**
- The transport is `http.server`, with a hand-written `openapi.json` and a test that
  keeps it matched to the routes. FastAPI stays a later option.
- The unpublished-port rule is recorded in `AGENT.md` under Storage, marked ⚠ as a
  pre-deploy check.

**0.1 — pipeline absence.**
- `diff_entries`, `version_control_diff` and `write_version_control` take
  `absence: bool = True`. With `False`, `absent` is empty and so nothing is closed by it.
  The decision lives in one place, `diff_entries`, and the other two pass it through.
  Protocols keep the default.
- `write_pipeline` and `diff_pipelines` pass `absence=False`.
- New `retire_pipeline(db, guid, retired_at)` closes one interval and returns the retired
  hash. It raises `InactivePipelineError(guid)` if there is no open interval, which
  includes a second retire, so a portal button cannot report success on nothing.

**Corrected — last entry's claim that no test wrote two pipelines separately was wrong.**
`test_a_pipeline_missing_from_the_write_is_deprecated_by_absence` asserted the old
behaviour on purpose. That design is superseded, so under the pre-stable rule the test was
**replaced**, not kept: `test_a_pipeline_missing_from_the_write_stays_active`.

**0.2 — WAL.** `initialize_db` runs `PRAGMA journal_mode = WAL` before the schema. The
test commits a write while a read-only connection holds a read transaction. **It was
checked against the code with the PRAGMA removed, and failed with `database is locked`
after 5 s.** A first version of the test only checked that a reader is not blocked by an
uncommitted write. That also passes without WAL, so it proved nothing and was replaced
before commit. **`busy_timeout` was not added:** `sqlite3.connect` already waits 5 s by
default, which is the same mechanism. Revisit if real lock waits run longer.

**0.3 — CI.** Added `--cov=utils`, which the "a new package must join `--cov`" rule had
missed.

**Tests:** 611 → 620.
- 1 replaced.
- 1 added for `diff_pipelines` never reporting absence.
- 6 added for `retire_pipeline`: closes only that pipeline, returns the hash, content
  survives, unknown guid refused, second retire refused, a re-save opens a new interval.
- 2 added for WAL: the file is left in WAL mode, and a commit succeeds while a read is in
  progress.

Ruff clean. `make audit`: compose 98.3%, utils 98.9%, total 92.6%.

**Local databases:** `db/chronos.db` and `db/compose.db` switch to WAL the next time
`initialize_db` runs against them. The schema itself is unchanged.

**Next:** Phase 1, the ports as plain functions in `api/`.

---

## 2026-09-17 — 45a26b8, tests c7c6858 — API Phase 1: both ports as plain functions, and a creator that never read back

**Built — new top-level package `api`.** No HTTP yet. Functions take database paths and
return JSON-ready dicts with stable ids and ISO times.
- **`api/query.py`**, the query port against `chronos.db` read-only: `list_protocols`,
  `protocol_versions` (raises `UnknownProtocolError`), `get_protocol` (raises
  `MissingHash`), and `build_lock`, which returns a lock document and writes no file.
- **`api/pipelines.py`**, the compose port:
  - `list_pipelines`.
  - `get_pipeline` (raises `InactivePipelineError`).
  - `save_pipeline`: validates the body, mints a guid only when none is given, refuses a
    guid with no history (`UnknownPipelineError`), keeps the first version's
    `created_on`, re-hydrates, writes, and returns new/changed/unchanged.
  - `retire`.
  - `pipeline_lock`: the pipeline lock plus a protocol lock over its deduplicated
    `node_hashes`.
- **`api/contract.py`**: `InvalidRequestError(problems)`, which lists every problem at
  once.

**Supporting changes:**
- `utils.intervals`: `active_rows` and `intervals_of`, generic reads.
- `seal.store`: `active_protocols` and `protocol_intervals`.
- `compose.store`: `active_pipelines` and `pipeline_intervals`. `api` never names a table.
- `compose.templates.build_pipeline` is split out of `pipelines_from_template`, so
  templates and the API build a pipeline through one function.
- `InactivePipelineError`'s message is now generic ("has no active version"), because
  reading and locking raise it as well as retiring.
- `pyproject.toml` `known-first-party` and the CI `--cov` list now include `api`.

**Discovered and fixed — `encode_entry` and `decode_entry` did not round-trip a
string.** `encode_entry` stored a `str` raw, and `decode_entry` always runs `json.loads`.
A plain-string creator therefore failed to decode on read. That includes the template's
own `creator: "Homunculus Pat"`, so `generate_pipeline_lock` would have crashed on any
template pipeline. No test locked a template pipeline, which is why it went unnoticed. A
raw string like `"123"` would also have decoded as the wrong type. **Fixed at the pair,
not in the API:** `encode_entry` now JSON-encodes everything except numbers and `None`.
Only metadata columns are affected (`creator`, `authors`, `keywords`), none of them
hashed, so **no hash changed**. `test_natives_pass_through_untouched` asserted the old
behaviour for `"text"`, so it was **rewritten** as a round-trip test covering `"Ada"`,
`""`, `"123"`, `"null"`, a dict, a list and `True`. `test_bool_is_not_treated_as_an_int`
now expects `"true"`.

**Local stores:** rows written before this keep a raw-string `keywords`. Nothing reads that
column today. A `compose.db` holding a string creator will not decode. Delete and rebuild
both per the 2026-09-16 recipe.

**Checked, not assumed.** All 40 new tests passed on the first run, so three were checked
against broken code:
- The `chmod 0444` guard does refuse a write, with `attempt to write a readonly database`.
- `test_saving_one_leaves_the_others_active` fails once `absence=False` is removed from
  `write_pipeline`.
- A plain-string creator crashed on read, which is how the encoding bug was found.

**Not built (deferred):**
- `pipeline_versions`, which would mirror `protocol_versions`.
- A `manifest_hash` on save.
- Resolving uids to hashes for `build_lock`.

Add each when the portal needs it.

**Tests:** 620 → 669, 19 files.
- `test_api.py` adds 43 test items: 40 for the ports, plus 3 for the creator round-trip.
- `test_utils.py` gains 6 items net. The old pass-through test lost its `"text"` case, and
  the new round-trip test has 7 cases.

Ruff clean. `make audit`: `api` 100%, total 94.0%.

**Next:** Phase 2, the `http.server` transport, `openapi.json`, and the route-and-document
parity test.

---

## 2026-09-17 — 45a26b8, tests c7c6858 — "entries, not rows"; `pipeline_versions`; what a template is

**Rule from the coder: a stored thing is an entry, not a row.**
- **Renamed:** `active_rows` → `active_entries`, and `rows_for` → `entries_for` in
  `test_store.py`. Every `row`/`rows` variable naming an entry is now `entry`/`entries`,
  across `utils`, `seal`, `compose`, `scribe`, `api` and their tests.
- **Also changed:** docstrings, including the trigger comments, and four `AGENT.md` lines.
- **Left alone on purpose:**
  - `sqlite3.Row` and `row_factory`, which are SQLite's names.
  - The markdown fact table in `render_facts`, now `facts`.
  - Markdown table rows in `test_scribe_markdown.py`.
  - The module table in `scripts/audit.py`.
- **One collision found and fixed.** In `collect_display` the rename would have assigned
  `entries = get_protocols(db, [entries[pid]...])`, shadowing the lock's `entries`
  argument. It worked only because the right-hand side runs first, so the local is now
  `stored`.

**Built — `pipeline_versions(db, guid)`.** Every version a pipeline has held, retired
versions included. It raises `UnknownPipelineError` for a guid with no history. Version
formatting moved to `api.contract.describe_intervals`, shared with `protocol_versions`.
Adds 3 tests.

**Tests:** 669 → 672. Ruff clean.

**The design the coder stated when asked what a pipeline's `manifest_hash` should do.**
This changes Phase 1's compose port, so it is recorded here before anything is rebuilt:
- **By default the user sees active protocols and active pipelines.**
- **A pipeline template records the DAG and each node's `protocol_guid`, never a hash.**
  In the UI each node shows the protocol's currently active version. A node whose protocol
  is no longer active is still sent and still drawn in the DAG, marked *inactive*. Until
  a lock exists, the latest protocols are placeholders.
- **Hashes are added only when a pipeline is validated, to create a lock file.**
  `node_hashes` and a `manifest_hash` belong to that moment, not to saving a template.
- **A lock file the user brings in is displayed at its pinned versions**, not the active
  ones.

**Where Phase 1 disagrees with this:**
- `save_pipeline` hydrates on every save and stores `node_hashes`.
- It refuses a node whose protocol is not active, through `UnresolvedProtocolError`.
- `nodes` accepts a uid, guid or bare id, not a guid only.
- There is no way to read a lock the user brings.

**Awaiting the coder's answers before rebuilding.**

**Answered by the coder, same session. This supersedes Phase 1's compose design:**
- **A template is the shape of a pipeline: its DAG and the `protocol_guid` of each node,
  never a hash.** The UI flow:
  1. The user opens a template ("use this template").
  2. They may add or remove steps.
  3. They either **save template**, which records the DAG only, or **export lock**, which
     takes that DAG, pulls the active protocol hashes, hashes the pipeline, and writes a
     full lock.
- **A pipeline is its DAG.** A change to the shape or to the guids used is a different
  pipeline. Which protocol *version* runs is the protocol store's concern, pinned only in
  the lock. The pinned form (`node_hashes`, `manifest_hash`) lives in the lock, not in
  `compose.db`.
- **Export refuses a node whose protocol is inactive.** A lock is a guarantee. This only
  happens when a protocol was retired while a pipeline still uses it. The UI renders the
  node inactive, the user swaps in an active protocol, then validates and saves.
  **Retiring a template** removes it from the template list.
- **Saving a template refuses a guid matching no protocol in the store; an inactive
  protocol is allowed.** A guid can match nothing when it is mistyped, stale, or from
  another workspace, even though everything the pull wrote was hashed.
- **Backend transactions are guid-only. Titles and ids are display only.**
- **The config template is a separate path.** It is hand-written to bootstrap `compose.db`
  with pipelines the team built in the old tool, and it keeps its minting.
- **Reading a lock the user brings is accounted for, not built.** It is a separate
  concern, handled later.

**Decided by the coder — option A: export requires a saved template.** An unchanged
DAG exports under its template's guid. An edited, unsaved DAG is refused until it is
saved, so every lock points at a pipeline that exists in `compose.db` and both halves
stay version-controlled. The UI can offer save-then-export as one action. Rejected:
minting a lock-only guid at export, which would leave two identities for one DAG once it
was saved.

---

## 2026-09-17 — 45a26b8, tests c7c6858, baseline c37b883 — templates are DAGs of guids; pinning happens only at export

**Built, as decided above.** The compose side of Phase 1 is rebuilt to match.

- **A template stores its DAG and node guids, never hashes.**
  - `save_pipeline` no longer hydrates. It calls `check_nodes_sealed`, which refuses a guid
    the store never held with `UnsealedProtocolError`, by node, and lets an inactive
    protocol through.
  - The body's `nodes` must map to non-empty strings. A bare id is refused, because it is
    not a guid.
  - `build_pipeline` ignores any `manifest_hash`/`node_hashes` in the written form.
- **Reads return node state.** `list_pipelines` and `get_pipeline` now take
  `protocol_db`. Each node carries `protocol_guid`, `status` (active or inactive),
  `protocol_uid`, `title` and `hash`, from one lookup for all nodes. A template shows no
  `node_hashes` or `manifest_hash`.
- **`export_lock(db, protocol_db, guid)` replaces `pipeline_lock`.**
  1. It takes a saved template's guid, so an edited template must be saved first
     (option A).
  2. It pins through `hydrate_pipeline`.
  3. It builds the protocol lock.
  4. It sets the pinned copy's `manifest_hash` to that lock's.
  5. It builds the pipeline lock from the pinned **artefact**, with
     `provenance.template_hash`.

  Nothing pinned is written to `compose.db`.
- **`hydrate_pipeline` is guid-only.** It reads `seal.store.latest_protocols` and raises
  `UnresolvedProtocolError(nodes)`, keyed by node, for any guid that is inactive or
  unknown. **Deleted:** `active_protocol_aliases` and `AmbiguousProtocolError`. Guid-only
  lookup removes the bare-id collision they existed for.
- **`generate_pipeline_lock` takes artefacts, not stored hashes**, because a pinned
  pipeline exists only in the lock. Its display now carries `root`, `nodes`,
  `node_hashes` and `manifest_hash`, which it lacked before, so a lock could not have
  reproduced a pipeline from its own contents. **`seal` no longer imports from
  `compose`.**
- **New reads:**
  - `utils.intervals.latest_entries`: each key's latest version, active or not.
  - `seal.store.latest_protocols(db, keys, by=guid|uid)`.
  - `compose.store.pipeline_from_entry`.
  - The constant `PROTOCOL_GUID`.
- **Bootstrap:** `compose.templates.load_template(path, db, protocol_db)`.
  - Names may be a `protocol_uid` or a guid. `guids_for_nodes` converts uids to guids and
    refuses a bare id or an unknown name.
  - Every pipeline is resolved before anything is written, so one bad name loads
    nothing.
  - Nothing calls it yet, and `PIPE_TEMPLATE` is still unread.
- **Config:** `config/pg_core_templates.yaml` loses its 14 `manifest_hash: null` and
  `node_hashes: null` lines, now ignored, and "will not hydrate" becomes "will not load".
  The coder's own header lines are untouched. `tests/fixtures/pipeline_template.yaml`
  names protocols by `protocol_uid`.

**Tests superseded and rewritten, per the pre-stable rule:**
- **Deleted:**
  - `test_an_id_live_on_two_sources_raises` and
    `test_the_uid_still_resolves_when_the_bare_id_is_ambiguous`, since aliases are gone.
  - `test_a_uid_resolves` and `test_a_guid_resolves`, since only guids resolve now.
  - `test_a_branch_survives_hydration`, covered by the one-protocol-at-two-nodes test and
    `TestGraphShape`.
  - `test_a_hydrated_template_writes_and_versions`, since templates no longer hydrate.
  - `test_an_unhydrated_template_carries_no_node_hashes`, superseded by
    `test_a_loaded_template_carries_no_hashes`.
  - `test_the_shipped_config_template_is_still_unhydratable`, rewritten as
    `test_the_shipped_config_template_does_not_load_yet`.
- **Rewritten:**
  - `test_a_new_protocol_version_rehydrates_to_a_new_pipeline_hash` became
    `test_a_new_protocol_version_pins_the_new_hash`, which now also asserts the template
    hash does **not** change.
  - The unknown-protocol and deprecated-protocol tests became by-node guid tests.
- **Added:**
  - `TestSealedCheck` (4).
  - `TestLoadTemplate` (7).
  - In `test_api.py`: template stored without hashes; a protocol moving on does not
    version the template; a swapped guid is a new version; inactive protocols may be
    saved; bare ids refused; node status on read, including inactive; `TestExportLock`
    (8).

Counts: `test_api.py` 46 → 59, and `test_compose.py` gains 3 net. **Suite: 672 → 688.**

**Checked against broken code:**
- With the `deprecated_at` check removed from `hydrate_pipeline`, both
  inactive-refused-by-node tests failed.
- With `check_nodes_sealed` removed from `save_pipeline`, both unsealed-guid tests
  failed.

The code was restored, and 688 pass.

**Process slip, recorded:**
- While counting tests, a `git stash && git stash apply` round trip was run with no need
  for it. The working tree came back identical: `git diff stash@{0}` is empty and 688
  pass.
- A duplicate `stash@{0}` is left for the coder to drop.
- A one-line `README.md` change in the tree was not made in this session.

**Cost:** ruff clean. `make audit`: `api` 100%, `compose` 98.4%, total 94.2%.

**Next:** Phase 2, the transport. Reading a lock the user brings stays a separate planned
concern.

**Secrets baseline updated, same session.** `detect-secrets-hook` flagged
`tests/dev_tests/test_api.py:49-50`, the `LYSE` and `ELUTE` constants. They are
`protocol_guid`s copied from `protocols_by_id.json`, whose own guids were already
baselined, so they are fixture values and not credentials.
- **Method:** `detect-secrets scan --baseline` over the tracked files plus untracked,
  non-ignored ones, first run on a copy.
  - `test_api.py` is not tracked yet, so a plain `scan` would have missed it.
  - The copy kept all 70 existing `protocols_by_id.json` entries and added exactly 2.
- **Caught before applying:** scanning a copy made detect-secrets record the copy's
  scratchpad path in the baseline's own `should_exclude_file` filter, which would have
  stopped the baseline excluding itself. It was restored to `security/.baseline.security`.
- **Final diff:** the 2 new entries and `generated_at`.
- **Result:** the hook reports no secrets. It still exits 123 until the baseline is
  staged, which is expected, and staging is left to the coder.

---

## 2026-09-17 — 46efb6b — API Phase 2: the HTTP transport and its documentation

**Built — `api/server.py`,** on the standard library with no new dependency.
- **`ROUTES`:** a dict of `(method, path template)` to a named handler. 12 routes:
  - Protocols: `GET /protocols`, `GET /protocols/{protocol_uid}/versions`, and
    `GET /protocol-versions/{hash}`. The hash has its own top-level path because a hash is
    not a child of a uid.
  - Locks: `POST /locks`.
  - Templates: `GET`/`POST /pipelines`, `GET`/`PUT`/`DELETE /pipelines/{guid}`,
    `GET /pipelines/{guid}/versions`, and `POST /pipelines/{guid}/lock`.
  - Docs: `GET /openapi.json`.
- **`dispatch(method, target, body, protocol_db, compose_db)`** does everything except the
  socket, so nearly all tests run without one.
  - An unknown path is 404. A known path with the wrong method is 405, naming the allowed
    methods.
  - Bad JSON or non-UTF-8 is 400.
  - Path segments are percent-decoded, so `protocols_io%3A568614` works.
- **Errors map by class:**
  - `NOT_FOUND` → 404: `MissingHash`, `UnknownProtocolError`, `UnknownPipelineError`,
    `InactivePipelineError`.
  - `UNPROCESSABLE` → 422: `InvalidRequestError`, `UnsealedProtocolError`,
    `UnresolvedProtocolError`, `NodeMismatchError`, `PipelineCycleError`,
    `DuplicatedIdError`.
  - The 404 and 422 bodies are `{error, message, **vars(error)}`. This relies on the rule
    that an exception takes data, so `nodes`, `problems`, `missing` and `cycle` reach the
    caller with no per-error code.
  - Anything else → 500 carrying only the class name. The traceback goes to stderr.
- **POST creates (201); PUT versions (200, or 201 when a retired template reopens).**
- **Writes are serialized by one `threading.Lock`,** marked `ponytail:`, on a
  `ThreadingHTTPServer`.
- **`serve()` refuses to start without `chronos.db`,** which only a pull creates, and
  creates `compose.db` if it is missing.
- **Entry point:** `python -m api.server`. `DB`, `API_HOST` (default `127.0.0.1`) and
  `API_PORT` (default `8080`) are read in `__main__`.

**Built — `api/openapi.json`**, OpenAPI 3.1, shipped as package data (`pyproject.toml`
`[tool.setuptools.package-data] api = ["*.json"]`). It was generated once by a throwaway
script and is maintained by hand from here. It covers operations, path parameters, request
bodies, every 2xx/404/422 response naming the errors behind it, and schemas for `Error`,
`Interval`, `ProtocolSummary`, `TemplateBody`, `SaveResult`, `Template` and `Lock`.
**`test_every_route_is_documented_and_nothing_else_is`** holds the document's
`(method, path)` set equal to `ROUTES`. Checked by deleting one route: it failed, and
passed again once restored. Further tests require every path parameter to be declared,
every operation to document a 2xx, and every `$ref` to resolve.

**Found by running it, not by the tests — a bodyless POST was refused.** The first version
answered 411 to any POST or PUT with no `Content-Length`. The socket tests always sent one,
so they passed. The live smoke test, `python -m api.server` against fresh stores in the
scratchpad driven with `curl`, got a 411 from `curl -X POST /pipelines/{guid}/lock`, which
sends no length because there is no body. A browser `fetch` with no body does the same, so
the portal would have hit it.
- **Fixed per HTTP/1.1:** no `Content-Length` and no `Transfer-Encoding` means no body.
- A chunked body is refused with 411, because it has no length to cap.
- A non-numeric length is 400.
- `test_a_bodyless_post_needs_no_length` reproduces the exact request.

The smoke run then passed end to end:
- `GET /protocols` → 7.
- `POST /pipelines` → 201.
- The bodyless lock export → a full lock with pinned `node_hashes`.
- A 422 named its nodes.
- `DELETE` then `GET` → 200, then 404.
- The scratch stores were deleted, and `db/` was never touched.

**Secrets:** `test_server.py` repeats the two fixture guids with an inline
`# pragma: allowlist secret`, so the baseline was not changed. The hook is clean.

**Tests:** 688 → 730. `test_server.py` adds 42, covering the OpenAPI document, routing,
bodies, both ports over HTTP, the 500 path, and the socket: round trip, save, the body cap,
bodyless POST, chunked, a bad length, refusing to start, and creating `compose.db`. Ruff
clean. `make audit`: `api` 97.3%. The only uncovered lines are `__main__`, left untested
just as `chronos`'s entry point is.

**Not built:**
- Authentication, deliberately deferred.
- Reading a lock the user brings.
- A Makefile target and a Docker Compose service, whose `API_HOST=0.0.0.0` and unpublished
  port are the ⚠ rule in `AGENT.md`.
- CORS headers. The portal reaches the API server-side over the Compose network, so none
  are needed unless a browser calls it directly.

**Open, from the coder at the end of the session: review the routes first next session.**
Some sections of the route table are not exactly what the coder wants, and some are not
yet clear to them. Before anything is built on the API, walk through `ROUTES` in
`api/server.py` and `openapi.json` together: the paths, the methods, the status codes, and
what each returns. Until then both are provisional. The item is flagged ⚠ in `AGENT.md`
under Storage, which is read first every session.

---

## 2026-09-18 — 1da73b1 — locking collapses to one route; the pipeline is the unit

**Recovered first:** `api/server.py` was missing from the working tree at the start of the
session — deleted from disk after `46efb6b` was committed, with its `.pyc` still in
`__pycache__` and `test_server.py` still present. Nothing in the log accounts for it, so it
was taken as accidental and restored with `git restore`. The 101 `api` + `server` tests
passed unchanged against the restored file, so nothing was lost.

**Decided — there is one way to build a lock: `POST /pipelines/{guid}/lock`.**
`POST /locks`, which took a list of protocol hashes, is deleted.

**Why.** The two routes were two special cases of one operation, and the review that
started with "these seem like the same thing" was right:
- Both are pure reads. Both return a document and store nothing.
- Both return the *same* document. `export_lock` returns `generate_lock(protocols,
  pipeline)`, which is `{**protocol_lock, "pipeline": pipeline_lock}`. `build_lock`
  returned the protocol half alone. One document with an optional `pipeline` key, not two
  document types.
- The only real difference was **who picks the pins**: `/locks` let the caller name exact
  hashes, including historical ones; `/pipelines/{guid}/lock` resolves the template's nodes
  to whatever is active now and refuses by node if a protocol has been retired.
- **The deciding argument: a single protocol is a pipeline with one step.** The ad-hoc set
  `/locks` existed to serve is expressible as a template, so the route had no case left.
- The `seal` layer already supported the union — `generate_lock` takes either-or-both and
  `generate_pipeline_lock` takes several pipelines. The API was *narrower* than the core it
  wraps; deleting a route did not remove a capability, it stopped hiding one.

**Rejected — a merged `POST /locks` taking `pipelines` and/or `hashes`.** It maps 1:1 onto
`generate_lock`'s own signature and was the other candidate, but it keeps two input modes
alive to serve a case that the one-step pipeline already covers, and adds a branch and a
union body to do it. Rejected as the more complex of two designs that do the same work.

**Rejected — pinning historical versions.** A caller still cannot ask for a lock at old
protocol versions; that needs an `as_of` threaded into `hydrate_pipeline`, and nothing
needs it yet. The day-to-version query it would build on is still parked on
`feature/versions-by-date`.

**Why the surviving route is still a POST, and on different grounds than before.**
`/locks` was a POST because a list of `sha256:`-prefixed hashes is ~74 bytes each once
percent-encoded, and proxies cap request lines around 4–8 KB — a lock over ~100 protocols
would have failed in the field, not in the tests. That argument dies with the route: the
survivor takes only a guid. It stays a POST for a different reason — **its answer is
time-dependent.** It pins "the protocols active now", so the same URL legitimately returns
a different lock tomorrow, and a proxy caching a GET would hand out a lock pinned to
yesterday's protocols. POST is uncacheable by default, which is the safe default here.
`GET` plus `Cache-Control: no-store` would work but relies on every intermediary obeying it.
**Flagged as the one part of this decision worth re-litigating** if the route ever needs to
be cacheable.

**Cost.**
- `export_protocols` and `export_pipeline` were deleted in the same session but landed in
  `bdc8e74`; see the next entry.
- `query.build_lock` is deleted; **the query port no longer builds locks at all.** Lock
  generation now reaches `chronos.db` only through the compose port.
- `TestTheQueryPortNeverWrites` lost its lock call as a result, so the `chmod 0444` guard
  no longer covers a lock build. `api.pipelines.export_lock` reads `chronos.db` read-only
  too but is not in that test — it would need a compose fixture in that scope. **Left
  open.**
- **`with_bodies` was preserved, not dropped.** It lived only on `/locks`, so deleting the
  route would have silently removed the only way to get a pins-only lock over the API. It
  moved to `export_lock` as an optional body, `{"with_bodies": false}`. The bodyless POST
  still works — `json_object` is reused so a JSON array is still 422 while no body is legal,
  which keeps the 411 regression from `46efb6b` fixed.
- `openapi.json`: `/locks` removed, `with_bodies` documented, the 422 on the lock route now
  names `InvalidRequestError` as well as `UnresolvedProtocolError`.

**Tests: 730 → 723.** Nine deleted with the route (`TestBuildLock`, two socket tests, the
read-only call), two added back for pins-only at the port and one retargeted at the new
route. `ROUTES` and `openapi.json` both 11 and held equal by the contract test. Ruff clean.
`make audit`: `api` 97.3% → 97.2%, 645 lines from 704. `docs/status.md` rewritten.

**Smoke-tested live**, against a scratch copy of `chronos.db`, because the last session's
real bug was found this way and not by the suite: `POST /locks` → 404; a one-step pipeline
saved from a real `protocol_guid` → 201; the bodyless export → 200 carrying `entries`,
`bodies`, `manifest_hash` and `pipeline`; `{"with_bodies": false}` → no `bodies`;
`{"with_bodies": 1}` → 422. Scratch stores deleted, `db/` untouched.

**Still open:** the route review is half done. The `/protocols` vs `/protocol-versions`
split and the `POST`/`PUT /pipelines` shared handler have not been walked through with the
coder; those remain provisional. The ⚠ in `AGENT.md` was narrowed to say so.

---

## 2026-09-18 — bdc8e74 — a repo-wide ponytail audit; what was cut, and two things that were not

**Asked for:** a whole-repo over-engineering audit and a dead-code hunt, with one rule —
**test usage does not justify production code.** A function exercised only by tests is dead
unless it serves what Hermetica is for.

**Method — reachability, not grep.** Hermetica has exactly two entry points:
`python -m chronos.chronos` and `python -m api.server`. Everything live is reachable from
one of them. A first pass by name-matching produced false positives (`diff_protocols`
looked live because `chronos.py:151` binds a *local variable* of that name), so the real
pass walked the import graph from both entry points and then checked call sites with `ast`
rather than text.

**Deleted — six functions, 79 lines, no behaviour change.**
- `format_entries` (`utils/store.py`) — zero references anywhere, prod or test.
- `as_date` (`utils/dates.py`) — `as_iso` is the live one.
- `read_pulls` (`chronos/pull_log.py`) — the pull log is written and never read back.
- `diff_protocols` (`seal/store.py`), `diff_pipelines` (`compose/store.py`), and
  `version_control_diff` (`utils/intervals.py`), which existed only for those two. A
  read-only "what would this pull change?" is a coherent feature; nothing had ever asked
  for it.
- **The tests kept their coverage**, because `write_protocols` and `write_pipeline` return
  the identical diff dict — the probes now assert through the path production actually
  takes. `test_every_connection_is_closed` drops from 4 connections to 3. Two tests went
  out with their functions.
- `4251 → 4172` lines, `167 → 159` public functions, `utils` coverage 99.0% → 100%.

**Also deleted, earlier in the session:** `export_protocols` and `export_pipeline`
(`seal/seal.py`). Neither had a caller, and `export_pipeline` **wrote an empty file** —
`PIPELINE_KEYS` was `("pipelines",)` while `generate_lock` stores the graph under
`"pipeline"`, so `write_lock_file`'s select-by-presence matched nothing. The
`# Need to check this` comment above the constant was right. `export_lock` escaped it by
luck: `PINS_KEYS` already carries `"pipeline"`, so the full lock was never affected.
`LOCK_KEYS` is now `PINS_KEYS + ("protocols", "bodies")` and the dead plural is gone —
**the bug was closed by deleting the function that used it, not by fixing the constant.**

**Not deleted, deliberately — `protocol_hash` (`seal/contract.py`).** Dead by the letter:
no production call site, three test files import it. Removing three lines meant adding a
shared test helper plus import plumbing that this layout does not support cleanly
(`tests/` and `tests/dev_tests/` are packages; a bare `from conftest import …` does not
resolve). Net zero lines, more indirection, and it stops naming a real rule — *what the
store hashes, metadata excluded*. **Reverted.** The rule that test usage does not justify
production code is right in general and wrong for a two-line statement of a domain
invariant.

**Not done, deliberately — minting the bootstrap template in place.** The audit proposed
dropping the `_minted.yaml` twin, the `mint` flag and both regexes, on the grounds that
minting in place is idempotent by construction and cannot issue a second set of guids.
**Withdrawn after testing it:** `safe_load` → `safe_dump` is not a round trip. It erased
all nine comment lines from `config/pg_core_templates.yaml`, including the block
explaining what `nodes` and `dag` mean. The twin exists to keep the hand-authored,
documented source separate from the generated guid-bearing file, and that is a better
reason than the one the code gives. AGENT.md now records why, so it is not re-proposed.

**Found while checking that — ⚠ guids are not reproducible across clones.**
`config/pg_core_templates.yaml` is tracked with `pipeline_guid: null`; no `_minted.yaml`
is in git and `.gitignore` never mentions one. The guids therefore live only in an
untracked file generated at bootstrap, so **two deploys from a fresh clone mint different
guids for the same pipelines** — and the guid is identity across versions. Not fixed here:
the fix is committing the minted twin once, which is a decision about what the repo
carries, not a code change. Flagged ⚠ in `AGENT.md` under Pipelines.

**The audit's real conclusion: this repo is not bloated, it is under-wired.** Of the 731
lines with no production caller, only 79 were genuinely dead. The rest is finished,
tested work with no entrance — `scribe` (527 lines, a lock back into something a human
reads), the bootstrap loader (96), and the lock verify/export pair (50). Each needs a
door, not a delete. AGENT.md now names them, and the reachability rule, so the next audit
does not relitigate them.

**Tests: 723 → 721.** Ruff clean. `make audit`: TOTAL 94.3% → 94.5%, `utils` 100%.
