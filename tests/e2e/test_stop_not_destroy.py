"""End-to-end coverage for the rp down -> stop semantic shift."""

import time
import uuid

import runpod

from .test_pod_lifecycle import _create_pod_with_fallback


def _extract_pod_id(stdout: str) -> str:
    for line in stdout.split("\n"):
        if "Saved alias" in line and "->" in line:
            return line.split("->")[-1].strip()
    raise AssertionError(f"Could not extract pod ID from:\n{stdout}")


class TestRpDownStops:
    def test_rp_down_default_stops_and_preserves_alias(
        self, cli_runner, test_pod_manager
    ):
        alias = f"test-down-stop-{uuid.uuid4().hex[:8]}"
        create_result = _create_pod_with_fallback(cli_runner, alias)
        assert create_result.returncode == 0, create_result.stderr
        pod_id = _extract_pod_id(create_result.stdout)
        test_pod_manager.created_pods.append(pod_id)

        # Default rp down should STOP, not destroy.
        result = cli_runner(["down", alias, "--skip-logs"])
        assert result.returncode == 0, result.stderr

        # Alias should still be present locally.
        list_result = cli_runner(["pod", "list", "--all"])
        assert alias in list_result.stdout, (
            f"Expected '{alias}' in pod list after stop; got:\n{list_result.stdout}"
        )

        # RunPod-side: pod should be EXITED, not gone.
        time.sleep(10)
        details = runpod.get_pod(pod_id)
        assert details is not None, "pod was destroyed when it should have stopped"
        status = str(details.get("desiredStatus", "")).upper()
        assert status in {"EXITED", "STOPPED"}, (
            f"Pod {pod_id} desiredStatus={status} after rp down (expected EXITED/STOPPED)"
        )

    def test_rp_down_destroy_terminates(self, cli_runner, test_pod_manager):  # noqa: ARG002
        alias = f"test-down-destroy-{uuid.uuid4().hex[:8]}"
        create_result = _create_pod_with_fallback(cli_runner, alias)
        assert create_result.returncode == 0, create_result.stderr
        # Do NOT register with test_pod_manager.created_pods — --destroy
        # will remove the pod, and the fixture cleanup would error on
        # already-terminated pods.

        result = cli_runner(["down", alias, "--skip-logs", "--destroy"])
        assert result.returncode == 0, result.stderr

        # Alias should be gone locally.
        list_result = cli_runner(["pod", "list", "--all"])
        assert alias not in list_result.stdout, (
            f"Alias '{alias}' should be gone after --destroy; got:\n{list_result.stdout}"
        )
