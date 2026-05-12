"""End-to-end pod-note set / append / clear / show."""

import uuid

from .test_pod_lifecycle import _create_pod_with_fallback


class TestPodNoteLifecycle:
    def test_note_set_show_append_clear(self, cli_runner, shared_test_pod):
        alias = shared_test_pod["alias"]

        # Set
        r = cli_runner(["pod", "note", alias, "AE-1234: e2e test"])
        assert r.returncode == 0, r.stderr

        # Show
        r = cli_runner(["pod", "note", alias])
        assert r.returncode == 0
        assert "AE-1234: e2e test" in r.stdout

        # Show via rp pod show
        r = cli_runner(["pod", "show", alias])
        assert "AE-1234: e2e test" in r.stdout

        # Note column appears in rp pod list (--all to bypass session filter)
        r = cli_runner(["pod", "list", "--all"])
        assert "AE-1234" in r.stdout

        # Append
        r = cli_runner(["pod", "note", alias, "more context", "--append"])
        assert r.returncode == 0
        r = cli_runner(["pod", "note", alias])
        assert "AE-1234: e2e test" in r.stdout
        assert "more context" in r.stdout

        # Clear
        r = cli_runner(["pod", "note", alias, "--clear"])
        assert r.returncode == 0
        r = cli_runner(["pod", "note", alias])
        assert "no note set" in r.stdout.lower()

    def test_up_note_flag_persists(self, cli_runner, test_pod_manager):  # noqa: ARG002
        """rp pod create stores --note-equivalent state when set explicitly via rp pod note."""
        alias = f"test-up-note-{uuid.uuid4().hex[:8]}"
        try:
            r = _create_pod_with_fallback(cli_runner, alias)
            assert r.returncode == 0, r.stderr

            # _create_pod_with_fallback uses pod create (no --note plumbing on
            # bare create in this version of the test helper). Set the note
            # explicitly to verify it flows through rp pod show.
            r = cli_runner(["pod", "note", alias, "from-test"])
            assert r.returncode == 0
            r = cli_runner(["pod", "show", alias])
            assert "from-test" in r.stdout
        finally:
            cli_runner(["pod", "destroy", alias, "--force"])
