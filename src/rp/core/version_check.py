"""Lightweight update notifier.

Fetches the `version =` line from `pyproject.toml` on the default branch of
the GitHub repo, caches the result for ~24h, and prints a one-line notice
when a newer release is available. The check is best-effort: any network,
parse, or filesystem error degrades silently so a flaky connection never
breaks a `rp` command.
"""

from __future__ import annotations

import contextlib
import json
import re
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PYPROJECT_URL = (
    "https://raw.githubusercontent.com/agencyenterprise/rp/main/pyproject.toml"
)
REPO_URL = "https://github.com/agencyenterprise/rp"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_AGE_HOURS = 24.0


def _find_editable_repo_root() -> Path | None:
    """If rp is installed editable from a git checkout, return that root.

    Walks up from this file's location looking for a `.git` directory.
    Returns None for wheel installs (where the source lives in site-packages).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _build_notice(installed: str, latest: str) -> str:
    repo = _find_editable_repo_root()
    if repo is not None:
        upgrade = f"Run `cd {repo} && git pull && uv pip install -e .` to upgrade."
    else:
        upgrade = f"See {REPO_URL} for upgrade instructions."
    return f"A new version of rp is available: {installed} → {latest}. {upgrade}"


@dataclass(frozen=True)
class CacheEntry:
    checked_at: datetime
    latest_version: str


# ── parsing ──────────────────────────────────────────────────────────


def parse_version_from_pyproject(toml_text: str) -> str | None:
    """Return the [project].version string, or None if missing/unparseable."""
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def _parse_version_tuple(s: str) -> tuple[int, ...] | None:
    """Parse a simple dotted-numeric version into a tuple. None on garbage.

    Pre-release / local-version segments aren't supported because we don't
    publish them — keeping this comparator minimal avoids pulling in
    `packaging` as a runtime dep.
    """
    if not _VERSION_RE.match(s):
        return None
    return tuple(int(part) for part in s.split("."))


def is_newer(latest: str, installed: str) -> bool:
    """True iff `latest` parses as a strictly higher version than `installed`."""
    a = _parse_version_tuple(latest)
    b = _parse_version_tuple(installed)
    if a is None or b is None:
        return False
    return a > b


# ── cache ────────────────────────────────────────────────────────────


def load_cache(path: Path) -> CacheEntry | None:
    """Load a cache entry; return None if the file is missing or invalid."""
    try:
        raw = json.loads(path.read_text())
        return CacheEntry(
            checked_at=datetime.fromisoformat(raw["checked_at"]),
            latest_version=raw["latest_version"],
        )
    except (OSError, ValueError, KeyError):
        return None


def save_cache(path: Path, version: str) -> None:
    """Persist a cache entry stamped with the current time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "latest_version": version,
    }
    path.write_text(json.dumps(payload))


def is_cache_fresh(entry: CacheEntry, max_age_hours: float) -> bool:
    age = datetime.now().astimezone() - entry.checked_at
    return age.total_seconds() < max_age_hours * 3600


# ── network ──────────────────────────────────────────────────────────


def fetch_latest_version(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    """Fetch + parse pyproject.toml from the default branch. None on any error."""
    try:
        req = urllib.request.Request(
            PYPROJECT_URL, headers={"User-Agent": "rp-version-check"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    return parse_version_from_pyproject(body)


# ── orchestration ────────────────────────────────────────────────────


def check_for_updates(
    installed_version: str,
    cache_path: Path,
    fetcher: Callable[[], str | None] = fetch_latest_version,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> str | None:
    """Return a one-line update notice, or None if up-to-date / unknown.

    Never raises. Uses the cache when fresh; otherwise calls `fetcher` and
    refreshes the cache on success.
    """
    try:
        latest: str | None = None

        cached = load_cache(cache_path)
        if cached is not None and is_cache_fresh(cached, max_age_hours):
            latest = cached.latest_version
        else:
            try:
                latest = fetcher()
            except Exception:
                latest = None
            if latest is not None:
                with contextlib.suppress(OSError):
                    save_cache(cache_path, latest)

        if latest is None or not is_newer(latest, installed_version):
            return None

        return _build_notice(installed_version, latest)
    except Exception:
        return None
