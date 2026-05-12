"""Tests for pod-note plumbing through create commands and the dedicated command."""

from rp.core.models import AppConfig


def test_app_config_records_note_on_alias():
    """add_alias accepts a note kwarg and stores it on the metadata."""
    config = AppConfig()
    config.add_alias("foo", "pod-1", note="AE-1234: classifier")
    assert config.pod_metadata["foo"].note == "AE-1234: classifier"


def test_app_config_no_note_leaves_field_none():
    config = AppConfig()
    config.add_alias("foo", "pod-1")
    assert config.pod_metadata["foo"].note is None


def test_pod_manager_note_lifecycle(temp_config_dir):  # noqa: ARG001
    """get / set / append / clear round trip via PodManager."""
    from rp.core.pod_manager import PodManager

    pm = PodManager(api_client=None)
    pm.add_alias("foo", "pod-1")
    assert pm.get_note("foo") is None

    pm.set_note("foo", "AE-1234: classifier eval")
    assert pm.get_note("foo") == "AE-1234: classifier eval"

    pm.append_note("foo", "checkpoint at /workspace/runs/v3")
    note = pm.get_note("foo")
    assert note is not None
    assert "AE-1234: classifier eval" in note
    assert "checkpoint at /workspace/runs/v3" in note

    pm.clear_note("foo")
    assert pm.get_note("foo") is None
