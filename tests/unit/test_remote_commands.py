"""
Unit tests for remote access commands (shell).

These tests verify the shell command functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer

from rp.cli.commands import shell_command
from rp.utils.errors import AliasError


class TestShellCommand:
    """Test shell command functionality."""

    @patch("rp.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_shell_command_success(self, mock_subprocess, mock_get_pod_manager):
        """Test shell command with successful connection."""
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager
        mock_subprocess.return_value = MagicMock(returncode=0)

        shell_command("test-alias")

        mock_manager.get_pod_id.assert_called_once_with("test-alias")
        mock_subprocess.assert_called_once_with(
            ["ssh", "-A", "test-alias"], check=False
        )

    @patch("rp.cli.commands.get_pod_manager")
    @patch("subprocess.run")
    def test_shell_command_connection_closed(
        self, mock_subprocess, mock_get_pod_manager
    ):
        """Test shell command when connection is closed by user."""
        mock_manager = MagicMock()
        mock_manager.get_pod_id.return_value = "test-pod-id"
        mock_get_pod_manager.return_value = mock_manager
        mock_subprocess.return_value = MagicMock(returncode=130)  # SIGINT

        shell_command("test-alias")

        mock_subprocess.assert_called_once_with(
            ["ssh", "-A", "test-alias"], check=False
        )

    @patch("rp.cli.commands.get_pod_manager")
    def test_shell_command_invalid_alias(self, mock_get_pod_manager):
        """Test shell command with invalid alias."""
        # Setup mock pod manager to raise error
        mock_manager = MagicMock()
        mock_manager.get_pod_id.side_effect = AliasError.not_found("invalid-alias")
        mock_get_pod_manager.return_value = mock_manager

        # Run command and expect typer.Exit (handle_cli_error converts to exit)
        with pytest.raises(typer.Exit):
            shell_command("invalid-alias")
