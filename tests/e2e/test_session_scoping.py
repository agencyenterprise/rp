"""End-to-end session-scoping behavior using RP_SESSION_ID overrides."""

import uuid

from .test_pod_lifecycle import _create_pod_with_fallback


class TestSessionScoping:
    def test_list_filters_by_rp_session_id(self, cli_runner, test_pod_manager):  # noqa: ARG002
        alias_a = f"test-scope-a-{uuid.uuid4().hex[:8]}"
        alias_b = f"test-scope-b-{uuid.uuid4().hex[:8]}"

        try:
            # Create one pod under each "session"
            for alias, sid in [(alias_a, "session-a"), (alias_b, "session-b")]:
                r = _create_pod_with_fallback(
                    cli_runner, alias, env={"RP_SESSION_ID": sid}
                )
                assert r.returncode == 0, f"create failed for {alias}: {r.stderr}"

            list_a = cli_runner(["pod", "list"], env={"RP_SESSION_ID": "session-a"})
            list_b = cli_runner(["pod", "list"], env={"RP_SESSION_ID": "session-b"})
            list_all = cli_runner(
                ["pod", "list", "--all"], env={"RP_SESSION_ID": "session-a"}
            )

            assert alias_a in list_a.stdout
            assert alias_b not in list_a.stdout

            assert alias_b in list_b.stdout
            assert alias_a not in list_b.stdout

            assert alias_a in list_all.stdout
            assert alias_b in list_all.stdout

        finally:
            for alias in (alias_a, alias_b):
                cli_runner(["pod", "destroy", alias, "--force", "--all-sessions"])
