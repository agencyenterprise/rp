"""Tests for session-aware filtering in rp pod list."""

from unittest.mock import MagicMock, patch

from rp.core.models import Pod, PodStatus


def _pods(*specs):
    """specs = list of (alias, owner_session_id) tuples"""
    out = []
    for alias, owner in specs:
        p = Pod(id=f"pod-{alias}", alias=alias, status=PodStatus.RUNNING)
        p.owner_session_id = owner
        out.append(p)
    return out


def test_list_filters_to_current_session(monkeypatch, capsys):
    """When CLAUDE_CODE_SESSION_ID is set, rp pod list shows only matching pods."""
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    monkeypatch.delenv("RP_SESSION_ID", raising=False)

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("mine", "session-a"),
            ("theirs", "session-b"),
            ("legacy", None),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=False)

    out = capsys.readouterr().out
    assert "mine" in out
    assert "theirs" not in out
    assert "legacy" in out, "Unowned (None) pods should always be visible"
    assert "1 pod owned by other session" in out


def test_list_all_ignores_session_filter(monkeypatch, capsys):
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("mine", "session-a"),
            ("theirs", "session-b"),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=True)

    out = capsys.readouterr().out
    assert "mine" in out
    assert "theirs" in out


def test_list_no_session_shows_all(monkeypatch, capsys):
    from rp.cli import commands

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("RP_SESSION_ID", raising=False)

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("a", "session-a"),
            ("b", "session-b"),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=False)

    out = capsys.readouterr().out
    assert "a" in out
    assert "b" in out
