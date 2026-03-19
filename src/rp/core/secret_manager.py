"""Secret management using macOS Keychain.

This module provides a wrapper around the macOS `security` CLI for storing
and retrieving secrets in the system keychain.
"""

import builtins
import json
import subprocess

from rp.config import SECRETS_MANIFEST_FILE, ensure_config_dir_exists

KEYCHAIN_SERVICE = "rp"


class SecretManager:
    """Manage secrets in macOS Keychain."""

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self.service = service

    def get(self, name: str) -> str | None:
        """Get a secret from Keychain. Returns None if not found."""
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    name,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def set(self, name: str, value: str) -> None:
        """Store a secret in Keychain (creates or updates)."""
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                self.service,
                "-a",
                name,
                "-w",
                value,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._add_to_manifest(name)

    def remove(self, name: str) -> bool:
        """Remove a secret from Keychain. Returns True if removed."""
        try:
            subprocess.run(
                [
                    "security",
                    "delete-generic-password",
                    "-s",
                    self.service,
                    "-a",
                    name,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self._remove_from_manifest(name)
            return True
        except subprocess.CalledProcessError:
            return False

    def list_names(self) -> list[str]:
        """List all managed secret names."""
        return sorted(self._load_manifest())

    def exists(self, name: str) -> bool:
        """Check if a secret exists."""
        return self.get(name) is not None

    def _load_manifest(self) -> builtins.set[str]:
        """Load the set of managed secret names from disk."""
        try:
            data = json.loads(SECRETS_MANIFEST_FILE.read_text())
            if isinstance(data, list):
                return {str(item) for item in data}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return set()

    def _save_manifest(self, names: builtins.set[str]) -> None:
        """Save the set of managed secret names to disk."""
        ensure_config_dir_exists()
        SECRETS_MANIFEST_FILE.write_text(json.dumps(sorted(names), indent=2) + "\n")

    def _add_to_manifest(self, name: str) -> None:
        """Add a secret name to the manifest."""
        names = self._load_manifest()
        names.add(name)
        self._save_manifest(names)

    def _remove_from_manifest(self, name: str) -> None:
        """Remove a secret name from the manifest."""
        names = self._load_manifest()
        names.discard(name)
        self._save_manifest(names)
