"""End-to-end tests for primary rp flows: `rp up`, `rp run`, auto-shutdown.

These exercise the paths users actually take (opinionated managed setup +
remote command execution) and cost more per run than the bare-pod suite,
so tests share one managed pod via a module-scoped fixture.

They need the runner to be able to SSH into the pod (root@<ip>). Locally
that works if your SSH key is registered with your RunPod account. In CI,
the skip guard keeps the module inert until a `CI_SSH_PRIVATE_KEY` secret
is configured and the e2e workflow installs it into the runner.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("RP_E2E_SSH_AVAILABLE") != "1",
    reason="CI runner has no SSH key for pod access (set CI_SSH_PRIVATE_KEY secret to enable)",
)

CHEAP_GPUS: tuple[str, ...] = (
    "1xRTX A4000",
    "1xRTX 3090",
    "1xRTX A5000",
    "1xRTX 4090",
    "1xRTX 5090",
)


@pytest.fixture(scope="module")
def managed_pod(cli_runner):
    """Create a managed pod via `rp up`, yield the alias, tear down with `rp down`."""
    alias = f"test-e2e-managed-{uuid.uuid4().hex[:8]}"
    last_result = None
    for gpu in CHEAP_GPUS:
        # `rp up` provisions a pod, installs tools, injects secrets, and
        # deploys auto-shutdown — comfortably over the default 5-minute
        # subprocess timeout on slower CI runners.
        result = cli_runner(
            ["up", "--gpu", gpu, "--storage", "0GB", "--alias", alias],
            timeout=600,
        )
        last_result = result
        if result.returncode == 0:
            break
        if "no longer any instances" not in result.stderr.lower() and (
            "unavailable" not in result.stderr.lower()
        ):
            raise AssertionError(f"rp up failed on {gpu}: {result.stderr}")
    else:
        raise AssertionError(
            f"rp up failed across all cheap GPUs: "
            f"{last_result.stderr if last_result else 'no result'}"
        )

    try:
        yield alias
    finally:
        # Best-effort teardown — the sweep will catch it if this fails.
        cli_runner(["down", alias, "--skip-logs"])


class TestManagedPodFlow:
    """Exercise the opinionated `rp up` path end to end."""

    def test_auto_shutdown_installed(self, cli_runner, managed_pod):
        """`rp up` must install the auto-shutdown script and cron on the pod.

        Regression guard for the packaging bug where auto_shutdown.sh was
        silently missing from the installed wheel — `rp up` reported
        success but the pod had no auto-shutdown, leaking compute.

        Checks run via `rp run --root` so auth uses the same SSH path as
        the tool itself (agent forwarding, SSH config) rather than a raw
        ssh invocation that can auth-fail in CI even when `rp run` works.
        """
        result = cli_runner(
            [
                "run",
                "--root",
                managed_pod,
                "--",
                "sh",
                "-c",
                "test -x /usr/local/bin/auto_shutdown.sh && crontab -l | grep -q auto_shutdown.sh && echo OK",
            ],
        )
        assert result.returncode == 0 and "OK" in result.stdout, (
            f"Auto-shutdown setup missing on pod. stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_rp_run_roundtrip(self, cli_runner, managed_pod):
        """`rp run <alias> -- <cmd>` should execute on the pod and return output."""
        result = cli_runner(["run", managed_pod, "--", "echo", "e2e-hello-world"])
        assert result.returncode == 0, f"rp run failed: {result.stderr}"
        assert "e2e-hello-world" in result.stdout
