"""Tests for configuration utilities."""

import os

from rp.config import load_template_vars
from rp.core.settings import ResolvedSettings


def _empty_settings(_start=None):
    """Return empty resolved settings to isolate .env/env var tests."""
    return ResolvedSettings(person=None, project=None, secrets=[], sources=[])


class TestLoadTemplateVars:
    """Test loading template variables from .env and environment."""

    def test_load_from_env_file(self, tmp_path, monkeypatch):
        """Test loading variables from .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("PROJECT=ast\nPERSON=alex\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert vars["person"] == "alex"

    def test_env_var_overrides_file(self, tmp_path, monkeypatch):
        """Test that RP_-prefixed env vars override .env file values."""
        env_file = tmp_path / ".env"
        env_file.write_text("PROJECT=ast\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        monkeypatch.setenv("RP_PROJECT", "goodfire")

        vars = load_template_vars()
        assert vars["project"] == "goodfire"

    def test_comments_and_blanks_ignored(self, tmp_path, monkeypatch):
        """Test that comments and blank lines in .env are skipped."""
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n\nPROJECT=ast\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert len(vars) == 1

    def test_quoted_values_stripped(self, tmp_path, monkeypatch):
        """Test that quotes around values are stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text("PROJECT=\"ast\"\nPERSON='alex'\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert vars["person"] == "alex"

    def test_no_env_file(self, tmp_path, monkeypatch):
        """Test graceful handling when .env file doesn't exist."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars == {}

    def test_env_var_only(self, tmp_path, monkeypatch):
        """Test loading from env vars without .env file."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        monkeypatch.setattr("rp.core.settings.resolve_settings", _empty_settings)
        monkeypatch.setenv("RP_PROJECT", "ast")
        monkeypatch.setenv("RP_PERSON", "alex")

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert vars["person"] == "alex"

    def test_settings_provides_base_values(self, tmp_path, monkeypatch):
        """Test that .rp_settings.json values are used as base."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        monkeypatch.setattr(
            "rp.core.settings.resolve_settings",
            lambda _start=None: ResolvedSettings(
                person="alex", project="ast", secrets=[], sources=[]
            ),
        )
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars["person"] == "alex"
        assert vars["project"] == "ast"

    def test_env_var_overrides_settings(self, tmp_path, monkeypatch):
        """Test that RP_ env vars override .rp_settings.json values."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        monkeypatch.setattr(
            "rp.core.settings.resolve_settings",
            lambda _start=None: ResolvedSettings(
                person="alex", project="ast", secrets=[], sources=[]
            ),
        )
        monkeypatch.setenv("RP_PROJECT", "goodfire")

        vars = load_template_vars()
        assert vars["project"] == "goodfire"  # env var wins
        assert vars["person"] == "alex"  # settings preserved
