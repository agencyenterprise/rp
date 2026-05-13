"""Verify the inline pod-setup script content; full E2E covered by tests/e2e."""

from rp.core.pod_setup import _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_xdg_cache_home():
    assert "export XDG_CACHE_HOME=/workspace/.cache" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_uv_cache_dir():
    assert "export UV_CACHE_DIR=/workspace/.cache/uv" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_pip_cache_dir():
    assert "export PIP_CACHE_DIR=/workspace/.cache/pip" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_hf_home():
    assert "export HF_HOME=/workspace/.cache/huggingface" in _TOOL_INSTALL_SCRIPT


def test_setup_script_creates_cache_dir():
    """The /workspace/.cache directory must be created before exports point at it."""
    assert "mkdir -p /workspace/.cache" in _TOOL_INSTALL_SCRIPT
