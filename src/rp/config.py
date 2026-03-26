"""
Configuration utilities for the RunPod CLI wrapper.

This module provides configuration constants and utilities for the RunPod CLI
wrapper, including paths to configuration files and directories.
"""

import os
from pathlib import Path

# --- CONFIGURATION ---
# Location to store alias→pod_id mappings
CONFIG_DIR = Path.home() / ".config" / "rp"
POD_CONFIG_FILE = CONFIG_DIR / "pods.json"
API_KEY_FILE = CONFIG_DIR / "runpod_api_key"
SETUP_FILE = CONFIG_DIR / "setup.sh"
ENV_FILE = CONFIG_DIR / ".env"

# The full path to your SSH config file.
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

# Marker prefix for SSH config
MARKER_PREFIX = "# rp:managed"

# --- END CONFIGURATION ---


def ensure_config_dir_exists() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_template_vars() -> dict[str, str]:
    """Load template variables from .rp_settings.json hierarchy, .env, and env vars.

    Resolution order (later overrides earlier):
    1. .rp_settings.json hierarchy (person, project from nearest settings file)
    2. ~/.config/rp/.env file (legacy, KEY=VALUE lines)
    3. RP_-prefixed environment variables (e.g. RP_PROJECT -> {project})
    """
    vars: dict[str, str] = {}

    # 1. Load from .rp_settings.json hierarchy
    from rp.core.settings import resolve_settings

    resolved = resolve_settings()
    vars.update(resolved.template_vars())

    # 2. Legacy .env file (overrides settings)
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text().splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            vars[key.lower()] = value

    # 3. Override with RP_-prefixed env vars (highest priority)
    for key, value in os.environ.items():
        if key.startswith("RP_") and key != "RP_":
            var_name = key[3:].lower()  # RP_PROJECT -> project
            vars[var_name] = value

    return vars
