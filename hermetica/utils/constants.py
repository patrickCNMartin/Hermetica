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
    "source",
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
    # Who runs it. Hashed: the same steps run by a human and by a robot are two
    # protocols. Declared upstream, never inferred — "" means nobody said.
    "executor",
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
    "protocol_uid",
    "source",
    "protocol_id",
    "protocol_guid",
    "title",
    "doi",
    "reserved_doi",
    "uri",
    "executor",
    "protocol",
) + PROTOCOL_METADATA_FIELDS


PROTOCOL_CONTENT = "protocol_content"
PROTOCOL_HISTORY = "protocol_history"
# Identity is the qualified uid; the bare id alone collides across platforms.
PROTOCOL_UID = "protocol_uid"
PROTOCOL_ID = "protocol_id"
PROTOCOL_SOURCE = "source"

# -----------------------------------------------------------------------------#
# PIPELINE DB PULLS
# -----------------------------------------------------------------------------#

PIPELINE_CONTENT_FIELDS: tuple[str, ...] = (
    "hash",
    "pipeline_guid",
    "title",
    "manifest_hash",
    "root",
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
PIPELINE_KEYS: tuple[str, ...] = ("pipelines",)

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
