"""Tests for PodMetadata serialization with the new layer-1/3/4 fields."""

from datetime import UTC, datetime

from rp.core.models import AppConfig, PodMetadata


def test_old_pod_metadata_json_deserializes_with_defaults():
    """Pre-existing pods.json rows (no new fields) must keep loading."""
    old_payload = {
        "pod_metadata": {"ast_alex_1": {"pod_id": "abc123", "managed": True}}
    }
    config = AppConfig.model_validate(old_payload)
    meta = config.pod_metadata["ast_alex_1"]
    assert meta.pod_id == "abc123"
    assert meta.managed is True
    assert meta.owner_session_id is None
    assert meta.stopped_at is None
    assert meta.note is None


def test_new_fields_round_trip():
    when = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    meta = PodMetadata(
        pod_id="abc",
        managed=True,
        owner_session_id="session-uuid",
        stopped_at=when,
        note="AE-1234: classifier eval",
    )
    blob = meta.model_dump_json()
    restored = PodMetadata.model_validate_json(blob)
    assert restored.owner_session_id == "session-uuid"
    assert restored.stopped_at == when
    assert restored.note == "AE-1234: classifier eval"


def test_defaults_for_new_fields():
    meta = PodMetadata(pod_id="abc")
    assert meta.owner_session_id is None
    assert meta.stopped_at is None
    assert meta.note is None
