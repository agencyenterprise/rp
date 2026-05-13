"""Tests for the session-id resolver."""

import pytest

from rp.core.session import current_session_id


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("RP_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def test_returns_none_when_no_env_vars():
    assert current_session_id() is None


def test_returns_claude_code_session_id_when_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert current_session_id() == "abc-123"


def test_rp_session_id_takes_precedence(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    monkeypatch.setenv("RP_SESSION_ID", "override-xyz")
    assert current_session_id() == "override-xyz"


def test_empty_rp_session_id_falls_through_to_claude(monkeypatch):
    """Empty string means 'opt out of scoping' — should NOT mask CLAUDE_CODE_SESSION_ID.

    Rationale: a user setting RP_SESSION_ID='' in a shell to disable scoping
    shouldn't surprise them inside Claude. Treat empty as unset.
    """
    monkeypatch.setenv("RP_SESSION_ID", "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert current_session_id() == "abc-123"


def test_empty_claude_session_id_returns_none(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    assert current_session_id() is None
