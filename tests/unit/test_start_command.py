"""rp pod start re-provisions managed pods with the full setup.

A stopped pod can come back on a fresh container filesystem (only /workspace
survives a RunPod stop/start), so resume must reinstall tools and recreate the
non-root user — i.e. run the full setup, not a secrets-only subset.
"""

from unittest.mock import MagicMock, patch


def _managed_pm(*, managed: bool):
    pm = MagicMock()
    pod = MagicMock()
    pod.id = "pod-1"
    pod.ip_address = "1.2.3.4"
    pod.ssh_port = 22
    pm.start_pod.return_value = pod
    pm.config.pod_metadata.get.return_value = MagicMock(managed=managed)
    return pm


def test_start_managed_pod_runs_full_setup(temp_config_dir):  # noqa: ARG001
    from rp.cli import commands

    with (
        patch.object(
            commands, "get_pod_manager", return_value=_managed_pm(managed=True)
        ),
        patch.object(commands, "get_ssh_manager"),
        patch.object(commands, "warn_secret_mismatches"),
        patch.object(commands, "_auto_clean"),
        patch("rp.core.pod_setup.PodSetup") as PodSetup,
    ):
        commands.start_command("foo")

    setup = PodSetup.return_value
    setup.run_full_setup.assert_called_once_with()


def test_start_managed_pod_no_setup_skips_setup(temp_config_dir):  # noqa: ARG001
    from rp.cli import commands

    with (
        patch.object(
            commands, "get_pod_manager", return_value=_managed_pm(managed=True)
        ),
        patch.object(commands, "get_ssh_manager"),
        patch.object(commands, "warn_secret_mismatches"),
        patch.object(commands, "_auto_clean"),
        patch("rp.core.pod_setup.PodSetup") as PodSetup,
    ):
        commands.start_command("foo", no_setup=True)

    PodSetup.assert_not_called()


def test_start_bare_pod_runs_legacy_setup_scripts(temp_config_dir):  # noqa: ARG001
    from rp.cli import commands

    with (
        patch.object(
            commands, "get_pod_manager", return_value=_managed_pm(managed=False)
        ),
        patch.object(commands, "get_ssh_manager"),
        patch.object(commands, "_auto_clean"),
        patch.object(commands, "run_setup_scripts") as run_setup_scripts,
        patch("rp.core.pod_setup.PodSetup") as PodSetup,
    ):
        commands.start_command("foo")

    run_setup_scripts.assert_called_once_with("foo")
    PodSetup.assert_not_called()
