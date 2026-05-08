"""Tests for the GitHub-backed version-update notifier."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rp.core import version_check

# ── parse_version_from_pyproject ─────────────────────────────────────


def test_parse_version_from_pyproject_extracts_version() -> None:
    toml = '[project]\nname = "rp"\nversion = "1.2.3"\n'
    assert version_check.parse_version_from_pyproject(toml) == "1.2.3"


def test_parse_version_from_pyproject_returns_none_when_missing() -> None:
    toml = '[project]\nname = "rp"\n'
    assert version_check.parse_version_from_pyproject(toml) is None


def test_parse_version_from_pyproject_returns_none_on_invalid_toml() -> None:
    assert version_check.parse_version_from_pyproject("not [valid toml") is None


# ── is_newer ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "latest,installed,expected",
    [
        ("0.12.0", "0.11.0", True),
        ("0.11.1", "0.11.0", True),
        ("1.0.0", "0.11.0", True),
        ("0.11.0", "0.11.0", False),
        ("0.11.0", "0.12.0", False),
        ("0.10.0", "0.11.0", False),
    ],
)
def test_is_newer(latest: str, installed: str, expected: bool) -> None:
    assert version_check.is_newer(latest, installed) is expected


def test_is_newer_returns_false_on_garbage() -> None:
    # Robustness: a bad version string from a malformed remote pyproject
    # should never crash the CLI.
    assert version_check.is_newer("not-a-version", "0.11.0") is False


# ── cache file ───────────────────────────────────────────────────────


def test_save_and_load_cache_roundtrip(tmp_path: Path) -> None:
    cache_path = tmp_path / "version_check.json"
    version_check.save_cache(cache_path, "0.12.5")

    entry = version_check.load_cache(cache_path)
    assert entry is not None
    assert entry.latest_version == "0.12.5"
    assert isinstance(entry.checked_at, datetime)


def test_load_cache_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert version_check.load_cache(tmp_path / "nope.json") is None


def test_load_cache_returns_none_on_corrupt_file(tmp_path: Path) -> None:
    cache_path = tmp_path / "version_check.json"
    cache_path.write_text("{not valid json")
    assert version_check.load_cache(cache_path) is None


def test_is_cache_fresh_true_for_recent() -> None:
    entry = version_check.CacheEntry(
        checked_at=datetime.now().astimezone() - timedelta(hours=1),
        latest_version="0.12.0",
    )
    assert version_check.is_cache_fresh(entry, max_age_hours=24.0) is True


def test_is_cache_fresh_false_for_old() -> None:
    entry = version_check.CacheEntry(
        checked_at=datetime.now().astimezone() - timedelta(hours=48),
        latest_version="0.12.0",
    )
    assert version_check.is_cache_fresh(entry, max_age_hours=24.0) is False


# ── check_for_updates (orchestration) ────────────────────────────────


def test_check_for_updates_returns_notice_when_newer(tmp_path: Path) -> None:
    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=tmp_path / "v.json",
        fetcher=lambda: "0.12.0",
    )
    assert notice is not None
    assert "0.12.0" in notice
    assert "0.11.0" in notice


def test_check_for_updates_returns_none_when_up_to_date(tmp_path: Path) -> None:
    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=tmp_path / "v.json",
        fetcher=lambda: "0.11.0",
    )
    assert notice is None


def test_check_for_updates_uses_cache_when_fresh(tmp_path: Path) -> None:
    cache_path = tmp_path / "v.json"
    version_check.save_cache(cache_path, "0.12.0")

    calls = {"count": 0}

    def fetcher() -> str | None:
        calls["count"] += 1
        return "0.99.0"  # Should not be reached.

    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=cache_path,
        fetcher=fetcher,
    )
    assert calls["count"] == 0
    assert notice is not None
    assert "0.12.0" in notice


def test_check_for_updates_refreshes_stale_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "v.json"
    # Write a stale cache file by hand.
    stale = {
        "checked_at": (datetime.now().astimezone() - timedelta(days=7)).isoformat(),
        "latest_version": "0.10.0",
    }
    cache_path.write_text(json.dumps(stale))

    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=cache_path,
        fetcher=lambda: "0.13.0",
    )
    assert notice is not None
    assert "0.13.0" in notice

    # Cache should be refreshed.
    refreshed = version_check.load_cache(cache_path)
    assert refreshed is not None
    assert refreshed.latest_version == "0.13.0"


def test_check_for_updates_returns_none_when_fetch_fails(tmp_path: Path) -> None:
    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=tmp_path / "v.json",
        fetcher=lambda: None,
    )
    assert notice is None


def test_check_for_updates_swallows_fetcher_exceptions(tmp_path: Path) -> None:
    def fetcher() -> str | None:
        raise RuntimeError("network is down")

    # Must not raise — a flaky network must never break the CLI.
    notice = version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=tmp_path / "v.json",
        fetcher=fetcher,
    )
    assert notice is None


def test_check_for_updates_creates_cache_dir_if_missing(tmp_path: Path) -> None:
    cache_path = tmp_path / "nested" / "dir" / "v.json"
    version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=cache_path,
        fetcher=lambda: "0.12.0",
    )
    assert cache_path.exists()


# ── fetch_latest_version (network — mocked) ──────────────────────────


def test_fetch_latest_version_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = b'[project]\nname = "rp"\nversion = "1.4.2"\n'

    class FakeResponse:
        def read(self) -> bytes:
            return sample

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    captured: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: float) -> FakeResponse:
        captured["timeout"] = timeout
        captured["url"] = getattr(req, "full_url", req)
        return FakeResponse()

    monkeypatch.setattr(version_check.urllib.request, "urlopen", fake_urlopen)

    assert version_check.fetch_latest_version(timeout=1.5) == "1.4.2"
    assert captured["timeout"] == 1.5
    assert "raw.githubusercontent.com" in str(captured["url"])
    assert "agencyenterprise/rp" in str(captured["url"])
    assert "pyproject.toml" in str(captured["url"])


def test_fetch_latest_version_returns_none_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(version_check.urllib.request, "urlopen", boom)
    assert version_check.fetch_latest_version() is None


# ── never blocks for too long ────────────────────────────────────────


def test_check_for_updates_uses_short_default_timeout(tmp_path: Path) -> None:
    """A slow fetcher must not block the CLI for noticeably long.

    We don't actually call the network here; the fetcher is injected,
    but this guards the design contract: check_for_updates is a thin
    wrapper that should return promptly when the fetcher does.
    """
    start = time.monotonic()
    version_check.check_for_updates(
        installed_version="0.11.0",
        cache_path=tmp_path / "v.json",
        fetcher=lambda: "0.12.0",
    )
    assert time.monotonic() - start < 1.0


# ── CLI wiring helper ────────────────────────────────────────────────


def test_maybe_print_update_notice_writes_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from rp import main as rp_main

    monkeypatch.delenv("RP_NO_VERSION_CHECK", raising=False)
    monkeypatch.setattr(
        rp_main,
        "_check_for_updates_safe",
        lambda: "A new version of rp is available: 0.11.0 → 0.12.0.",
    )

    rp_main._maybe_print_update_notice()

    captured = capsys.readouterr()
    assert "0.12.0" in captured.err
    # Notice must not pollute stdout — pipelines often capture stdout.
    assert captured.out == ""


def test_maybe_print_update_notice_skips_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from rp import main as rp_main

    monkeypatch.setenv("RP_NO_VERSION_CHECK", "1")

    def boom() -> str | None:
        raise AssertionError("must not be called when env var set")

    monkeypatch.setattr(rp_main, "_check_for_updates_safe", boom)
    rp_main._maybe_print_update_notice()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_maybe_print_update_notice_skips_during_shell_completion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Shell completion (typer) sets _RP_COMPLETE; printing breaks it."""
    from rp import main as rp_main

    monkeypatch.delenv("RP_NO_VERSION_CHECK", raising=False)
    monkeypatch.setenv("_RP_COMPLETE", "complete_zsh")
    monkeypatch.setattr(
        rp_main,
        "_check_for_updates_safe",
        lambda: "A new version is available",
    )

    rp_main._maybe_print_update_notice()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_maybe_print_update_notice_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from rp import main as rp_main

    monkeypatch.delenv("RP_NO_VERSION_CHECK", raising=False)

    def boom() -> str | None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rp_main, "_check_for_updates_safe", boom)

    # Must not raise — the notice path is best-effort.
    rp_main._maybe_print_update_notice()

    captured = capsys.readouterr()
    assert captured.err == ""
