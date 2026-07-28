# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import argparse
import json
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------#
# JSONCANVAS PRIMITIVES
# -----------------------------------------------------------------------------#
# Obsidian's canvas format (jsoncanvas.org): nodes carry x/y/width/height, edges
# name a node and a side. Colour is "1".."6" against the theme palette.
RED, ORANGE, YELLOW, GREEN, CYAN, PURPLE = "1", "2", "3", "4", "5", "6"

Side = str  # "top" | "right" | "bottom" | "left"


class Canvas:
    """Accumulates nodes and edges, then serializes to a .canvas document."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    # Groups are emitted first so Obsidian stacks them behind their children.
    def group(
        self,
        node_id: str,
        label: str,
        x: int,
        y: int,
        width: int,
        height: int,
        color: str | None = None,
    ) -> None:
        """A labelled backdrop rectangle."""
        node = {
            "id": node_id,
            "type": "group",
            "label": label,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        if color:
            node["color"] = color
        self.nodes.append(node)

    def text(
        self,
        node_id: str,
        body: str,
        x: int,
        y: int,
        width: int,
        height: int,
        color: str | None = None,
    ) -> None:
        """A markdown card."""
        node = {
            "id": node_id,
            "type": "text",
            "text": body,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        if color:
            node["color"] = color
        self.nodes.append(node)

    def edge(
        self,
        from_node: str,
        to_node: str,
        label: str | None = None,
        from_side: Side = "bottom",
        to_side: Side = "top",
        color: str | None = None,
    ) -> None:
        """A directed arrow between two nodes."""
        edge = {
            "id": f"e{len(self.edges):03d}",
            "fromNode": from_node,
            "fromSide": from_side,
            "toNode": to_node,
            "toSide": to_side,
            "toEnd": "arrow",
        }
        if label:
            edge["label"] = label
        if color:
            edge["color"] = color
        self.edges.append(edge)

    def write(self, path: Path) -> None:
        """Serialize to disk."""
        document = {"nodes": self.nodes, "edges": self.edges}
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------#
# DATA FLOW — what the payload becomes at each step
# -----------------------------------------------------------------------------#
def build_data_flow() -> Canvas:
    """The protocols.io payload's journey from JSON to two tables."""
    c = Canvas()

    c.group("g1", "1 · Acquire — bytes in", -560, -320, 1000, 760, CYAN)
    c.group("g2", "2 · Select & hash — what the bytes mean", -560, 500, 1480, 1180,
            PURPLE)
    c.group("g3", "3 · Dedupe & map to rows", -560, 1740, 1480, 620, GREEN)
    c.group("g4", "4 · Diff & write", -560, 2420, 1480, 560, GREEN)
    c.group("g5", "5 · chronos.db — append-only", -560, 3040, 1480, 500, ORANGE)

    c.text(
        "env",
        "**`env/.env`** → `chronos.py:19-31`\n\n"
        "`API_KEY` · `BASE_URL` · `PROTOCOL_URL` · `DB`\n\n"
        "`PULL_PARAMS`: `order_field=id` (must be a unique key), "
        "`key=\" \"` (required), `page_size=10`, `max_pull=None`",
        0, -460, 420, 130, YELLOW,
    )
    c.text(
        "api",
        "**protocols.io**\n`GET /v3/protocols`\n\nUntrusted external input.",
        0, -240, 420, 110, ORANGE,
    )
    c.text(
        "walk",
        "**`_walk_pages`** · `request_utils.py:22`\n\n"
        "`page_id` starts at **0**. The loop is driven by "
        "`payload['pagination'].next_page`, never by page length.",
        0, -90, 420, 150, CYAN,
    )
    c.text(
        "count",
        "**count check** · `request_utils.py:89`\n\n"
        "`len(protocols) == total_results`? → one greedy retry → raise.\n"
        "Skipped when capped or resumed.",
        0, 100, 420, 130, CYAN,
    )
    c.text(
        "incomplete",
        "`IncompletePullError`\n\n"
        "A partial pull must never look like upstream deletions.",
        -520, 100, 420, 130, RED,
    )
    c.text(
        "raw",
        "**`list[dict]`** — raw records\n\nEvery upstream field still present.",
        0, 270, 420, 110, CYAN,
    )

    c.edge("env", "api", "config")
    c.edge("api", "walk", "paginated JSON")
    c.edge("walk", "count", "(items, total)")
    c.edge("count", "incomplete", "mismatch twice", "left", "right", RED)
    c.edge("count", "raw", "verified")

    c.text(
        "select",
        "**`select_protocol`** · `contract.py:81`\n\n"
        "Keep `STABLE_FIELDS` (11, **required**) + `METADATA_FIELDS` (3, "
        "optional).\nAllowlist — nothing unasked-for can enter.",
        0, 560, 420, 140, PURPLE,
    )
    c.text(
        "missing",
        "`MissingStableFieldsError`\n\n"
        "A missing stable field would hash as \"removed\" and mint a bogus "
        "version for everything affected.",
        -520, 560, 420, 140, RED,
    )
    c.text(
        "selected",
        "**`selected: dict`**\n\nidentity fields **+** metadata, together.",
        0, 760, 420, 120, PURPLE,
    )
    c.text(
        "meta",
        "**`METADATA_FIELDS`** — retained, *never hashed*\n\n"
        "`created_on` · `authors` · `creator`\n\n"
        "Re-attribution is not a new protocol version.",
        520, 760, 420, 140, YELLOW,
    )
    c.text(
        "hashable",
        "**`hashable_content`** · `contract.py:107`\n\n"
        "Drops metadata, applies the scrub. **This is the fork.**",
        0, 940, 420, 140, PURPLE,
    )
    c.text(
        "scrub",
        "**`scrub_signed_urls`** · `contract.py:67`\n\n"
        "Blanks the *values* of 11 AWS/CloudFront params. The URL and filename "
        "**stay hashed** — swapping the attached file is a real version "
        "change.\n\nLeft in: two hashes 34 s apart.",
        -520, 940, 420, 160, PURPLE,
    )
    c.text(
        "canon",
        "**`canonical_json`** · `canonical.py:27`\n\n"
        "`_normalize` NFC → `sort_keys` → `separators=(',',':')` → "
        "`ensure_ascii` → `allow_nan=False` → utf-8",
        0, 1140, 420, 160, PURPLE,
    )
    c.text(
        "blob",
        "**`blob: bytes`**\n\nThe exact bytes that get stored *and* hashed.",
        0, 1360, 420, 110, PURPLE,
    )
    c.text(
        "hash",
        "**`hash_bytes`** → SHA256 hexdigest\n\nThe version identity.",
        0, 1530, 420, 110, PURPLE,
    )

    c.edge("raw", "select", "per record")
    c.edge("select", "missing", "field absent", "left", "right", RED)
    c.edge("select", "selected")
    c.edge("selected", "meta", "metadata branch", "right", "left", YELLOW)
    c.edge("selected", "hashable", "identity branch")
    c.edge("scrub", "hashable", "applied per field", "right", "left")
    c.edge("hashable", "canon")
    c.edge("canon", "blob")
    c.edge("blob", "hash")

    c.text(
        "unique",
        "**`get_unique_protocols`** · `request_utils.py:122`\n\n"
        "`{hash: selected}` — first-wins. Identical content collapses to one "
        "version.",
        0, 1800, 420, 130, CYAN,
    )
    c.text(
        "torow",
        "**`to_row` / `to_rows`** · `store.py:115`\n\n"
        "Re-derives the blob and hash, then attaches metadata and `valid_from`.",
        0, 1990, 420, 170, GREEN,
    )
    c.text(
        "pulled",
        "**`now_epoch()`** · `chronos.py:72`\n\n"
        "One pull, **one** timestamp — it dates both the new intervals and the "
        "closures. The clock is not read per row.",
        520, 1990, 420, 140, YELLOW,
    )
    c.text(
        "metajson",
        "**`_metadata_json`** · `store.py:109`\n\n"
        "`authors` / `creator` → canonical JSON text.\n"
        "**`to_epoch(created_on)`** → `valid_from`, else `pulled_at`.",
        -520, 1990, 420, 140, YELLOW,
    )
    c.text(
        "row",
        "**`ProtocolRow`** (9 fields)\n\n"
        "The first **8** are `protocol_content` columns, in column order. The "
        "9th, `valid_from`, belongs to history.",
        0, 2220, 420, 120, GREEN,
    )

    c.edge("hash", "unique", "keyed by hash")
    c.edge("meta", "torow", "rides along", "bottom", "right", YELLOW)
    c.edge("unique", "torow")
    c.edge("pulled", "torow", "pulled_at", "left", "right", YELLOW)
    c.edge("metajson", "torow", None, "right", "left", YELLOW)
    c.edge("torow", "row")

    c.text(
        "diff",
        "**`_diff`** · `store.py:159`\n\n"
        "active (`deprecated_at IS NULL`) vs incoming →\n"
        "**new** · **changed** · **unchanged** · **absent**\n\n"
        "Absence is the part content-addressing cannot see.",
        0, 2480, 420, 180, GREEN,
    )
    c.text(
        "dup",
        "`DuplicateProtocolIdError`\n\n"
        "Two versions of one `protocol_id` in a single pull — at most one may "
        "ever be active.",
        -520, 2480, 420, 160, RED,
    )
    c.text(
        "write",
        "**`write_pull`** · `store.py:233`\n\n"
        "`opening = new ∪ changed`\n`closing = changed ∪ absent`\n"
        "`first_time = new − _seen_before`\n\n"
        "→ INSERT content · CLOSE interval · OPEN interval",
        0, 2720, 420, 200, GREEN,
    )
    c.text(
        "validfrom",
        "**Backdating rule**\n\n"
        "Only a `protocol_id`'s **first-ever** version backdates to "
        "`created_on`.\n\n"
        "\"First-ever\" = *no history at all*, not \"no live version\" — a "
        "protocol that vanishes and returns must open at the pull that found "
        "it again.",
        520, 2720, 420, 200, YELLOW,
    )

    c.edge("row", "diff")
    c.edge("diff", "dup", "guard", "left", "right", RED)
    c.edge("diff", "write", "the diff drives the write")
    c.edge("validfrom", "write", "picks valid_from", "left", "right", YELLOW)

    c.text(
        "content",
        "**`protocol_content`**\n"
        "`hash` PK · ids · name · `protocol` (blob) · `created_on` · `authors` "
        "· `creator`\n\n`INSERT OR IGNORE` — never deleted.",
        -460, 3100, 400, 170, ORANGE,
    )
    c.text(
        "history",
        "**`protocol_history`**\n"
        "`protocol_id` · `hash` · `valid_from` · `deprecated_at`\n\n"
        "`[valid_from, deprecated_at)` — intervals must never overlap.",
        0, 3100, 400, 170, ORANGE,
    )
    c.text(
        "snap",
        "**`snapshots`**\n`manifest_hash` PK · `created_at` · `provenance`\n\n"
        "*Schema only — the manifest builder is Phase 2.*",
        460, 3100, 400, 170, ORANGE,
    )
    c.text(
        "verify",
        "**`verify_protocols`** · `store.py:283`\n\n"
        "Re-hashes every stored blob and returns the keys that no longer "
        "match. Closes the loop back to `hash_bytes`.",
        0, 3350, 400, 140, GREEN,
    )

    c.edge("write", "content", "blob + metadata")
    c.edge("write", "history", "close / open")
    c.edge("content", "verify", "re-hash", "bottom", "left")

    return c


# -----------------------------------------------------------------------------#
# CALL GRAPH — who invokes whom
# -----------------------------------------------------------------------------#
def build_call_graph() -> Canvas:
    """Static call graph, layered outward from the cron entry point."""
    c = Canvas()

    c.group("cg_main", "entry point", 740, -80, 480, 210, CYAN)
    c.group("cg_live", "the cron path", -460, 180, 2760, 1140)
    c.group(
        "cg_off",
        "not on the cron path — read-only, boundary, unbuilt",
        -460, 1400, 2760, 480, YELLOW,
    )

    c.text(
        "main",
        "**`chronos.py` `__main__`**\n\n"
        "Must run as `python -m chronos.chronos` — invoked by path, `chronos` "
        "resolves to the module instead of the package.",
        780, -20, 400, 130, CYAN,
    )

    layer_1 = 240
    c.text("init_db", "`initialize_db`\n`store.py:81`", -420, layer_1, 300, 80,
           GREEN)
    c.text("get_list", "`get_protocol_list`\n`request_utils.py:65`", 0, layer_1,
           300, 80, CYAN)
    c.text("proc", "`process_protocols`\n`request_utils.py:109`", 340, layer_1,
           300, 80, CYAN)
    c.text("now", "`now_epoch`\n`dates.py:14`", 680, layer_1, 300, 80, YELLOW)
    c.text("to_rows", "`to_rows`\n`store.py:138`", 1020, layer_1, 300, 80, GREEN)
    c.text("write_pull", "`write_pull`\n`store.py:233`", 1360, layer_1, 300, 80,
           GREEN)

    for node in ("init_db", "get_list", "proc", "now", "to_rows", "write_pull"):
        c.edge("main", node)

    layer_2 = 440
    c.text(
        "connect",
        "`connect`\n`store.py:22`\n*commit/rollback + close in finally*",
        -420, layer_2, 300, 110, GREEN,
    )
    c.text("walk", "`_walk_pages`\n`request_utils.py:22`", 0, layer_2, 300, 80,
           CYAN)
    c.text("sel", "`select_protocol`\n`contract.py:81`", 340, layer_2, 300, 80,
           PURPLE)
    c.text("phash", "`protocol_hash`\n`contract.py:127`", 680, layer_2, 300, 80,
           PURPLE)
    c.text("uniq", "`get_unique_protocols`\n`request_utils.py:122`", 1020,
           layer_2, 300, 80, CYAN)
    c.text("to_row", "`to_row`\n`store.py:115`", 1360, layer_2, 300, 80, GREEN)
    c.text("diff", "`_diff`\n`store.py:159`", 1700, layer_2, 300, 80, GREEN)
    c.text("seen", "`_seen_before`\n`store.py:182`", 2040, layer_2, 300, 80,
           GREEN)

    c.edge("init_db", "connect")
    c.edge("get_list", "walk", "×2 on retry")
    c.edge("proc", "sel")
    c.edge("proc", "phash")
    c.edge("proc", "uniq")
    c.edge("to_rows", "to_row")
    c.edge("write_pull", "connect", None, "left", "right")
    c.edge("write_pull", "diff")
    c.edge("write_pull", "seen")
    c.edge("write_pull", "now", "default", "top", "bottom")

    layer_3 = 660
    c.text("req", "`requests.get`\n*+ `raise_for_status`*", 0, layer_3, 300, 80,
           ORANGE)
    c.text("scrub", "`scrub_signed_urls`\n`contract.py:67`", 340, layer_3, 300,
           80, PURPLE)
    c.text("pblob", "`protocol_blob`\n`contract.py:120`", 680, layer_3, 300, 80,
           PURPLE)
    c.text("mjson", "`_metadata_json`\n`store.py:109`", 1360, layer_3, 300, 80,
           GREEN)
    c.text("to_epoch", "`to_epoch`\n`dates.py:19`", 1700, layer_3, 300, 80,
           YELLOW)
    c.text("active", "`_active_hashes`\n`store.py:150`", 2040, layer_3, 300, 80,
           GREEN)

    c.edge("walk", "req")
    c.edge("phash", "pblob")
    c.edge("to_row", "mjson")
    c.edge("to_row", "to_epoch")
    c.edge("to_row", "pblob", None, "left", "right")
    c.edge("diff", "active")

    layer_4 = 880
    c.text("hashable", "`hashable_content`\n`contract.py:107`", 680, layer_4,
           300, 80, PURPLE)
    c.edge("pblob", "hashable")
    c.edge("hashable", "scrub", None, "left", "right")

    layer_5 = 1080
    c.text("norm", "`_normalize`\n`canonical.py:16` — NFC", 340, layer_5, 300,
           80, PURPLE)
    c.text("canon", "`canonical_json`\n`canonical.py:27`", 680, layer_5, 300, 80,
           PURPLE)
    c.text("hbytes", "`hash_bytes`\n`canonical.py:44`", 1020, layer_5, 300, 80,
           PURPLE)

    c.edge("hashable", "canon")
    c.edge("pblob", "canon", None, "right", "right")
    c.edge("canon", "norm", None, "left", "right")
    c.edge("phash", "hbytes", None, "right", "top")
    c.edge("to_row", "hbytes", None, "bottom", "right")
    c.edge("mjson", "canon", None, "bottom", "right")

    off_path = 1460
    c.text(
        "verify",
        "`verify_protocols`\n`store.py:283`\n→ `connect(ro)`, `hash_bytes`",
        -420, off_path, 300, 110, GREEN,
    )
    c.text(
        "diff_pull",
        "`diff_pull`\n`store.py:203`\n→ `connect(ro)`, `_diff`",
        -80, off_path, 300, 110, GREEN,
    )
    c.text(
        "get_active",
        "`get_active_hashes`\n`store.py:197`\n→ `connect(ro)`, `_active_hashes`",
        260, off_path, 300, 110, GREEN,
    )
    c.text(
        "chash",
        "`content_hash`\n`canonical.py:49`\n*unused by the pull path*",
        600, off_path, 300, 110, PURPLE,
    )
    c.text(
        "render",
        "`from_epoch` · `as_date` · `as_iso`\n`dates.py:42-54`\n"
        "*call-boundary rendering only*",
        940, off_path, 300, 110, YELLOW,
    )
    c.text(
        "eod",
        "`end_of_day`\n`dates.py:57`\n*upper bound for \"as of date D\"*",
        1280, off_path, 300, 110, YELLOW,
    )
    c.text(
        "lock",
        "`generate_protocol_lock`\n`seal.py:10`\n**stub — Phase 2**",
        1620, off_path, 300, 110, RED,
    )
    c.text(
        "ports",
        "**query port** (ro) · **compose port** (write)\n"
        "*Phase 4 — no raw SQL crosses the boundary*",
        1960, off_path, 300, 110, RED,
    )

    return c


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
CANVASES = {
    "hermetica-data-flow.canvas": build_data_flow,
    "hermetica-call-graph.canvas": build_call_graph,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Obsidian canvases of Hermetica's structure."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.cwd(),
        help="output directory (point it at an Obsidian vault)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for name, build in CANVASES.items():
        canvas = build()
        canvas.write(args.out / name)
        print(
            f"{name}: {len(canvas.nodes)} nodes, {len(canvas.edges)} edges "
            f"-> {args.out / name}"
        )


if __name__ == "__main__":
    main()
