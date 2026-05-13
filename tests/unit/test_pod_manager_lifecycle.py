"""Unit tests for stop/start side effects on PodMetadata."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from rp.core.pod_manager import PodManager


def _make_pm():
    api = MagicMock()
    api.stop_pod.return_value = None
    api.start_pod.return_value = None
    api.wait_for_pod_ready.return_value = {"id": "pod-1", "desiredStatus": "RUNNING"}
    pm = PodManager(api_client=api)
    pm.add_alias("foo", "pod-1")
    return pm


def test_stop_pod_records_stopped_at(temp_config_dir):  # noqa: ARG001
    pm = _make_pm()
    before = datetime.now(UTC) - timedelta(seconds=1)
    pm.stop_pod("foo")
    meta = pm.config.pod_metadata["foo"]
    assert meta.stopped_at is not None
    assert meta.stopped_at >= before


def test_start_pod_clears_stopped_at(temp_config_dir):  # noqa: ARG001
    pm = _make_pm()
    pm.stop_pod("foo")
    assert pm.config.pod_metadata["foo"].stopped_at is not None
    pm.start_pod("foo")
    assert pm.config.pod_metadata["foo"].stopped_at is None
