"""
Configuration utilities for the RunPod CLI wrapper.

This module provides configuration constants and utilities for the RunPod CLI
wrapper, including paths to configuration files and directories.
"""

from pathlib import Path

# --- CONFIGURATION ---
# Location to store alias→pod_id mappings
CONFIG_DIR = Path.home() / ".config" / "rp"
POD_CONFIG_FILE = CONFIG_DIR / "pods.json"
API_KEY_FILE = CONFIG_DIR / "runpod_api_key"
SETUP_FILE = CONFIG_DIR / "setup.sh"

# The full path to your SSH config file.
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

# Marker prefix for SSH config
MARKER_PREFIX = "# rp:managed"

# --- END CONFIGURATION ---


def ensure_config_dir_exists() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
