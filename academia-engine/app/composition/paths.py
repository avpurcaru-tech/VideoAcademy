import ntpath
import re
from pathlib import Path
from typing import Any


_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def validate_local_path(value: Any, field_name: str) -> None:
    """Reject implicit, remote, URI, query-bearing, or fragment-bearing paths."""
    if not isinstance(value, (str, Path)):
        raise ValueError(f"{field_name} must be an explicit local path.")
    raw = str(value).strip()
    if not raw or raw in {".", ".."}:
        raise ValueError(f"{field_name} must be explicit.")
    normalized = raw.replace("\\", "/")
    if not _WINDOWS_DRIVE.match(raw) and _URI_SCHEME.match(raw):
        raise ValueError(f"{field_name} must not contain a URI scheme.")
    if normalized.lower().startswith(("http:/", "https:/", "//")):
        raise ValueError(f"{field_name} must be local, not remote.")
    if "?" in raw or "#" in raw:
        raise ValueError(f"{field_name} must not contain URL query or fragment data.")


def normalized_local_path(path: Path) -> str:
    """Return a Windows-stable lexical identity for output path comparisons."""
    return ntpath.normcase(ntpath.abspath(str(path)))
