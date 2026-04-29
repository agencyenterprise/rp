"""Tests for hierarchical .rp_settings.json resolution."""

import json

from rp.core.settings import (
    ResolvedSecret,
    RpSettings,
    _walk_to_root,
    find_nearest_settings_file,
    resolve_settings,
    save_settings,
)


class TestWalkToRoot:
    def test_walks_to_root(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        dirs = _walk_to_root(deep)
        assert dirs[0] == deep
        assert dirs[1] == deep.parent
        assert dirs[2] == deep.parent.parent
        assert dirs[-1].parent == dirs[-1]  # root

    def test_root_returns_single(self):
        from pathlib import Path

        dirs = _walk_to_root(Path("/"))
        assert len(dirs) == 1


class TestRpSettings:
    def test_defaults(self):
        s = RpSettings()
        assert s.person is None
        assert s.project is None
        assert s.secrets == []

    def test_from_dict(self):
        s = RpSettings(person="alex", project="ast", secrets=["HF_TOKEN"])
        assert s.person == "alex"
        assert s.secrets == ["HF_TOKEN"]


class TestResolvedSecret:
    def test_keychain_account(self, tmp_path):
        s = ResolvedSecret("HF_TOKEN", tmp_path)
        assert s.keychain_account() == f"{tmp_path}:HF_TOKEN"


class TestResolveSettings:
    def test_empty_when_no_files(self, tmp_path):
        resolved = resolve_settings(tmp_path)
        assert resolved.person is None
        assert resolved.project is None
        assert resolved.secrets == []
        assert resolved.sources == []

    def test_single_file(self, tmp_path):
        settings = {"person": "alex", "project": "ast", "secrets": ["HF_TOKEN"]}
        (tmp_path / ".rp_settings.json").write_text(json.dumps(settings))

        resolved = resolve_settings(tmp_path)
        assert resolved.person == "alex"
        assert resolved.project == "ast"
        assert len(resolved.secrets) == 1
        assert resolved.secrets[0].name == "HF_TOKEN"
        assert resolved.secrets[0].source_dir == tmp_path

    def test_hierarchical_scalars_closest_wins(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / ".rp_settings.json").write_text(
            json.dumps({"person": "alex", "project": "global-proj"})
        )
        (child / ".rp_settings.json").write_text(json.dumps({"project": "child-proj"}))

        resolved = resolve_settings(child)
        assert resolved.project == "child-proj"  # child wins
        assert resolved.person == "alex"  # inherited from parent

    def test_hierarchical_secrets_closest_wins(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / ".rp_settings.json").write_text(
            json.dumps({"secrets": ["HF_TOKEN", "WANDB_API_KEY"]})
        )
        (child / ".rp_settings.json").write_text(
            json.dumps({"secrets": ["HF_TOKEN", "OPENAI_API_KEY"]})
        )

        resolved = resolve_settings(child)
        names = [s.name for s in resolved.secrets]
        assert names == ["HF_TOKEN", "OPENAI_API_KEY", "WANDB_API_KEY"]

        # HF_TOKEN should come from child (closest)
        hf = next(s for s in resolved.secrets if s.name == "HF_TOKEN")
        assert hf.source_dir == child

        # WANDB_API_KEY from parent
        wandb = next(s for s in resolved.secrets if s.name == "WANDB_API_KEY")
        assert wandb.source_dir == parent

    def test_sources_closest_first(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / ".rp_settings.json").write_text(json.dumps({"person": "a"}))
        (child / ".rp_settings.json").write_text(json.dumps({"person": "b"}))

        resolved = resolve_settings(child)
        assert resolved.sources[0] == child / ".rp_settings.json"
        assert resolved.sources[1] == parent / ".rp_settings.json"

    def test_invalid_json_skipped(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text("not json {{{")
        resolved = resolve_settings(tmp_path)
        assert resolved.person is None
        assert resolved.secrets == []

    def test_template_vars(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text(
            json.dumps({"person": "alex", "project": "ast"})
        )
        resolved = resolve_settings(tmp_path)
        vars = resolved.template_vars()
        assert vars == {"person": "alex", "project": "ast"}

    def test_template_vars_partial(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text(json.dumps({"person": "alex"}))
        resolved = resolve_settings(tmp_path)
        vars = resolved.template_vars()
        assert vars == {"person": "alex"}
        assert "project" not in vars

    def test_aws_profile_single_file(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text(
            json.dumps({"aws_profile": "amaranth-mfa"})
        )
        resolved = resolve_settings(tmp_path)
        assert resolved.aws_profile == "amaranth-mfa"

    def test_aws_profile_default_none(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text(json.dumps({"person": "alex"}))
        resolved = resolve_settings(tmp_path)
        assert resolved.aws_profile is None

    def test_aws_profile_closest_wins(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / ".rp_settings.json").write_text(
            json.dumps({"aws_profile": "default"})
        )
        (child / ".rp_settings.json").write_text(
            json.dumps({"aws_profile": "amaranth-mfa"})
        )

        resolved = resolve_settings(child)
        assert resolved.aws_profile == "amaranth-mfa"

    def test_aws_profile_inherited_from_parent(self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        (parent / ".rp_settings.json").write_text(
            json.dumps({"aws_profile": "amaranth-mfa"})
        )
        (child / ".rp_settings.json").write_text(json.dumps({"project": "ast"}))

        resolved = resolve_settings(child)
        assert resolved.aws_profile == "amaranth-mfa"


class TestFindNearestSettingsFile:
    def test_finds_in_current(self, tmp_path):
        (tmp_path / ".rp_settings.json").write_text("{}")
        result = find_nearest_settings_file(tmp_path)
        assert result == tmp_path / ".rp_settings.json"

    def test_finds_in_parent(self, tmp_path):
        child = tmp_path / "child"
        child.mkdir()
        (tmp_path / ".rp_settings.json").write_text("{}")
        result = find_nearest_settings_file(child)
        assert result == tmp_path / ".rp_settings.json"

    def test_returns_none_when_not_found(self, tmp_path):
        # tmp_path has no .rp_settings.json ancestors (until /)
        # but we can't guarantee / doesn't have one, so just test the walk logic
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = find_nearest_settings_file(deep)
        assert result is None


class TestSaveSettings:
    def test_creates_file(self, tmp_path):
        settings = RpSettings(person="alex", secrets=["HF_TOKEN"])
        path = save_settings(tmp_path, settings)
        assert path == tmp_path / ".rp_settings.json"

        data = json.loads(path.read_text())
        assert data["person"] == "alex"
        assert data["secrets"] == ["HF_TOKEN"]

    def test_excludes_defaults(self, tmp_path):
        settings = RpSettings(person="alex")
        path = save_settings(tmp_path, settings)
        data = json.loads(path.read_text())
        assert "secrets" not in data  # empty list excluded
        assert "project" not in data  # None excluded
