# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# -----------------------------------------------------------------------------#
# IMPORT GENERIC UTILS
# -----------------------------------------------------------------------------#
from chronos.pull_log import record_pull
from chronos.report import format_failure, format_report, write_report
from compose.store import SCHEMA as PIPELINE_SCHEMA
from seal.store import SCHEMA as PROTOCOL_SCHEMA
from seal.store import format_protocol_entry, write_protocols

# -----------------------------------------------------------------------------#
# IMPORT SOURCE ADAPTERS
# -----------------------------------------------------------------------------#
from sources import protocols_io
from sources.contract import ProtocolSource, check_source_name
from utils.dates import get_timestamp
from utils.error_handling import UnreadableProtocolError
from utils.store import initialize_db

# -----------------------------------------------------------------------------#
# SET ENV VARS
# -----------------------------------------------------------------------------#
dotenv_path = Path.cwd() / "env" / ".env"
load_dotenv(dotenv_path=dotenv_path)


API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
PROTOCOL_LIST_URL = os.getenv("PROTOCOL_LIST_URL", "")
PROTOCOL_URL = os.getenv("PROTOCOL_URL", "")

CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")

EMAIL = os.getenv("EMAIL", "")
LOGS = os.getenv("LOGS", "logs")
DB_OUT = os.getenv("DB", "db")

# A set of pre-built pipelines.
PIPE_TEMPLATE = os.getenv("PIPE_TEMPLATE", "")

# Which platforms tonight's run reads, in order. Comma separated.
SOURCES = os.getenv("SOURCES", "protocols_io")

# Walk uses old entry point which goes through folder - works but archived
# filter uses the modern entry point - but sucks.
# This might need to be updated completely depending on what happens
# with protocols.io
PULL_STRATEGY = os.getenv("PULL_STRATEGY", "walk")


# -----------------------------------------------------------------------------#
# WHICH SOURCES TO PULL
# -----------------------------------------------------------------------------#
def build_sources(
    names: list[str],
    base_url: str,
    api_key: str,
    strategy: str = "walk",
    list_url: str = "",
    protocol_url: str = "",
    page_size: int = 10,
    max_pull: int | None = None,
    raw_dump: str = "",
) -> list[ProtocolSource]:
    """Turn source names into configured adapters.

    Every value arrives as an argument — the env is read once, in __main__, and
    passed down. A new platform is one more branch here plus its own arguments.
    """
    sources = []
    for name in names:
        if name == "protocols_io":
            sources.append(
                protocols_io.build_source(
                    base_url=base_url,
                    api_key=api_key,
                    strategy=strategy,
                    list_url=list_url,
                    protocol_url=protocol_url,
                    page_size=page_size,
                    max_pull=max_pull,
                    raw_dump=raw_dump,
                )
            )
        else:
            raise ValueError(f"unknown source {name!r}")
    return sources


# -----------------------------------------------------------------------------#
# ONE PULL, ONE SOURCE
# -----------------------------------------------------------------------------#
def run_protocol_pull(db_name: str, pulled_at: int, source: ProtocolSource) -> dict:
    """Discover, fetch, seal. Returns the entry written to the log.

    Knows no field names: whatever a platform calls things is settled by the
    time an artefact arrives.
    """
    check_source_name(source.name)

    discovery = source.discover()
    print(
        f"{source.name}: strategy={discovery.strategy} -> "
        f"{len(discovery.ids)} protocols to fetch"
    )
    for warning in discovery.detail.get("warnings", []):
        print(f"  WARNING: {warning}")

    artefacts, retired, warnings = [], [], []
    for protocol_id in discovery.ids:
        fetched = source.fetch(protocol_id)
        warnings.extend(fetched.warnings)
        for warning in fetched.warnings:
            print(f"  WARNING: {warning}")

        if fetched.retired:
            retired.append(protocol_id)
            print(f"  retired, not sealed: {protocol_id}")
            continue
        # A read that failed is not evidence the protocol went away. Until the
        # skipped set is subtracted inside _diff, the only safe answer is to
        # stop: nothing is written, so nothing is deprecated by absence.
        if fetched.artefact is None:
            raise UnreadableProtocolError(
                f"{source.name} returned no artefact for {protocol_id} and did "
                f"not declare it retired"
            )
        artefacts.append(fetched.artefact)

    protocol_entries = format_protocol_entry(artefacts, pulled_at)
    diff_protocols = write_protocols(db_name, protocol_entries, pulled_at)

    return {
        "source": source.name,
        "strategy": discovery.strategy,
        **discovery.detail,
        "fetched": len(discovery.ids),
        "deprecated": sorted(retired),
        "sealed": len(protocol_entries),
        "diff": {key: sorted(value) for key, value in diff_protocols.items()},
        "warnings": discovery.detail.get("warnings", []) + warnings,
    }


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    # Initialize data base and create if does not exist (schema only).
    protcol_db = f"{DB_OUT}/chronos.db"
    pipeline_db = f"{DB_OUT}/compose.db"
    initialize_db(protcol_db, PROTOCOL_SCHEMA)
    initialize_db(pipeline_db, PIPELINE_SCHEMA)

    # pull time stamp
    # This is when we start the pull - everything else follows this pull time
    # One clock read for every source, so their intervals line up.
    pulled_at = get_timestamp()
    names = [name.strip() for name in SOURCES.split(",") if name.strip()]

    configured = build_sources(
        names,
        base_url=BASE_URL,
        api_key=API_KEY,
        strategy=PULL_STRATEGY,
        list_url=PROTOCOL_LIST_URL,
        protocol_url=PROTOCOL_URL,
        raw_dump=DB_OUT,
    )

    reports, failed = [], False
    for source in configured:
        # Per source, not around the loop: one platform being down must not stop
        # the others, and a source that raises writes nothing, so none of its
        # protocols are deprecated by absence.
        try:
            entry = run_protocol_pull(protcol_db, pulled_at, source)
        except Exception as error:
            failed = True
            entry = {
                "source": source.name,
                "strategy": PULL_STRATEGY,
                "failed": True,
                "error": f"{type(error).__name__}: {error}",
            }
            record_pull(LOGS, pulled_at, entry)
            reports.append(format_failure({**entry, "pulled_at": pulled_at}, error))
            print(f"{source.name}: pull FAILED — Check pull logs")
            continue

        record_pull(LOGS, pulled_at, entry)
        reports.append(format_report({**entry, "pulled_at": pulled_at}))

    write_report(LOGS, "\n".join(reports))

    # place holder to transfer log to the maintainer
    if EMAIL:
        # No mail route is configured yet; the report is written for a human to
        # collect or for cron to pipe onward.
        print(f"  (intended for {EMAIL} — no mail transport wired)")

    if failed:
        sys.exit(1)
