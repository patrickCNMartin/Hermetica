# -----------------------------------------------------------------------------#
# This is where custom error handling well be stored
# Agentic formating create error classes that do the same thing
# and are basically pointless
# -----------------------------------------------------------------------------#


class MissingHash(ValueError):
    """Hash Value not found in data base."""


class DuplicatedIdError(ValueError):
    """Duplicated entries in the version control data base"""


class UnreadableProtocolError(ValueError):
    """Cannot read the protocol from a given source"""


class MalformedLockError(ValueError):
    """The file is not a lock document — a key the format requires is missing."""
