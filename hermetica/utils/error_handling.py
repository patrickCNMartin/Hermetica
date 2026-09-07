# -----------------------------------------------------------------------------#
# This is where custom error handling well be stored
# Agentic formating create error classes that do the same thing
# and are basically pointless
# -----------------------------------------------------------------------------#


class MissingHash(ValueError):
    """Hash Value not found in data base."""


class MalformedLockError(ValueError):
    """The file is not a lock document — a key the format requires is missing."""
