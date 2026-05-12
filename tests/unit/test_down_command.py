"""rp down default branches stop vs destroy correctly."""

from unittest.mock import MagicMock, patch


def test_down_stops_by_default(temp_config_dir):  # noqa: ARG001
    from rp.cli import commands

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.aliases = {"foo": "pod-1"}
        pm.get_pod_id.return_value = "pod-1"
        get_pm.return_value = pm
        with patch.object(commands, "get_ssh_manager"):
            commands.down_command("foo", skip_logs=True, destroy=False)
        pm.stop_pod.assert_called_once_with("foo")
        pm.destroy_pod.assert_not_called()


def test_down_destroy_terminates(temp_config_dir):  # noqa: ARG001
    from rp.cli import commands

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.aliases = {"foo": "pod-1"}
        pm.get_pod_id.return_value = "pod-1"
        pm.destroy_pod.return_value = "pod-1"
        get_pm.return_value = pm
        with patch.object(commands, "get_ssh_manager"):
            commands.down_command("foo", skip_logs=True, destroy=True)
        pm.destroy_pod.assert_called_once_with("foo")
        pm.stop_pod.assert_not_called()
