"""Regression tests for packaging configuration.

These tests build a real wheel and inspect its contents, to catch packaging
bugs that runtime tests wouldn't notice (editable installs / source checkouts
see the source tree directly, masking missing package-data declarations).
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    out_dir = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(out_dir.glob("rp-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return zipfile.ZipFile(wheels[0])


@pytest.mark.parametrize(
    "asset",
    ["rp/assets/auto_shutdown.sh", "rp/assets/default_setup.sh"],
)
def test_asset_shipped_in_wheel(built_wheel: zipfile.ZipFile, asset: str) -> None:
    """rp/assets/*.sh must be bundled in the wheel.

    Regression test: without `[tool.setuptools.package-data]` declaring
    `"rp.assets" = ["*.sh"]`, setuptools ships only .py files. That silently
    disabled `rp up`'s auto-shutdown cron — the code warned and returned
    instead of raising, so every managed pod ran without auto-shutdown.
    """
    assert (
        asset in built_wheel.namelist()
    ), f"{asset} missing from wheel. Files present: {built_wheel.namelist()}"
