"""Tests for pod_setup helpers."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from rp.core.pod_setup import (
    _APT_WAIT_PREAMBLE,
    PodSetup,
    _get_aws_credentials,
    _is_transient_apt_failure,
)


class TestGetAwsCredentials:
    def test_no_profile_uses_inherited_env(self):
        """Without an explicit profile, AWS_PROFILE is whatever the parent
        shell has set (or unset). We verify the subprocess env mirrors
        os.environ rather than carrying an injected AWS_PROFILE."""
        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="AWS_ACCESS_KEY_ID=AKIA\nAWS_SECRET_ACCESS_KEY=secret\n",
            stderr="",
        )
        with (
            patch("rp.core.pod_setup.subprocess.run", return_value=fake) as run,
            patch.dict("os.environ", {"AWS_PROFILE": "shell-default"}, clear=False),
        ):
            creds = _get_aws_credentials()
        assert creds == {
            "AWS_ACCESS_KEY_ID": "AKIA",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        assert run.call_args.kwargs["env"]["AWS_PROFILE"] == "shell-default"

    def test_profile_overrides_aws_profile_env(self):
        """An explicit profile sets AWS_PROFILE in the subprocess env so
        the export-credentials call resolves to that named profile, even
        if the user's shell has a different default selected."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="AWS_ACCESS_KEY_ID=AKIA\n", stderr=""
        )
        with (
            patch("rp.core.pod_setup.subprocess.run", return_value=fake) as run,
            patch.dict("os.environ", {"AWS_PROFILE": "shell-default"}, clear=False),
        ):
            creds = _get_aws_credentials(profile="amaranth-mfa")
        assert creds == {"AWS_ACCESS_KEY_ID": "AKIA"}
        assert run.call_args.kwargs["env"]["AWS_PROFILE"] == "amaranth-mfa"

    def test_returns_empty_on_aws_error(self):
        """A failed `aws` call must not leak as a hard error during
        secret injection — we tolerate missing/broken AWS setup."""
        with patch(
            "rp.core.pod_setup.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "aws"),
        ):
            assert _get_aws_credentials() == {}

    def test_returns_empty_when_aws_missing(self):
        with patch("rp.core.pod_setup.subprocess.run", side_effect=FileNotFoundError):
            assert _get_aws_credentials(profile="any") == {}


class TestIsTransientAptFailure:
    """Heuristic for retrying install_tools on dpkg-lock contention."""

    def test_exit_100_is_transient(self):
        err = subprocess.CalledProcessError(100, "ssh", "", "")
        assert _is_transient_apt_failure(err) is True

    def test_lock_message_in_stderr_is_transient(self):
        err = subprocess.CalledProcessError(
            1, "ssh", "", "E: Could not get lock /var/lib/dpkg/lock-frontend"
        )
        assert _is_transient_apt_failure(err) is True

    def test_unattended_upgrades_marker_is_transient(self):
        err = subprocess.CalledProcessError(1, "ssh", "unattended-upgr is running", "")
        assert _is_transient_apt_failure(err) is True

    def test_unrelated_failure_is_not_transient(self):
        err = subprocess.CalledProcessError(
            2, "ssh", "", "package foo has unmet dependencies"
        )
        assert _is_transient_apt_failure(err) is False


class TestInstallToolsRetry:
    """install_tools retries transient apt failures, fails fast on others."""

    def _make_setup(self):
        # Skip __init__'s SecretManager() side effects by constructing manually.
        setup = PodSetup.__new__(PodSetup)
        setup.ssh_alias = "test-alias"
        setup.pod_id = "pod-xyz"
        setup.console = MagicMock()
        return setup

    def test_succeeds_on_first_try(self):
        setup = self._make_setup()
        with (
            patch.object(setup, "_ssh_run_script") as run,
            patch("rp.core.pod_setup.time.sleep") as sleep,
        ):
            setup.install_tools()
        assert run.call_count == 1
        sleep.assert_not_called()

    def test_retries_transient_then_succeeds(self):
        setup = self._make_setup()
        transient = subprocess.CalledProcessError(100, "ssh", "", "lock")
        with (
            patch.object(
                setup, "_ssh_run_script", side_effect=[transient, None]
            ) as run,
            patch("rp.core.pod_setup.time.sleep") as sleep,
        ):
            setup.install_tools()
        assert run.call_count == 2
        sleep.assert_called_once_with(30)

    def test_does_not_retry_non_transient(self):
        setup = self._make_setup()
        non_transient = subprocess.CalledProcessError(2, "ssh", "", "syntax error")
        with (
            patch.object(setup, "_ssh_run_script", side_effect=non_transient) as run,
            patch("rp.core.pod_setup.time.sleep") as sleep,
            pytest.raises(subprocess.CalledProcessError),
        ):
            setup.install_tools()
        assert run.call_count == 1
        sleep.assert_not_called()

    def test_gives_up_after_three_attempts(self):
        setup = self._make_setup()
        transient = subprocess.CalledProcessError(100, "ssh", "", "lock")
        with (
            patch.object(setup, "_ssh_run_script", side_effect=[transient] * 3) as run,
            patch("rp.core.pod_setup.time.sleep"),
            pytest.raises(subprocess.CalledProcessError),
        ):
            setup.install_tools()
        assert run.call_count == 3


def test_apt_wait_preamble_is_prepended_to_install_script():
    """Sanity check: the install script starts with the lock-wait function so
    we can never accidentally skip it by reordering."""
    from rp.core.pod_setup import _TOOL_INSTALL_SCRIPT

    assert _TOOL_INSTALL_SCRIPT.startswith(_APT_WAIT_PREAMBLE)
    assert "_apt_wait" in _APT_WAIT_PREAMBLE
