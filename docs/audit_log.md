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
