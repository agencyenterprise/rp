"""
Test configuration and fixtures for the RunPod CLI wrapper.

This module provides pytest fixtures for both E2E and unit testing.
E2E tests use real RunPod API calls with cost controls and user confirmation.
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import runpod

from rp.config import CONFIG_DIR, ensure_config_dir_exists


@pytest.fixture(scope="session")
def setup_runpod_api():
    """Set up RunPod API key for testing."""
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        api_key_file = Path.home() / ".config" / "rp" / "runpod_api_key"
        if api_key_file.exists():
            api_key = api_key_file.read_text().strip()

    if not api_key:
        msg = "RUNPOD_API_KEY not found. Set env var or store in ~/.config/rp/runpod_api_key"
        pytest.skip(msg)  # ty: ignore[too-many-positional-arguments]

    runpod.api_key = api_key
    return api_key


@pytest.fixture
def temp_config_dir():
    """Create a temporary config directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        original_config_dir = CONFIG_DIR
        # Patch the config module to use temp directory
        from rp import config
        from rp.cli import utils as cli_utils

        config.CONFIG_DIR = Path(temp_dir) / "rp"
        config.POD_CONFIG_FILE = config.CONFIG_DIR / "pods.json"
        config.API_KEY_FILE = config.CONFIG_DIR / "runpod_api_key"
        config.SETUP_FILE = config.CONFIG_DIR / "setup.sh"
        # Modules that did `from rp.config import SETUP_FILE` bound the
        # original path at import time; redirect their references too.
        cli_utils.SETUP_FILE = config.SETUP_FILE
        cli_utils.API_KEY_FILE = config.API_KEY_FILE

        ensure_config_dir_exists()

        # Create empty setup script to avoid errors
        config.SETUP_FILE.write_text(
            "#!/bin/bash\n# Test setup script\necho 'Setup complete'"
        )

        yield config.CONFIG_DIR

        # Restore original config
        config.CONFIG_DIR = original_config_dir
        config.POD_CONFIG_FILE = original_config_dir / "pods.json"
        config.API_KEY_FILE = original_config_dir / "runpod_api_key"
        config.SETUP_FILE = original_config_dir / "setup.sh"
        cli_utils.SETUP_FILE = config.SETUP_FILE
        cli_utils.API_KEY_FILE = config.API_KEY_FILE


@pytest.fixture(scope="session")
def cli_runner():
    """Provide a CLI test runner.

    Session-scoped because the returned callable holds no per-test state,
    and module-scoped fixtures (e.g. `managed_pod`) need to depend on it.
    """

    def run_command(
        cmd_args: list[str],
        input_text: str | None = None,
        env: dict | None = None,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """Run the rp command with given arguments."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        result = subprocess.run(
            ["uv", "run", "rp", *cmd_args],
            check=False,
            input=input_text,
            text=True,
            capture_output=True,
            env=full_env,
            timeout=timeout,
        )
        return result

    return run_command


@pytest.fixture(scope="session")
def confirm_e2e_tests():
    """Ask user to confirm E2E tests that will incur costs."""
    # For now, assume user has already confirmed by running the tests
    # In a real scenario, we'd integrate with pytest-custom-confirm or similar
    return True


class TestPodManager:
    """Helper class for managing test pods with automatic cleanup."""

    def __init__(self):
        self.created_pods: list[str] = []
        self.test_aliases: list[str] = []

    # Substrings of displayName for cheap/usually-available GPUs, in order
    # of preference. Whatever's listed by the API and matches gets tried.
    CHEAP_GPU_PATTERNS: tuple[str, ...] = (
        "A4000",
        "3090",
        "A5000",
        "4090",
        "5090",
    )

    def _candidate_gpu_ids(self) -> list[str]:
        try:
            gpus = runpod.get_gpus()
            if isinstance(gpus, dict) and "gpus" in gpus:
                gpus = gpus["gpus"]
        except Exception:
            gpus = []

        by_name = {str(g.get("displayName", "")).upper(): g.get("id") for g in gpus}
        ids: list[str] = []
        for pattern in self.CHEAP_GPU_PATTERNS:
            for name, gid in by_name.items():
                if pattern in name and gid not in ids:
                    ids.append(gid)
        # Fallback to a known-good ID if API returned nothing.
        return ids or ["NVIDIA RTX A4000"]

    def create_test_pod(self, alias: str) -> dict:
        """Create a minimal test pod and track it for cleanup.

        Tries several cheap GPU types in order — RunPod availability
        fluctuates, so a single hardcoded choice flakes too often.
        """
        last_err: Exception | None = None
        for gpu_id in self._candidate_gpu_ids():
            try:
                created = runpod.create_pod(
                    name=f"test-{alias}",
                    image_name="runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
                    gpu_type_id=gpu_id,
                    gpu_count=1,
                    volume_in_gb=10,
                    volume_mount_path="/workspace",
                    support_public_ip=True,
                    start_ssh=True,
                    ports="22/tcp",
                )
            except Exception as e:
                last_err = e
                continue

            if isinstance(created, dict) and created.get("id"):
                self.created_pods.append(created["id"])
                self.test_aliases.append(alias)
                return created
            # Some SDK responses signal unavailability by returning a dict
            # with no id / an error field. Try the next GPU.
            last_err = RuntimeError(f"create_pod returned {created!r}")

        raise RuntimeError(
            f"Failed to create test pod across {self.CHEAP_GPU_PATTERNS}: {last_err}"
        )

    def wait_for_pod_ready(self, pod_id: str, timeout: int = 300) -> dict:
        """Wait for pod to be ready with network info."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            pod_details = runpod.get_pod(pod_id)
            if pod_details and pod_details.get("runtime") is not None:
                return pod_details
            time.sleep(10)

        raise TimeoutError(f"Pod {pod_id} not ready after {timeout}s")

    def cleanup_all(self):
        """Clean up all test pods."""
        for pod_id in self.created_pods[:]:
            try:
                # Stop first, then terminate
                runpod.stop_pod(pod_id)
                time.sleep(5)  # Brief wait before terminating
                runpod.terminate_pod(pod_id)
                self.created_pods.remove(pod_id)
            except Exception as e:
                print(f"Warning: Failed to cleanup pod {pod_id}: {e}")


@pytest.fixture(scope="module")
def test_pod_manager(setup_runpod_api):  # noqa: ARG001
    """Provide a test pod manager with automatic cleanup."""
    manager = TestPodManager()
    yield manager
    # Cleanup after test
    manager.cleanup_all()


@pytest.fixture(autouse=True)
def ensure_test_isolation(temp_config_dir):
    """Ensure each test runs in isolation with clean config."""
    pass  # temp_config_dir fixture already provides isolation
