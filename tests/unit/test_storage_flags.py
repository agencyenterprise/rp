"""CLI surface tests for the storage flags.

`--storage` (persistent volume) and `--disk` (container disk) are the current
flag names. `--persistent-volume` was renamed to `--storage` and
`--container-disk` was renamed to `--disk` in earlier releases.
These tests verify the typer surface advertises the correct names, defaults
are correct, and old names produce a usage error rather than silently working.
"""

import re
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from rp.core.models import AppConfig, GPUSpec, PodCreateRequest, PodTemplate
from rp.core.pod_manager import PodManager
from rp.main import app, pod_app, secrets_app, template_app

# Sub-apps are normally registered inside `main()`; tests need to do it
# manually to invoke `rp pod create` / `rp template create` etc.
app.add_typer(pod_app, name="pod")
app.add_typer(template_app, name="template")
app.add_typer(secrets_app, name="secrets")

runner = CliRunner()

# Typer/Rich styles each `-` as its own ANSI span, so `--disk` isn't a
# contiguous substring of styled help output. Strip codes before asserting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _help(args: list[str]) -> str:
    result = runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0, result.output
    return _ANSI_RE.sub("", result.output)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TestStorageFlagName:
    """The flag for persistent volume is --storage (renamed from --persistent-volume)."""

    def test_up_help_uses_storage(self):
        out = _help(["up"])
        assert "--storage" in out
        assert "--persistent-volume" not in out

    def test_pod_create_help_uses_storage(self):
        out = _help(["pod", "create"])
        assert "--storage" in out
        assert "--persistent-volume" not in out

    def test_template_create_help_uses_storage(self):
        out = _help(["template", "create"])
        assert "--storage" in out
        assert "--persistent-volume" not in out


class TestUpHelp:
    def test_advertises_flags(self):
        out = _help(["up"])
        assert "--disk" in out
        assert "--storage" in out
        assert "--network-volume" in out

    def test_old_flag_absent_from_help(self):
        out = _help(["up"])
        assert "--persistent-volume" not in out


class TestPodCreateHelp:
    def test_advertises_flags(self):
        out = _help(["pod", "create"])
        assert "--disk" in out
        assert "--storage" in out
        assert "--network-volume" in out

    def test_old_flags_absent_from_help(self):
        out = _help(["pod", "create"])
        assert "--persistent-volume" not in out
        assert "--container-disk" not in out


class TestTemplateCreateHelp:
    def test_advertises_flags(self):
        out = _help(["template", "create"])
        assert "--disk" in out
        assert "--storage" in out

    def test_old_flags_absent_from_help(self):
        out = _help(["template", "create"])
        assert "--persistent-volume" not in out
        assert "--container-disk" not in out

    def test_storage_no_longer_required(self):
        """--storage defaults to 0GB and is optional on `rp template create` —
        verified by the absence of an error when only --gpu/--alias-pattern
        are provided. We invoke with a fake identifier and rely on a
        downstream failure (no API key, etc.) — but typer itself must
        accept the missing flag rather than reject as a usage error."""
        result = runner.invoke(
            app,
            [
                "template",
                "create",
                "test-tmpl-from-test",
                "--alias-pattern",
                "{i}",
                "--gpu",
                "h100",
            ],
        )
        # Typer-level usage errors exit with code 2 and emit "Missing option".
        # Anything else (including downstream failures we don't care about)
        # means typer accepted the invocation.
        if result.exit_code == 2:
            assert "missing option" not in (result.output or "").lower()


class TestRejectsOldFlags:
    @pytest.mark.parametrize(
        "argv",
        [
            ["up", "--persistent-volume", "100GB"],
            ["pod", "create", "--persistent-volume", "100GB"],
            ["pod", "create", "--container-disk", "100GB"],
            [
                "template",
                "create",
                "x",
                "--alias-pattern",
                "{i}",
                "--gpu",
                "h100",
                "--persistent-volume",
                "100GB",
            ],
            [
                "template",
                "create",
                "x",
                "--alias-pattern",
                "{i}",
                "--gpu",
                "h100",
                "--container-disk",
                "100GB",
            ],
        ],
    )
    def test_old_flags_produce_usage_error(self, argv: list[str]):
        result = runner.invoke(app, argv)
        # Typer exits 2 with "No such option" when an unknown flag is given.
        assert result.exit_code == 2
        assert "no such option" in (result.output or "").lower()


class TestCreatePodFromTemplateOverrides:
    """`rp up --disk` / `--storage` need to override template values
    via create_pod_from_template's new override params."""

    def _template(self) -> PodTemplate:
        return PodTemplate(
            identifier="t",
            alias_template="x_{i}",
            gpu_spec="h100",
            storage_spec="0GB",
            container_disk_spec="500GB",
        )

    def _build(self, monkeypatch):
        api = MagicMock()
        api.find_gpu_type_ids.return_value = ["NVIDIA H100"]
        pod_data = {
            "id": "pod-1",
            "name": "x_99",
            "desiredStatus": "RUNNING",
            "imageName": "img",
            "machine": {"podHostId": "host"},
            "runtime": {"ports": []},
        }
        api.create_pod.return_value = pod_data
        api.wait_for_pod_ready.return_value = pod_data
        pm = PodManager(api_client=api)
        # Inject a pristine AppConfig with just our template, bypassing
        # the user's real ~/.config/rp/pods.json on disk.
        cfg = AppConfig()
        cfg.pod_templates["t"] = self._template()
        pm._config = cfg
        monkeypatch.setattr(pm, "_save_config", lambda: None)
        # _locked_config writes to disk; stub it to a no-op context manager
        # that just yields the in-memory config.
        from contextlib import contextmanager

        @contextmanager
        def fake_locked():
            yield pm._config

        monkeypatch.setattr(pm, "_locked_config", fake_locked)
        return pm, api

    def test_disk_override_takes_precedence_over_template(self, monkeypatch):
        pm, api = self._build(monkeypatch)
        pm.create_pod_from_template(
            "t",
            alias_override="x_99",
            container_disk_gb_override=250,
        )
        kwargs = api.create_pod.call_args.kwargs
        assert kwargs["container_disk_in_gb"] == 250

    def test_storage_override_takes_precedence_over_template(self, monkeypatch):
        pm, api = self._build(monkeypatch)
        pm.create_pod_from_template(
            "t",
            alias_override="x_99",
            volume_gb_override=300,
        )
        kwargs = api.create_pod.call_args.kwargs
        assert kwargs["volume_in_gb"] == 300

    def test_no_override_uses_template_values(self, monkeypatch):
        pm, api = self._build(monkeypatch)
        pm.create_pod_from_template("t", alias_override="x_99")
        kwargs = api.create_pod.call_args.kwargs
        assert kwargs["container_disk_in_gb"] == 500
        assert kwargs["volume_in_gb"] == 0


class TestPodCreateRequestDefaults:
    """The model still supports container_disk_gb=20 as its baked-in default.
    The CLI now passes 500 explicitly (the new shipped default), but the
    model field default is unchanged — so model-level callers (tests, API)
    still see 20 unless overridden."""

    def test_request_default_unchanged(self):
        req = PodCreateRequest(
            alias="a",
            gpu_spec=GPUSpec(count=1, model="H100"),
            volume_gb=0,
        )
        assert req.container_disk_gb == 20


class TestStorageDefaults:
    """The default per-pod persistent volume is 400GB and the container disk is 50GB."""

    def test_default_templates_use_400gb_storage_and_50gb_disk(self):
        from rp.core.default_templates import get_default_templates

        for ident, tpl in get_default_templates().items():
            assert tpl.storage_spec == "400GB", (
                f"{ident}: expected storage_spec=400GB, got {tpl.storage_spec}"
            )
            assert tpl.container_disk_spec == "50GB", (
                f"{ident}: expected container_disk_spec=50GB, got {tpl.container_disk_spec}"
            )
