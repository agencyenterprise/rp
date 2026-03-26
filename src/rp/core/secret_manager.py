"""Secret management using macOS Keychain.

This module provides a wrapper around the macOS `security` CLI for storing
and retrieving secrets in the system keychain.

Secrets are scoped to directories via .rp_settings.json files. Each secret's
keychain account is encoded as '<dir_path>:<SECRET_NAME>' to allow the same
env var name to have different values at different directory levels.
"""

import subprocess
from pathlib import Path

from rp.core.settings import ResolvedSecret, resolve_settings

KEYCHAIN_SERVICE = "rp"


class SecretManager:
    """Manage secrets in macOS Keychain with path-scoped keys."""

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self.service = service

    def get(self, name: str, source_dir: Path | None = None) -> str | None:
        """Get a secret from Keychain.

        If source_dir is provided, uses path-scoped key. Otherwise uses
        unscoped key (e.g. for RUNPOD_API_KEY bootstrap).
        """
        account = f"{source_dir}:{name}" if source_dir is not None else name
        return self._keychain_get(account)

    def get_resolved(self, secret: ResolvedSecret) -> str | None:
        """Get a secret using its resolved path scope."""
        return self._keychain_get(secret.keychain_account())

    def set(self, name: str, value: str, source_dir: Path | None = None) -> None:
        """Store a secret in Keychain.

        If source_dir is provided, uses path-scoped key and writes to that
        directory's .rp_settings.json. Otherwise uses unscoped key (e.g. for
        RUNPOD_API_KEY bootstrap).
        """
        if source_dir is not None:
            account = f"{source_dir}:{name}"
            self._keychain_set(account, value)
            self._add_to_settings_file(name, source_dir)
        else:
            self._keychain_set(name, value)

    def remove(self, name: str, source_dir: Path | None = None) -> bool:
        """Remove a secret from Keychain.

        If source_dir is provided, removes the path-scoped key and entry from
        that directory's .rp_settings.json. Otherwise removes the unscoped key.
        """
        if source_dir is not None:
            account = f"{source_dir}:{name}"
            success = self._keychain_delete(account)
            if success:
                self._remove_from_settings_file(name, source_dir)
            return success
        else:
            return self._keychain_delete(name)

    def list_resolved(self, start: Path | None = None) -> list[ResolvedSecret]:
        """List secrets resolved from the directory hierarchy."""
        resolved = resolve_settings(start)
        return resolved.secrets

    def exists(self, name: str, source_dir: Path | None = None) -> bool:
        """Check if a secret exists in Keychain."""
        return self.get(name, source_dir) is not None

    def check_mismatches(self, start: Path | None = None) -> list[ResolvedSecret]:
        """Check for secrets listed in settings but missing from keychain.

        Returns:
            Secrets listed in .rp_settings.json but not found in keychain.
        """
        resolved = resolve_settings(start)

        missing_from_keychain = [
            s for s in resolved.secrets if self.get_resolved(s) is None
        ]

        return missing_from_keychain

    # --- Keychain operations ---

    def _keychain_get(self, account: str) -> str | None:
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    account,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def _keychain_set(self, account: str, value: str) -> None:
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                self.service,
                "-a",
                account,
                "-w",
                value,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _keychain_delete(self, account: str) -> bool:
        try:
            subprocess.run(
                [
                    "security",
                    "delete-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    account,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    # --- Settings file operations ---

    def _add_to_settings_file(self, name: str, directory: Path) -> None:
        """Add a secret name to the .rp_settings.json in the given directory."""
        from rp.core.settings import (
            RpSettings,
            _load_settings_file,
            save_settings,
        )

        settings = _load_settings_file(directory)
        if settings is None:
            settings = RpSettings(secrets=[name])
        elif name not in settings.secrets:
            settings.secrets.append(name)
        else:
            return  # Already present
        save_settings(directory, settings)

    def _remove_from_settings_file(self, name: str, directory: Path) -> None:
        """Remove a secret name from the .rp_settings.json in the given directory."""
        from rp.core.settings import _load_settings_file, save_settings

        settings = _load_settings_file(directory)
        if settings is None or name not in settings.secrets:
            return
        settings.secrets.remove(name)
        save_settings(directory, settings)
