"""Unit tests for the built-in pod templates."""

import pytest

from rp.cli.utils import parse_gpu_spec, parse_storage_spec
from rp.core.default_templates import (
    DEFAULT_CONTAINER_DISK,
    DEFAULT_IMAGE,
    get_default_templates,
    is_default_template,
)
from rp.core.models import PodTemplate

EXPECTED_GPU_GRID = {
    "h100": (1, "H100"),
    "2h100": (2, "H100"),
    "4h100": (4, "H100"),
    "8h100": (8, "H100"),
    "h200": (1, "H200"),
    "2h200": (2, "H200"),
    "4h200": (4, "H200"),
    "8h200": (8, "H200"),
    "b200": (1, "B200"),
    "2b200": (2, "B200"),
    "4b200": (4, "B200"),
    "8b200": (8, "B200"),
}


class TestGPUGrid:
    """The {1,2,4,8} x {H100, H200, B200} matrix should be available as built-ins."""

    @pytest.mark.parametrize("identifier", EXPECTED_GPU_GRID.keys())
    def test_template_exists(self, identifier: str):
        templates = get_default_templates()
        assert identifier in templates
        assert isinstance(templates[identifier], PodTemplate)

    @pytest.mark.parametrize("identifier,expected", list(EXPECTED_GPU_GRID.items()))
    def test_gpu_spec_parses_to_expected_count_and_model(
        self, identifier: str, expected: tuple[int, str]
    ):
        template = get_default_templates()[identifier]
        spec = parse_gpu_spec(template.gpu_spec)
        assert (spec.count, spec.model) == expected

    @pytest.mark.parametrize("identifier", EXPECTED_GPU_GRID.keys())
    def test_identifier_matches_gpu_spec(self, identifier: str):
        """Identifier convention: '<count><model>' for >1, bare '<model>' for 1."""
        template = get_default_templates()[identifier]
        spec = parse_gpu_spec(template.gpu_spec)
        if spec.count == 1:
            assert identifier == spec.model.lower()
        else:
            assert identifier == f"{spec.count}{spec.model.lower()}"


class TestDefaultsApplied:
    """All built-in templates share the same shipped defaults."""

    @pytest.fixture(scope="class")
    def templates(self) -> dict[str, PodTemplate]:
        return get_default_templates()

    def test_all_use_default_image(self, templates: dict[str, PodTemplate]):
        for ident, t in templates.items():
            assert t.image == DEFAULT_IMAGE, ident

    def test_all_use_zero_volume(self, templates: dict[str, PodTemplate]):
        for ident, t in templates.items():
            assert parse_storage_spec(t.storage_spec) == 0, ident

    def test_all_use_default_container_disk(self, templates: dict[str, PodTemplate]):
        expected = parse_storage_spec(DEFAULT_CONTAINER_DISK)
        for ident, t in templates.items():
            assert t.container_disk_spec is not None, ident
            assert parse_storage_spec(t.container_disk_spec) == expected, ident

    def test_all_use_project_person_alias_pattern(
        self, templates: dict[str, PodTemplate]
    ):
        for ident, t in templates.items():
            assert t.alias_template == "{project}_{person}_{i}", ident

    def test_no_network_volume_attached(self, templates: dict[str, PodTemplate]):
        for ident, t in templates.items():
            assert t.network_volume_id is None, ident

    def test_identifier_field_matches_dict_key(self, templates: dict[str, PodTemplate]):
        for ident, t in templates.items():
            assert t.identifier == ident


class TestIsDefaultTemplate:
    def test_recognises_grid_members(self):
        for identifier in EXPECTED_GPU_GRID:
            assert is_default_template(identifier)

    def test_recognises_other_builtins(self):
        assert is_default_template("5090")
        assert is_default_template("a40")

    def test_rejects_unknown_identifier(self):
        assert not is_default_template("definitely-not-a-template")
        assert not is_default_template("")
