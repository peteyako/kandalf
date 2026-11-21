"""Where a kandalf was built from: the release tag on its commit, or a later commit (a dev
build), and when it was built. setup.py's build step writes describe() into the package as
_build.json; a kandalf run from a checkout asks git when the server starts. The page shows it
in its bottom right corner (versionLabel there). Standard library only, with no imports from
the package, so setup.py can load this file on its own."""

import datetime
import json
import subprocess
from pathlib import Path

BUILD_FILE = "_build.json"


def _git(src: Path, *args) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def describe(src: Path, now: datetime.datetime | None = None) -> dict | None:
    """Where a source checkout stands: {"tag": the tag on HEAD, None when there is none or
    files aren't committed, "commit": HEAD's short hash, "dirty": files not committed,
    "built": now, ISO 8601 in UTC}. None when src isn't a git checkout."""
    commit = _git(src, "rev-parse", "--short", "HEAD")
    if not commit:
        return None
    dirty = bool(_git(src, "status", "--porcelain", "--untracked-files=no"))
    tag = _git(src, "describe", "--tags", "--exact-match", "HEAD")
    when = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    return {"tag": None if dirty else tag or None, "commit": commit, "dirty": dirty,
            "built": when.isoformat(timespec="seconds")}


def info(package: Path, version: str) -> dict:
    """What the page is told: {"version"} and, when known, describe's facts: from the package's
    _build.json, else from the checkout the package sits in."""
    built = package / BUILD_FILE
    facts = None
    if built.is_file():
        try:
            facts = json.loads(built.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    elif (package.parent / ".git").exists():
        facts = describe(package.parent)
    return {"version": version, **(facts or {})}
