"""Secret management using macOS Keychain.

This module provides a wrapper around the macOS `security` CLI for storing
and retrieving secrets in the system keychain.

Secrets are scoped to directories via .rp_settings.json files. Each secret's
keychain account is encoded as '<dir_path>:<SECRET_NAME>' to allow the same
env var name to have different values at different directory levels.
"""

import builtins
import json
import subprocess
from pathlib import Path

from rp.config import SECRETS_MANIFEST_FILE, ensure_config_dir_exists
from rp.core.settings import ResolvedSecret, resolve_settings

KEYCHAIN_SERVICE = "rp"


class SecretManager:
    """Manage secrets in macOS Keychain with path-scoped keys."""

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self.service = service

    def get(self, name: str, source_dir: Path | None = None) -> str | None:
        """Get a secret from Keychain.

        If source_dir is provided, uses path-scoped key. Otherwise falls back
        to legacy (unscoped) key for backward compatibility.
        """
        account = f"{source_dir}:{name}" if source_dir is not None else name
        return self._keychain_get(account)

    def get_resolved(self, secret: ResolvedSecret) -> str | None:
        """Get a secret using its resolved path scope."""
        value = self._keychain_get(secret.keychain_account())
        if value is not None:
            return value
        # Fall back to legacy unscoped key
        return self._keychain_get(secret.name)

    def set(self, name: str, value: str, source_dir: Path | None = None) -> None:
        """Store a secret in Keychain.

        If source_dir is provided, uses path-scoped key and writes to that
        directory's .rp_settings.json. Otherwise uses legacy behavior.
        """
        if source_dir is not None:
            account = f"{source_dir}:{name}"
            self._keychain_set(account, value)
            self._add_to_settings_file(name, source_dir)
        else:
            # Legacy behavior: unscoped keychain key + central manifest
            self._keychain_set(name, value)
            self._add_to_manifest(name)

    def remove(self, name: str, source_dir: Path | None = None) -> bool:
        """Remove a secret from Keychain.

        If source_dir is provided, removes the path-scoped key and entry from
        that directory's .rp_settings.json. Otherwise uses legacy behavior.
        """
        if source_dir is not None:
            account = f"{source_dir}:{name}"
            success = self._keychain_delete(account)
            if success:
                self._remove_from_settings_file(name, source_dir)
            return success
        else:
            success = self._keychain_delete(name)
            if success:
                self._remove_from_manifest(name)
            return success

    def list_names(self) -> list[str]:
        """List all managed secret names from legacy central manifest."""
        return sorted(self._load_manifest())

    def list_resolved(self, start: Path | None = None) -> list[ResolvedSecret]:
        """List secrets resolved from the directory hierarchy."""
        resolved = resolve_settings(start)
        return resolved.secrets

    def exists(self, name: str, source_dir: Path | None = None) -> bool:
        """Check if a secret exists in Keychain."""
        return self.get(name, source_dir) is not None

    def check_mismatches(
        self, start: Path | None = None
    ) -> tuple[list[ResolvedSecret], list[str]]:
        """Check for mismatches between settings files and keychain.

        Returns:
            missing_from_keychain: secrets listed in settings but not in keychain
            orphaned_legacy: secret names in legacy manifest but not in any settings file
        """
        resolved = resolve_settings(start)
        resolved_names = {s.name for s in resolved.secrets}

        missing_from_keychain = [
            s for s in resolved.secrets if self.get_resolved(s) is None
        ]

        legacy_names = self._load_manifest()
        orphaned_legacy = sorted(legacy_names - resolved_names)

        return missing_from_keychain, orphaned_legacy

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

    # --- Settings file operations (new hierarchical approach) ---

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

    # --- Legacy manifest operations (backward compatibility) ---

    def _load_manifest(self) -> builtins.set[str]:
        """Load the set of managed secret names from the central manifest."""
        try:
            data = json.loads(SECRETS_MANIFEST_FILE.read_text())
            if isinstance(data, list):
                return {str(item) for item in data}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return set()

    def _save_manifest(self, names: builtins.set[str]) -> None:
        ensure_config_dir_exists()
        SECRETS_MANIFEST_FILE.write_text(json.dumps(sorted(names), indent=2) + "\n")

    def _add_to_manifest(self, name: str) -> None:
        names = self._load_manifest()
        names.add(name)
        self._save_manifest(names)

    def _remove_from_manifest(self, name: str) -> None:
        names = self._load_manifest()
        names.discard(name)
        self._save_manifest(names)
