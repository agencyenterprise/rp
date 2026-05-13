"""Tests for pods-list rendering with the new note column."""

from io import StringIO

from rich.console import Console

from rp.cli.utils import display_pods_table
from rp.core.models import Pod, PodStatus


def _capture(pods, *, show_owner_column=False):
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=False, no_color=True)
    display_pods_table(pods, console=console, show_owner_column=show_owner_column)
    return buf.getvalue()


def test_table_omits_note_column_when_no_notes():
    pods = [
        Pod(id="p1", alias="foo", status=PodStatus.RUNNING),
        Pod(id="p2", alias="bar", status=PodStatus.STOPPED),
    ]
    out = _capture(pods)
    assert "Note" not in out


def test_table_includes_note_column_when_any_pod_has_note():
    pod_with_note = Pod(id="p1", alias="foo", status=PodStatus.RUNNING)
    pod_with_note.note = "AE-1234: classifier eval"
    pod_without = Pod(id="p2", alias="bar", status=PodStatus.STOPPED)
    out = _capture([pod_with_note, pod_without])
    assert "Note" in out
    assert "AE-1234: classifier" in out
