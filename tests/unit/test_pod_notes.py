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
