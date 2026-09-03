# -----------------------------------------------------------------------------#
# This is where we have all the "configs" and constants
# This could be a set of builder functions
# It just irks me to have to find where all of this shit is
# -----------------------------------------------------------------------------#

# -----------------------------------------------------------------------------#
# PIPELINES
# -----------------------------------------------------------------------------#
PIPELINE_HASH_FIELDS: tuple[str, ...] = (
    "guid",
    "title",
    "manifest_hash",
    "DAG",
    "executor",
    "root",
)
# Specify other in
PIPELINE_METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
)

PIPELINE_FIELDS: tuple[str, ...] = PIPELINE_HASH_FIELDS + PIPELINE_METADATA_FIELDS


# -----------------------------------------------------------------------------#
# PROTOCOLS
# -----------------------------------------------------------------------------#
PROTOCOL_HASH_FIELDS: tuple[str, ...] = (
    "doi",
    "reserved_doi",
    "id",
    "guid",
    "title",
    "description",
    "guidelines",
    "before_start",
    "disclaimer",
    "warning",
    "materials",
    "steps",
    "chain",
    "units",
    "uri",
    "version_class",
    "protocol_references",
)

PROTOCOL_METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
    "authors",
    "keywords",
)
PROTOCOL_FIELDS: tuple[str, ...] = PROTOCOL_HASH_FIELDS + PROTOCOL_METADATA_FIELDS

# -----------------------------------------------------------------------------#
# PROTOCOL DB PULLS
# -----------------------------------------------------------------------------#
PROTOCOL_CONTENT_FIELDS: tuple[str, ...] = (
    "hash",
    "protocol_id",
    "protocol_guid",
    "title",
    "doi",
    "reserved_doi",
    "uri",
    "protocol",
) + PROTOCOL_METADATA_FIELDS


PROTOCOL_CONTENT = "protocol_content"
PROTOCOL_HISTORY = "protocol_history"
PROTOCOL_ID = "protocol_id"

# -----------------------------------------------------------------------------#
# PIPELINE DB PULLS
# -----------------------------------------------------------------------------#

PIPELINE_CONTENT_FIELDS: tuple[str, ...] = (
    "hash",
    "pipeline_guid",
    "title",
    "manifest_hash",
    "root",
    "executor",
    "DAG",
    "pipeline",
) + PIPELINE_METADATA_FIELDS


PIPELINE_CONTENT = "pipeline_content"
PIPELINE_HISTORY = "pipeline_history"
PIPELINE_GUID = "pipeline_guid"


# -----------------------------------------------------------------------------#
# LOCKS
# -----------------------------------------------------------------------------#
PINS_KEYS: tuple[str, ...] = (
    "manifest_hash",
    "as_of",
    "created_at",
    "provenance",
    "entries",
    "pipeline",
)
PROTOCOL_KEYS: tuple[str, ...] = ("protocols", "bodies")

# Need to check this
PIPELINE_KEYS: tuple[str, ...] = "dag"

LOCK_KEYS: tuple[str, ...] = PINS_KEYS + PROTOCOL_KEYS + PIPELINE_KEYS


# -----------------------------------------------------------------------------#
# Hashing algorithm
# -----------------------------------------------------------------------------#
HASH_ALGORITHM = "sha256"


# -----------------------------------------------------------------------------#
# DRIFT VERIFICATION
# -----------------------------------------------------------------------------#
# not sure I am going to keep this.
DRIFT: tuple[str, ...] = (
    "manifest_hash",
    "body_hash",
    "missing_bodies",
    "orphan_bodies",
)
