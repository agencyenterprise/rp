"""Tests for configuration utilities."""

import os

from rp.config import load_template_vars


class TestLoadTemplateVars:
    """Test loading template variables from .env and environment."""

    def test_load_from_env_file(self, tmp_path, monkeypatch):
        """Test loading variables from .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("PROJECT=ast\nPERSON=alex\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
        # Clear any RP_ env vars that might interfere
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
        monkeypatch.setenv("RP_PROJECT", "goodfire")

        vars = load_template_vars()
        assert vars["project"] == "goodfire"

    def test_comments_and_blanks_ignored(self, tmp_path, monkeypatch):
        """Test that comments and blank lines in .env are skipped."""
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n\nPROJECT=ast\n")

        monkeypatch.setattr("rp.config.ENV_FILE", env_file)
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
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert vars["person"] == "alex"

    def test_no_env_file(self, tmp_path, monkeypatch):
        """Test graceful handling when .env file doesn't exist."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        for key in list(os.environ):
            if key.startswith("RP_") and key != "RP_":
                monkeypatch.delenv(key, raising=False)

        vars = load_template_vars()
        assert vars == {}

    def test_env_var_only(self, tmp_path, monkeypatch):
        """Test loading from env vars without .env file."""
        monkeypatch.setattr("rp.config.ENV_FILE", tmp_path / "nonexistent")
        monkeypatch.setenv("RP_PROJECT", "ast")
        monkeypatch.setenv("RP_PERSON", "alex")

        vars = load_template_vars()
        assert vars["project"] == "ast"
        assert vars["person"] == "alex"
