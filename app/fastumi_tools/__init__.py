"""FastUMI Tools local management application."""

import re
from pathlib import Path


_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
_SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def _read_version() -> str:
    """Read the application version from the repository/package VERSION file."""
    try:
        value = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("FastUMI Tools VERSION file is missing: %s" % _VERSION_FILE) from exc
    if not _SEMVER_PATTERN.fullmatch(value):
        raise RuntimeError("Invalid FastUMI Tools version in %s: %r" % (_VERSION_FILE, value))
    return value


__version__ = _read_version()
