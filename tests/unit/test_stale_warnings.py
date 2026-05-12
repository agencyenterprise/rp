"""Tests for stale-pod detection + formatting helpers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from rp.cli.utils import format_age, format_storage_cost


@pytest.mark.parametrize(
    "delta,expected_substr",
    [
        (timedelta(minutes=30), "30m"),
        (timedelta(hours=2), "2h"),
        (timedelta(hours=25), "1d"),
        (timedelta(days=5, hours=2), "5d"),
    ],
)
def test_format_age(delta, expected_substr):
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    when = now - delta
    out = format_age(when, now=now)
    assert expected_substr in out


def test_format_storage_cost_basic():
    # 400 GB at $0.10/GB/month
    assert format_storage_cost(400) == "~$40/mo"


def test_format_storage_cost_zero():
    assert format_storage_cost(0) == "~$0/mo"


def test_stale_stopped_pods_filters_by_threshold(temp_config_dir):  # noqa: ARG001
    from rp.core.pod_manager import PodManager

    pm = PodManager(api_client=MagicMock())
    now = datetime.now(UTC)
    pm.add_alias("recent", "p1")
    pm.add_alias("old", "p2")
    pm.add_alias("running", "p3")
    pm.config.pod_metadata["recent"].stopped_at = now - timedelta(hours=2)
    pm.config.pod_metadata["old"].stopped_at = now - timedelta(hours=48)
    # "running" has stopped_at=None

    stale = pm.stale_stopped_pods(threshold_hours=24, now=now)
    aliases = {alias for alias, _ in stale}
    assert aliases == {"old"}
