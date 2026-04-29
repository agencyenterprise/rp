"""Tests for pod_setup helpers."""

import subprocess
from unittest.mock import patch

from rp.core.pod_setup import _get_aws_credentials


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
