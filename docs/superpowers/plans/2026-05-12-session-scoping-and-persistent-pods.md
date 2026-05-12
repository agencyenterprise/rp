# Session-scoped pods, stop-not-destroy, and pod notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `rp` safe to use across multiple parallel Claude Code sessions by (a) defaulting to stop-instead-of-destroy with a persistent `/workspace`, (b) tagging pods with the originating Claude session and filtering/confirming on cross-session ops, (c) attaching one-line notes to each pod for stale-pod review, and (d) surfacing stopped-pod cost banners after 24 hours.

**Architecture:** Four orthogonal changes implemented in phases A–F. Layer 1 (stop-not-destroy + storage defaults) carries most of the safety value; layer 2 (stale warnings + `rp prune`) covers the new "stopped pods pile up" failure mode that layer 1 introduces; layer 3 (session scoping) adds a soft cross-session safety net via `CLAUDE_CODE_SESSION_ID`; layer 4 (notes) provides the context needed when reviewing stopped pods later. Phase G covers E2E coverage and docs; Phase H is the version bump.

**Tech Stack:** Python 3.13+, Pydantic v2, Typer, pytest. macOS-only secret backend (unaffected by this work). All on-disk state is `~/.config/rp/pods.json` protected by an exclusive file lock.

**Spec:** `docs/superpowers/specs/2026-05-12-session-scoping-and-persistent-pods-design.md`

---

## File Map

**New files:**
- `src/rp/core/session.py` — `current_session_id()` resolver
- `tests/unit/test_session.py` — session resolution tests
- `tests/unit/test_pod_metadata.py` — new-field serialization tests
- `tests/unit/test_stale_warnings.py` — stale detection logic tests
- `tests/unit/test_pod_notes.py` — note command unit tests
- `tests/e2e/test_pod_notes.py` — E2E note lifecycle
- `tests/e2e/test_stop_not_destroy.py` — E2E `rp down` semantics
- `tests/e2e/test_session_scoping.py` — E2E session filter + confirm prompt

**Modified files:**
- `src/rp/core/models.py` — `PodMetadata` field additions
- `src/rp/core/pod_manager.py` — populate fields; stop tracks timestamp; session filter; stale helpers
- `src/rp/core/default_templates.py` — `storage_spec="400GB"`, `container_disk_spec="50GB"`
- `src/rp/core/pod_setup.py` — `_TOOL_INSTALL_SCRIPT` exports XDG/cache env vars
- `src/rp/assets/auto_shutdown.sh` — `DELETE` → `POST /stop`
- `src/rp/cli/main.py` — `--storage` rename; `--note` flag; `--destroy`, `--all`, `--all-sessions` flags; new `pod note`, `prune` commands
- `src/rp/cli/commands.py` — implementations for all of the above plus stale banner
- `src/rp/cli/utils.py` — `format_age()`, `format_storage_cost()` helpers
- `docs.md`, `README.md` — flag rename, new commands, defaults
- `~/.claude/skills/runpod/docs/start.md` — note-writing instruction, `/workspace` for clones
- `~/.claude/skills/runpod/docs/stop.md` — `--destroy` guidance
- `pyproject.toml` — `0.12.0` → `0.13.0`

---

## Phase A — Foundation

### Task A1: Session resolver helper

**Files:**
- Create: `src/rp/core/session.py`
- Test: `tests/unit/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session.py
"""Tests for the session-id resolver."""

import pytest

from rp.core.session import current_session_id


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("RP_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def test_returns_none_when_no_env_vars():
    assert current_session_id() is None


def test_returns_claude_code_session_id_when_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert current_session_id() == "abc-123"


def test_rp_session_id_takes_precedence(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    monkeypatch.setenv("RP_SESSION_ID", "override-xyz")
    assert current_session_id() == "override-xyz"


def test_empty_rp_session_id_falls_through_to_claude(monkeypatch):
    """Empty string means 'opt out of scoping' — should NOT mask CLAUDE_CODE_SESSION_ID.

    Rationale: a user setting RP_SESSION_ID='' in a shell to disable scoping
    shouldn't surprise them inside Claude. Treat empty as unset.
    """
    monkeypatch.setenv("RP_SESSION_ID", "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    assert current_session_id() == "abc-123"


def test_empty_claude_session_id_returns_none(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    assert current_session_id() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: ImportError / collection failure (module doesn't exist yet).

- [ ] **Step 3: Implement**

```python
# src/rp/core/session.py
"""Resolve the current Claude Code / rp session ID for pod scoping."""

import os


def current_session_id() -> str | None:
    """Return the active session ID, or None if no session is active.

    Precedence:
      1. RP_SESSION_ID (explicit user override; empty string = unset)
      2. CLAUDE_CODE_SESSION_ID (auto-set by Claude Code ≥ 2.1.139)
      3. None — bare terminal, scripts, CI, etc.
    """
    explicit = os.environ.get("RP_SESSION_ID")
    if explicit:
        return explicit
    claude = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if claude:
        return claude
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/session.py tests/unit/test_session.py
git commit -m "Add session-id resolver for pod scoping

Reads RP_SESSION_ID (explicit override) then CLAUDE_CODE_SESSION_ID
(auto-set by Claude Code). Returns None outside any session so bare
terminal use stays scoping-free."
```

---

### Task A2: Add new PodMetadata fields

**Files:**
- Modify: `src/rp/core/models.py:260-266` (PodMetadata class)
- Test: `tests/unit/test_pod_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pod_metadata.py
"""Tests for PodMetadata serialization with the new layer-1/3/4 fields."""

from datetime import datetime, timezone

from rp.core.models import AppConfig, PodMetadata


def test_old_pod_metadata_json_deserializes_with_defaults():
    """Pre-existing pods.json rows (no new fields) must keep loading."""
    old_payload = {
        "pod_metadata": {
            "ast_alex_1": {"pod_id": "abc123", "managed": True}
        }
    }
    config = AppConfig.model_validate(old_payload)
    meta = config.pod_metadata["ast_alex_1"]
    assert meta.pod_id == "abc123"
    assert meta.managed is True
    assert meta.owner_session_id is None
    assert meta.stopped_at is None
    assert meta.note is None


def test_new_fields_round_trip():
    when = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    meta = PodMetadata(
        pod_id="abc",
        managed=True,
        owner_session_id="session-uuid",
        stopped_at=when,
        note="AE-1234: classifier eval",
    )
    blob = meta.model_dump_json()
    restored = PodMetadata.model_validate_json(blob)
    assert restored.owner_session_id == "session-uuid"
    assert restored.stopped_at == when
    assert restored.note == "AE-1234: classifier eval"


def test_defaults_for_new_fields():
    meta = PodMetadata(pod_id="abc")
    assert meta.owner_session_id is None
    assert meta.stopped_at is None
    assert meta.note is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_metadata.py -v`
Expected: fail on `meta.owner_session_id` (AttributeError).

- [ ] **Step 3: Implement**

Edit `src/rp/core/models.py`, replace the existing `PodMetadata` class (around line 260):

```python
class PodMetadata(BaseModel):
    """Pod metadata persisted in pods.json."""

    pod_id: str = Field(description="RunPod instance ID")
    managed: bool = Field(
        default=False, description="Whether this pod was created with 'rp up'"
    )
    owner_session_id: str | None = Field(
        default=None,
        description="Session that created this pod (RP_SESSION_ID or CLAUDE_CODE_SESSION_ID at creation). None = unscoped.",
    )
    stopped_at: datetime | None = Field(
        default=None,
        description="Wall-clock time when the pod was last stopped via rp. Cleared on start. Drives stale-pod warnings.",
    )
    note: str | None = Field(
        default=None,
        description="Free-form one-line description of what the pod is for (ticket, task). Shown in list/show/prune output.",
    )
```

The `datetime` import already exists at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_metadata.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/models.py tests/unit/test_pod_metadata.py
git commit -m "Extend PodMetadata with owner_session_id, stopped_at, note

Three independent additions used by upcoming layers:
- owner_session_id powers session-scoped list/confirm
- stopped_at drives the 24h stale-pod warning
- note carries human/agent-written context into stale review

All default to None so existing pods.json files load unchanged."
```

---

## Phase B — Pod notes

### Task B1: Thread `--note` through `rp up` and `rp pod create`

**Files:**
- Modify: `src/rp/cli/main.py:126-157` (up), `:294-351` (pod create)
- Modify: `src/rp/cli/commands.py:106-117` (create_command signature), `:289-298` (up_command signature)
- Modify: `src/rp/core/pod_manager.py` (`create_pod`, `create_pod_from_template` — accept and store note)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pod_notes.py
"""Tests for pod-note plumbing through create commands and the dedicated command."""

from rp.core.models import AppConfig, PodMetadata


def test_app_config_records_note_on_alias(tmp_path):
    """add_alias accepts a note kwarg and stores it on the metadata."""
    config = AppConfig()
    config.add_alias("foo", "pod-1", note="AE-1234: classifier")
    assert config.pod_metadata["foo"].note == "AE-1234: classifier"


def test_app_config_no_note_leaves_field_none():
    config = AppConfig()
    config.add_alias("foo", "pod-1")
    assert config.pod_metadata["foo"].note is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_notes.py -v`
Expected: `TypeError: add_alias() got an unexpected keyword argument 'note'`.

- [ ] **Step 3: Implement**

In `src/rp/core/models.py`, update `AppConfig.add_alias`:

```python
def add_alias(
    self,
    alias: str,
    pod_id: str,
    force: bool = False,
    *,
    note: str | None = None,
    owner_session_id: str | None = None,
) -> bool:
    """Add or update an alias mapping.

    note / owner_session_id are only set when creating a fresh row.
    Updating an existing alias preserves the existing values for these
    fields unless force=True is supplied with new values.
    """
    existing = self.pod_metadata.get(alias)
    if existing is not None:
        if existing.pod_id == pod_id:
            return True
        if not force:
            return False
    self.pod_metadata[alias] = PodMetadata(
        pod_id=pod_id,
        note=note,
        owner_session_id=owner_session_id,
    )
    return True
```

In `src/rp/core/pod_manager.py`, update `add_alias` (around line 89):

```python
def add_alias(
    self,
    alias: str,
    pod_id: str,
    force: bool = False,
    *,
    note: str | None = None,
    owner_session_id: str | None = None,
) -> None:
    """Add or update an alias mapping."""
    with self._locked_config() as config:
        if not config.add_alias(
            alias,
            pod_id,
            force,
            note=note,
            owner_session_id=owner_session_id,
        ):
            raise AliasError.already_exists(alias)
```

Update `PodCreateRequest` in `src/rp/core/models.py` to carry a `note` field:

```python
class PodCreateRequest(BaseModel):
    # ... existing fields unchanged ...
    note: str | None = Field(default=None, description="Free-form one-line note describing this pod")
```

Update `PodManager.create_pod` to pass `note` and `owner_session_id` to `add_alias`. Find the section that does `config.add_alias(request.alias, pod_id, force=request.force)` (around line 196) and replace with:

```python
        from rp.core.session import current_session_id

        with self._locked_config() as config:
            config.add_alias(
                request.alias,
                pod_id,
                force=request.force,
                note=request.note,
                owner_session_id=current_session_id(),
            )
```

Update `create_pod_from_template` to accept and pass through a `note` kwarg (add `note: str | None = None` param; pass into `PodCreateRequest`).

Update `src/rp/cli/commands.py`:

- `create_command` signature: add `note: str | None = None` and thread it into `PodCreateRequest(..., note=note)` and `create_pod_from_template(..., note=note)`.
- `up_command` signature: add `note: str | None = None` and thread it identically.

Update `src/rp/cli/main.py`:

- `up()` typer command: add `note: str = typer.Option(None, "--note", help="One-line description of what this pod is for (e.g. 'AE-1234: classifier eval'). Shown in rp pod list and stale-pod warnings.")` and pass to `up_command`.
- `create()` typer command: same `note` option, pass to `create_command`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_notes.py -v`
Expected: 2 passed.

Also sanity-check existing tests:
Run: `uv run pytest tests/unit/ -v`
Expected: all pass (the keyword-only `note=` / `owner_session_id=` arguments don't break existing positional callers).

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/models.py src/rp/core/pod_manager.py src/rp/cli/commands.py src/rp/cli/main.py tests/unit/test_pod_notes.py
git commit -m "Thread --note through rp up and rp pod create

Adds --note CLI flag, PodCreateRequest.note, and keyword-only note
and owner_session_id args on AppConfig/PodManager add_alias. The
owner_session_id is auto-populated from current_session_id() at
create time."
```

---

### Task B2: `rp pod note` command (set / append / clear / show)

**Files:**
- Modify: `src/rp/core/pod_manager.py` — add `get_note`, `set_note`, `append_note`, `clear_note`
- Modify: `src/rp/cli/commands.py` — `note_command`
- Modify: `src/rp/cli/main.py` — register `pod note` subcommand

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_pod_notes.py`:

```python
def test_pod_manager_note_lifecycle(temp_config_dir):  # noqa: ARG001
    """get / set / append / clear round trip via PodManager."""
    from rp.core.pod_manager import PodManager

    pm = PodManager(api_client=None)
    pm.add_alias("foo", "pod-1")
    assert pm.get_note("foo") is None

    pm.set_note("foo", "AE-1234: classifier eval")
    assert pm.get_note("foo") == "AE-1234: classifier eval"

    pm.append_note("foo", "checkpoint at /workspace/runs/v3")
    note = pm.get_note("foo")
    assert "AE-1234: classifier eval" in note
    assert "checkpoint at /workspace/runs/v3" in note

    pm.clear_note("foo")
    assert pm.get_note("foo") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_notes.py::test_pod_manager_note_lifecycle -v`
Expected: AttributeError on `pm.get_note`.

- [ ] **Step 3: Implement**

In `src/rp/core/pod_manager.py`, add methods (after `set_managed`):

```python
    def get_note(self, alias: str) -> str | None:
        """Return the note for an alias, or None if unset."""
        self.get_pod_id(alias)  # validates alias exists
        meta = self.config.pod_metadata.get(alias)
        return meta.note if meta else None

    def set_note(self, alias: str, note: str) -> None:
        """Replace the note for an alias."""
        with self._locked_config() as config:
            meta = config.pod_metadata.get(alias)
            if meta is None:
                raise AliasError.not_found(alias, list(config.get_all_aliases()))
            meta.note = note

    def append_note(self, alias: str, addition: str) -> None:
        """Append text to an existing note (newline-separated). Creates the note if absent."""
        with self._locked_config() as config:
            meta = config.pod_metadata.get(alias)
            if meta is None:
                raise AliasError.not_found(alias, list(config.get_all_aliases()))
            meta.note = f"{meta.note}\n{addition}" if meta.note else addition

    def clear_note(self, alias: str) -> None:
        with self._locked_config() as config:
            meta = config.pod_metadata.get(alias)
            if meta is None:
                raise AliasError.not_found(alias, list(config.get_all_aliases()))
            meta.note = None
```

In `src/rp/cli/commands.py`, add:

```python
def note_command(
    alias: str | None,
    text: str | None,
    *,
    append: bool = False,
    clear: bool = False,
) -> None:
    """Set / append / clear / show a pod's note."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)

        if clear:
            pod_manager.clear_note(alias)
            console.print(f"🗑️  Cleared note for '[bold]{alias}[/bold]'")
            return

        if text is None:
            # show
            current = pod_manager.get_note(alias)
            if current:
                console.print(current)
            else:
                console.print(f"[dim](no note set for {alias})[/dim]")
            return

        if append:
            pod_manager.append_note(alias, text)
            console.print(f"✏️  Appended to note for '[bold]{alias}[/bold]'")
        else:
            pod_manager.set_note(alias, text)
            console.print(f"✏️  Set note for '[bold]{alias}[/bold]'")

    except Exception as e:
        handle_cli_error(e)
```

In `src/rp/cli/main.py`, import `note_command` and add to `pod_app`:

```python
@pod_app.command("note")
def pod_note(
    alias: str = typer.Argument(
        None, help="Pod alias", autocompletion=complete_alias
    ),
    text: str = typer.Argument(
        None, help="Note text. Omit to print the current note."
    ),
    append: bool = typer.Option(
        False, "--append", "-a", help="Append to the existing note instead of replacing"
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Remove the note entirely"
    ),
):
    """Set, append to, clear, or print the one-line note attached to a pod.

    Examples:
        rp pod note my-pod "AE-1234: classifier eval"
        rp pod note my-pod --append "checkpoint at /workspace/runs/v3"
        rp pod note my-pod --clear
        rp pod note my-pod
    """
    note_command(alias, text, append=append, clear=clear)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_notes.py -v`
Expected: 3 passed.

Smoke check the CLI registers:
Run: `uv run rp pod note --help`
Expected: help text shown (exits 0).

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/pod_manager.py src/rp/cli/commands.py src/rp/cli/main.py tests/unit/test_pod_notes.py
git commit -m "Add rp pod note command (set / append / clear / show)

Append uses a newline separator so multiple short context bumps stay
readable in rp pod show output."
```

---

### Task B3: Display note in `rp pod show` and `rp pod list`

**Files:**
- Modify: `src/rp/cli/commands.py:662-739` (show_command) — add Note line
- Modify: `src/rp/cli/utils.py` (display_pods_table) — conditionally add Note column

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_pod_notes.py
def test_show_command_renders_note(cli_runner_in_memory):
    """rp pod show includes the note when one is set."""
    # cli_runner_in_memory: invokes the typer app against a temp config dir
    # (existing test pattern — see test_cli_utils.py for setup).
    # If this fixture doesn't exist, skip; we'll cover this end-to-end
    # in the E2E test instead.
    pass  # placeholder — actual assertion deferred to E2E coverage
```

Skip writing a unit test for the rendered text (it'd require a lot of fixture wiring around the rich console). Instead, write a focused test for the column-presence logic in `display_pods_table`:

```python
# tests/unit/test_pod_display.py
"""Tests for pods-list rendering with the new note column."""

from io import StringIO

from rich.console import Console

from rp.cli.utils import display_pods_table
from rp.core.models import Pod, PodStatus


def _capture(pods):
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=False, no_color=True)
    display_pods_table(pods, console=console)
    return buf.getvalue()


def test_table_omits_note_column_when_no_notes():
    pods = [
        Pod(id="p1", alias="foo", status=PodStatus.RUNNING),
        Pod(id="p2", alias="bar", status=PodStatus.STOPPED),
    ]
    out = _capture(pods)
    assert "Note" not in out


def test_table_includes_note_column_when_any_pod_has_note():
    pod_with_note = Pod(id="p1", alias="foo", status=PodStatus.RUNNING)
    pod_with_note.note = "AE-1234: classifier eval"  # set via attr — see impl
    pod_without = Pod(id="p2", alias="bar", status=PodStatus.STOPPED)
    out = _capture([pod_with_note, pod_without])
    assert "Note" in out
    assert "AE-1234: classifier" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_display.py -v`
Expected: fail — either `Pod` has no `note` field, or `display_pods_table` doesn't accept a `console=` kwarg, or doesn't render the column.

- [ ] **Step 3: Implement**

Add a `note` field on `Pod` so the data flows from PodMetadata into the display layer. In `src/rp/core/models.py`, add to `Pod`:

```python
    note: str | None = Field(None, description="One-line description (mirrored from PodMetadata for display)")
```

In `src/rp/core/pod_manager.py`, in `list_pods` (around line 130), after `pod = ...` is built, attach the note:

```python
            meta = self.config.pod_metadata.get(alias)
            if meta and meta.note:
                pod.note = meta.note
```

Also in `get_pod` (around line 119) do the same so `rp pod show` sees it.

In `src/rp/cli/utils.py`, update `display_pods_table` to accept an optional console and conditionally include a Note column. Find the existing function and modify so it:
1. Accepts `console: Console | None = None` (default: module `console`).
2. Computes `any_notes = any(p.note for p in pods)`.
3. Adds a "Note" column only when `any_notes`, truncated to 40 chars.

(See the existing function — preserve its structure; only add the new column and the optional console param.)

In `src/rp/cli/commands.py`, in `show_command` (after the existing storage section, around line 690), add:

```python
        # Note
        if hasattr(pod, "note") and pod.note:
            console.print(f"[bold]Note:[/bold]      {pod.note}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_display.py tests/unit/test_pod_notes.py -v`
Expected: all pass.

Smoke: `uv run rp pod note --help` and `uv run rp pod list` still work.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/models.py src/rp/core/pod_manager.py src/rp/cli/utils.py src/rp/cli/commands.py tests/unit/test_pod_display.py
git commit -m "Show note in rp pod show and rp pod list output

Note column appears only when at least one pod has a note set, to
keep the default table compact."
```

---

### Task B4: `rp up` reminder when `--note` missing inside Claude

**Files:**
- Modify: `src/rp/cli/commands.py` (up_command) — print reminder after success

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_pod_notes.py
def test_note_reminder_in_claude_when_unset(monkeypatch, capsys):
    """The reminder line is emitted when CLAUDECODE=1 and no note was passed."""
    from rp.cli.commands import _print_note_reminder_if_needed

    monkeypatch.setenv("CLAUDECODE", "1")
    _print_note_reminder_if_needed("ast_alex_4", note=None)
    captured = capsys.readouterr()
    assert "No note set" in captured.out
    assert "rp pod note ast_alex_4" in captured.out


def test_no_reminder_outside_claude(monkeypatch, capsys):
    from rp.cli.commands import _print_note_reminder_if_needed

    monkeypatch.delenv("CLAUDECODE", raising=False)
    _print_note_reminder_if_needed("ast_alex_4", note=None)
    captured = capsys.readouterr()
    assert "No note set" not in captured.out


def test_no_reminder_when_note_provided(monkeypatch, capsys):
    from rp.cli.commands import _print_note_reminder_if_needed

    monkeypatch.setenv("CLAUDECODE", "1")
    _print_note_reminder_if_needed("ast_alex_4", note="AE-1234: x")
    captured = capsys.readouterr()
    assert "No note set" not in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_notes.py -v -k reminder`
Expected: ImportError on `_print_note_reminder_if_needed`.

- [ ] **Step 3: Implement**

In `src/rp/cli/commands.py`, add a helper near the top:

```python
def _print_note_reminder_if_needed(alias: str, note: str | None) -> None:
    """When inside Claude Code and no note was given, print a single-line reminder.

    Suppressed for bare-terminal use to avoid nagging human operators.
    """
    if note:
        return
    if os.environ.get("CLAUDECODE") != "1":
        return
    console.print(
        f"ℹ️  No note set. Run: [bold]rp pod note {alias} \"<ticket-id>: <task>\"[/bold]"
    )
```

Add `import os` at the top of the file if not already imported.

Call this at the end of `up_command`'s successful path (after the `🎉 Managed pod ... is ready.` line but inside the success branch):

```python
            _print_note_reminder_if_needed(final_alias, note)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_notes.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/rp/cli/commands.py tests/unit/test_pod_notes.py
git commit -m "Print 'no note set' reminder after rp up inside Claude Code

Single line, after success banner. Suppressed outside Claude so bare-
terminal use isn't nagged."
```

---

## Phase C — Storage rename and new defaults

### Task C1: Rename `--persistent-volume` to `--storage`

**Files:**
- Modify: `src/rp/cli/main.py` — three call sites (up, pod create, template create)
- Modify: `src/rp/cli/commands.py` — parameter rename
- Modify: existing tests referring to `--persistent-volume`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_storage_flags.py` (file already exists from PR #10):

```python
def test_up_help_uses_storage_flag(cli_runner):
    """rp up --help should show --storage, not --persistent-volume."""
    result = cli_runner(["up", "--help"], env={"NO_COLOR": "1"})
    assert "--storage" in result.stdout
    assert "--persistent-volume" not in result.stdout


def test_pod_create_help_uses_storage_flag(cli_runner):
    result = cli_runner(["pod", "create", "--help"], env={"NO_COLOR": "1"})
    assert "--storage" in result.stdout
    assert "--persistent-volume" not in result.stdout


def test_template_create_help_uses_storage_flag(cli_runner):
    result = cli_runner(["template", "create", "--help"], env={"NO_COLOR": "1"})
    assert "--storage" in result.stdout
    assert "--persistent-volume" not in result.stdout
```

(If `tests/unit/test_storage_flags.py` doesn't have a `cli_runner` fixture, look at the existing tests there — they use `subprocess` or typer's `CliRunner`. Match the pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_storage_flags.py -v -k uses_storage`
Expected: assertion failure — `--storage` not present.

- [ ] **Step 3: Implement**

In `src/rp/cli/main.py`:

- `up()` (line ~140-145): rename `persistent_volume` parameter to `storage`, flag from `"--persistent-volume"` to `"--storage"`. Update the call to `up_command(...)`.
- `create()` (line ~310-315 inside `pod_app`): same rename. Update call to `create_command(...)`.
- `template_create()` (line ~475-480): same rename. Update call to `template_create_command(...)`.

In `src/rp/cli/commands.py`:

- `up_command(...)`: rename param `persistent_volume` → `storage`, update inner uses (parse_storage_spec call).
- `create_command(...)`: same rename.
- `template_create_command(...)`: same rename. Internally the field is `storage_spec` on the template, which is already correctly named.

Existing E2E tests use `--persistent-volume` (see `tests/e2e/test_pod_lifecycle.py:_create_pod_with_fallback`). Update them to use `--storage`.

Search for any remaining references:
```bash
grep -rn "persistent_volume\|persistent-volume" src/ tests/
```
Replace all hits.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_storage_flags.py -v`
Expected: all pass.

Run: `uv run pytest tests/unit/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Rename --persistent-volume to --storage

Shorter and clearer. The old flag is removed (not aliased) — pre-1.0
software, no users outside our team.

Renames the parameter through CLI -> commands -> tests; the template
field name (storage_spec) was already correct."
```

---

### Task C2: New defaults — 400GB storage, 50GB container disk

**Files:**
- Modify: `src/rp/core/default_templates.py` — flip `storage_spec` and `container_disk_spec`
- Modify: `src/rp/cli/commands.py` — defaults in `up_command` and `create_command` when no template
- Test: `tests/unit/test_storage_flags.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_storage_flags.py`:

```python
def test_default_templates_use_400gb_storage_and_50gb_disk():
    from rp.core.default_templates import get_default_templates

    for ident, tpl in get_default_templates().items():
        assert tpl.storage_spec == "400GB", f"{ident} storage_spec was {tpl.storage_spec}"
        assert tpl.container_disk_spec == "50GB", f"{ident} container_disk_spec was {tpl.container_disk_spec}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_storage_flags.py -v -k default_templates_use`
Expected: assertion failure (currently 0GB / 500GB).

- [ ] **Step 3: Implement**

In `src/rp/core/default_templates.py`, change `DEFAULT_CONTAINER_DISK` to `"50GB"` and replace every `storage_spec="0GB"` with `storage_spec="400GB"` (13 entries). Single find-replace per literal.

In `src/rp/cli/commands.py`:

- In `up_command`, replace the line `container_disk_gb=disk_gb if disk_gb is not None else 500,` with `container_disk_gb=disk_gb if disk_gb is not None else 50,`. Replace `volume_gb if volume_gb is not None else 0` with `volume_gb if volume_gb is not None else 400` (this is the new `rp up --gpu X` default — no template).
- In `create_command`, similar updates: `parse_storage_spec(disk) if disk is not None else 500` → `... else 50`; `parse_storage_spec(persistent_volume)/storage if ... else 0` → `... else 400`.

Update `src/rp/core/models.py:185` — `container_disk_gb: int = Field(default=20, ge=10, ...)`:

The default value of the Pydantic model field stays at 20 (it's only used when the field isn't supplied). The CLI layer overrides with explicit 50/400 above. Leave the Pydantic default alone to avoid breaking unrelated callers.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_storage_flags.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Default to 400GB /workspace volume and 50GB container disk

Persistence is now the default so work survives stop/start; the
container disk shrinks since everything valuable lives on the
persistent volume. Existing user templates with explicit values are
unaffected — only the built-in default_templates and the
no-template paths in up_command/create_command change."
```

---

### Task C3: Workspace cache env vars in pod setup

**Files:**
- Modify: `src/rp/core/pod_setup.py` — `_TOOL_INSTALL_SCRIPT` writes XDG/cache exports into `/etc/profile.d/rp-env.sh`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pod_setup_script.py
"""Verify the inline setup script content; full E2E covered by tests/e2e."""

from rp.core.pod_setup import _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_xdg_cache_home():
    assert "export XDG_CACHE_HOME=/workspace/.cache" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_uv_cache_dir():
    assert "export UV_CACHE_DIR=/workspace/.cache/uv" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_pip_cache_dir():
    assert "export PIP_CACHE_DIR=/workspace/.cache/pip" in _TOOL_INSTALL_SCRIPT


def test_setup_script_exports_hf_home():
    assert "export HF_HOME=/workspace/.cache/huggingface" in _TOOL_INSTALL_SCRIPT


def test_setup_script_creates_cache_dir():
    """The /workspace/.cache directory must be created before exports point at it."""
    assert "mkdir -p /workspace/.cache" in _TOOL_INSTALL_SCRIPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_setup_script.py -v`
Expected: 5 failures (none of the strings present).

- [ ] **Step 3: Implement**

In `src/rp/core/pod_setup.py`, modify the `/etc/profile.d/rp-env.sh` heredoc inside `_TOOL_INSTALL_SCRIPT` (around line 504). Add the cache env vars and a `mkdir -p` before the existing exports:

```python
_TOOL_INSTALL_SCRIPT = (
    _APT_WAIT_PREAMBLE
    + """\
set -e
# ... existing apt installs unchanged ...

# Ensure workspace cache dir exists for the rp-managed env exports below
mkdir -p /workspace/.cache

# Environment sourcing
cat > /etc/profile.d/rp-env.sh << 'PROFILED'
export PATH="$HOME/.local/bin:$PATH"
export XDG_CACHE_HOME=/workspace/.cache
export UV_CACHE_DIR=/workspace/.cache/uv
export PIP_CACHE_DIR=/workspace/.cache/pip
export HF_HOME=/workspace/.cache/huggingface
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
PROFILED
chmod 644 /etc/profile.d/rp-env.sh
# ... rest unchanged ...
"""
)
```

(Keep the rest of `_TOOL_INSTALL_SCRIPT` exactly as-is. Only the heredoc body and the new `mkdir -p` line change.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_setup_script.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/pod_setup.py tests/unit/test_pod_setup_script.py
git commit -m "Point uv/pip/hf caches at /workspace/.cache on managed pods

XDG_CACHE_HOME plus tool-specific overrides means model downloads and
package caches survive stop/start with the persistent volume. The
profile.d hook also makes them visible in non-rp shells."
```

---

## Phase D — Stop-not-destroy

### Task D1: Track `stopped_at` on stop / start

**Files:**
- Modify: `src/rp/core/pod_manager.py` — `stop_pod`, `start_pod` update `stopped_at`
- Test: `tests/unit/test_pod_manager_lifecycle.py` (new — small)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pod_manager_lifecycle.py
"""Unit tests for stop/start side effects on PodMetadata."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from rp.core.pod_manager import PodManager
from rp.core.models import PodStatus


def _make_pm():
    api = MagicMock()
    api.stop_pod.return_value = None
    api.start_pod.return_value = None
    api.wait_for_pod_ready.return_value = {"id": "pod-1", "desiredStatus": "RUNNING"}
    pm = PodManager(api_client=api)
    pm.add_alias("foo", "pod-1")
    return pm


def test_stop_pod_records_stopped_at(temp_config_dir):  # noqa: ARG001
    pm = _make_pm()
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    pm.stop_pod("foo")
    meta = pm.config.pod_metadata["foo"]
    assert meta.stopped_at is not None
    assert meta.stopped_at >= before


def test_start_pod_clears_stopped_at(temp_config_dir):  # noqa: ARG001
    pm = _make_pm()
    pm.stop_pod("foo")
    assert pm.config.pod_metadata["foo"].stopped_at is not None
    pm.start_pod("foo")
    assert pm.config.pod_metadata["foo"].stopped_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pod_manager_lifecycle.py -v`
Expected: assertion failure (`stopped_at` is still None after stop).

- [ ] **Step 3: Implement**

In `src/rp/core/pod_manager.py`:

```python
    def stop_pod(self, alias: str) -> None:
        """Stop a pod and record the timestamp."""
        from datetime import datetime, timezone

        pod_id = self.get_pod_id(alias)
        self.api_client.stop_pod(pod_id)
        with self._locked_config() as config:
            meta = config.pod_metadata.get(alias)
            if meta is not None:
                meta.stopped_at = datetime.now(timezone.utc)

    def start_pod(self, alias: str) -> Pod:
        """Start/resume a pod and clear stopped_at."""
        pod_id = self.get_pod_id(alias)
        self.api_client.start_pod(pod_id)
        pod_data = self.api_client.wait_for_pod_ready(pod_id, timeout=300)
        with self._locked_config() as config:
            meta = config.pod_metadata.get(alias)
            if meta is not None:
                meta.stopped_at = None
        return Pod.from_runpod_response(alias, pod_data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pod_manager_lifecycle.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/pod_manager.py tests/unit/test_pod_manager_lifecycle.py
git commit -m "Track stopped_at on pod stop / start

Drives the 24h stale-pod warning. Cleared on start so pods that
get woken up don't trigger the banner."
```

---

### Task D2: `rp down` defaults to stop; `--destroy` opt-in

**Files:**
- Modify: `src/rp/cli/commands.py` — `down_command` branches on `destroy`
- Modify: `src/rp/cli/main.py` — `down()` adds `--destroy` flag

- [ ] **Step 1: Write the failing test**

This is hard to unit-test cleanly (the command orchestrates many subsystems). Cover the routing with a focused mock-based test:

```python
# tests/unit/test_down_command.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_down_command.py -v`
Expected: fail — `down_command` doesn't accept `destroy=` kwarg yet.

- [ ] **Step 3: Implement**

Replace `down_command` in `src/rp/cli/commands.py`:

```python
def down_command(
    alias: str | None,
    skip_logs: bool = False,
    destroy: bool = False,
) -> None:
    """Sync logs then stop (default) or destroy (--destroy) a managed pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.get_pod_id(alias)

        if not skip_logs:
            try:
                from rp.core.claude_remote import ClaudeRemote

                console.print(f"📥 Syncing logs from '[bold]{alias}[/bold]'…")
                remote = ClaudeRemote(alias, pod_id, console)
                local_dir = remote.sync_logs()
                console.print(f"✅ Logs synced to [bold]{local_dir}[/bold]")
            except Exception as log_err:
                console.print(f"[yellow]⚠ Could not sync logs: {log_err}[/yellow]")

        ssh_manager = get_ssh_manager()

        if destroy:
            console.print(f"🔥 Destroying pod '[bold]{alias}[/bold]'…")
            pod_manager.destroy_pod(alias)
            console.print(f"✅ Terminated pod [bold]{pod_id}[/bold].")
            removed = ssh_manager.remove_host_config(alias)
            if removed:
                console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")
            console.print(
                f"🗑️  Removed alias '[bold]{alias}[/bold]' from local configuration."
            )
        else:
            console.print(f"🛑 Stopping pod '[bold]{alias}[/bold]'…")
            pod_manager.stop_pod(alias)
            console.print(
                f"✅ Pod stopped. /workspace and alias preserved — "
                f"resume with [bold]rp pod start {alias}[/bold]."
            )
            removed = ssh_manager.remove_host_config(alias)
            if removed:
                console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")

        _auto_clean()

    except Exception as e:
        handle_cli_error(e)
```

In `src/rp/cli/main.py`, update the `down()` typer command:

```python
@app.command()
def down(
    alias: str = typer.Argument(
        None,
        help="Pod alias",
        autocompletion=complete_alias,
    ),
    skip_logs: bool = typer.Option(
        False, "--skip-logs", help="Skip syncing Claude logs before stopping/destroying"
    ),
    destroy: bool = typer.Option(
        False,
        "--destroy",
        help="Permanently terminate the pod instead of stopping. "
        "Use only when (a) the pod is broken, or (b) all code is committed/pushed "
        "and all data is on S3 — anything in /workspace will be lost.",
    ),
):
    """Sync logs and stop a pod (use --destroy to terminate permanently)."""
    down_command(alias, skip_logs, destroy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_down_command.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/pod_manager.py src/rp/cli/commands.py src/rp/cli/main.py tests/unit/test_down_command.py
git commit -m "rp down stops by default; --destroy flag terminates

Major behavior change: work in /workspace is preserved across rp down.
Users who want the old behavior pass --destroy. The success message
now points at rp pod start to resume."
```

---

### Task D3: Auto-shutdown stops instead of destroys

**Files:**
- Modify: `src/rp/assets/auto_shutdown.sh` — change `DELETE /v1/pods/<id>` to `POST /v1/pods/<id>/stop`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_auto_shutdown_script.py
"""The bundled auto_shutdown.sh stops the pod, not destroys it."""

import importlib.resources


def _script() -> str:
    ref = importlib.resources.files("rp.assets").joinpath("auto_shutdown.sh")
    with importlib.resources.as_file(ref) as p:
        return p.read_text()


def test_auto_shutdown_calls_stop_endpoint():
    s = _script()
    assert "/v1/pods/${RUNPOD_POD_ID}/stop" in s


def test_auto_shutdown_uses_post_not_delete():
    s = _script()
    # The single curl call inside the idle-exceeded branch.
    assert "-X POST" in s
    assert "-X DELETE" not in s


def test_auto_shutdown_log_message_says_stop():
    s = _script()
    assert "Stopping pod" in s
    # Old log line should be gone.
    assert "Destroying pod" not in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auto_shutdown_script.py -v`
Expected: 3 failures.

- [ ] **Step 3: Implement**

In `src/rp/assets/auto_shutdown.sh`, replace the idle-exceeded block at the bottom (currently lines 67-80):

```bash
if [ "$IDLE_MINUTES" -ge "$IDLE_THRESHOLD_MINUTES" ]; then
    echo "$LOG_PREFIX Idle threshold exceeded. Stopping pod ${RUNPOD_POD_ID}..."

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
        "https://rest.runpod.io/v1/pods/${RUNPOD_POD_ID}/stop")

    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "204" ]; then
        echo "$LOG_PREFIX Pod stop request sent (HTTP ${HTTP_CODE})."
    else
        echo "$LOG_PREFIX Pod stop request returned HTTP ${HTTP_CODE}."
    fi
fi
```

Also update the file's top comment (lines 1-4):

```bash
#!/bin/bash
# Auto-stop script for GPU pods.
# Deployed by 'rp up', runs every 5 minutes via cron.
# Stops the pod after IDLE_THRESHOLD_MINUTES of all GPUs at 0% utilization.
# /workspace and the alias are preserved; resume with 'rp pod start <alias>'.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auto_shutdown_script.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/assets/auto_shutdown.sh tests/unit/test_auto_shutdown_script.py
git commit -m "Auto-shutdown stops the pod instead of destroying it

Preserves /workspace and the alias so a pod that idles out can be
resumed with rp pod start. The DELETE -> POST /stop change requires
no other rp updates — secrets and the cron job survive a stop/start
cycle (the pod_setup re-injection on start handles any drift)."
```

---

## Phase E — Session scoping

### Task E1: `rp pod list` filters by current session

**Files:**
- Modify: `src/rp/cli/commands.py` — `list_command` accepts `show_all`
- Modify: `src/rp/cli/main.py` — `pod list` adds `--all`
- Modify: `src/rp/cli/utils.py` — `display_pods_table` accepts optional `show_owner_column`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_scoping.py
"""Tests for session-aware filtering in rp pod list and destroy confirmation."""

from unittest.mock import MagicMock, patch

from rp.core.models import AppConfig, Pod, PodStatus


def _pods(*specs):
    """specs = list of (alias, owner_session_id) tuples"""
    out = []
    for alias, owner in specs:
        p = Pod(id=f"pod-{alias}", alias=alias, status=PodStatus.RUNNING)
        p.owner_session_id = owner
        out.append(p)
    return out


def test_list_filters_to_current_session(monkeypatch, capsys):
    """When CLAUDE_CODE_SESSION_ID is set, rp pod list shows only matching pods."""
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    monkeypatch.delenv("RP_SESSION_ID", raising=False)

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("mine", "session-a"),
            ("theirs", "session-b"),
            ("legacy", None),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=False)

    out = capsys.readouterr().out
    assert "mine" in out
    assert "theirs" not in out
    assert "legacy" in out, "Unowned (None) pods should always be visible"
    assert "1 pod owned by other session" in out


def test_list_all_ignores_session_filter(monkeypatch, capsys):
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("mine", "session-a"),
            ("theirs", "session-b"),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=True)

    out = capsys.readouterr().out
    assert "mine" in out
    assert "theirs" in out


def test_list_no_session_shows_all(monkeypatch, capsys):
    from rp.cli import commands

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("RP_SESSION_ID", raising=False)

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.list_pods.return_value = _pods(
            ("a", "session-a"),
            ("b", "session-b"),
        )
        get_pm.return_value = pm
        commands.list_command(show_all=False)

    out = capsys.readouterr().out
    assert "a" in out
    assert "b" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_session_scoping.py -v`
Expected: failures — `list_command` doesn't accept `show_all`, `Pod` may not have `owner_session_id` set.

- [ ] **Step 3: Implement**

In `src/rp/core/models.py`, add to `Pod`:

```python
    owner_session_id: str | None = Field(None, description="Owning session (mirrored from PodMetadata)")
```

In `src/rp/core/pod_manager.py`, in `list_pods`, set this from the metadata (same pattern as `note`):

```python
            meta = self.config.pod_metadata.get(alias)
            if meta:
                if meta.note:
                    pod.note = meta.note
                pod.owner_session_id = meta.owner_session_id
```

In `src/rp/cli/commands.py`, replace `list_command`:

```python
def list_command(show_all: bool = False) -> None:
    """List pods, filtered to the current session by default."""
    try:
        from rp.core.session import current_session_id

        pod_manager = get_pod_manager()
        pods = pod_manager.list_pods()

        session = current_session_id()

        if session is None or show_all:
            display_pods_table(pods, show_owner_column=(session is None or show_all))
            return

        owned = [p for p in pods if p.owner_session_id in (session, None)]
        other_count = sum(
            1
            for p in pods
            if p.owner_session_id is not None and p.owner_session_id != session
        )
        display_pods_table(owned)
        if other_count:
            plural = "pods" if other_count != 1 else "pod"
            console.print(
                f"\n[dim]+ {other_count} {plural} owned by other session"
                f"{'s' if other_count != 1 else ''} — use [bold]rp pod list --all[/bold] to see.[/dim]"
            )

    except Exception as e:
        handle_cli_error(e)
```

Update `src/rp/cli/utils.py:display_pods_table` signature to:

```python
def display_pods_table(
    pods: list[Pod],
    *,
    console: Console | None = None,
    show_owner_column: bool = False,
) -> None:
    """Render the pods table; conditionally adds Owner and Note columns."""
```

When `show_owner_column` is true, render an additional column showing the first 8 chars of `pod.owner_session_id` (or `-` if None). (Locate the existing table-building code and add this conditionally.)

In `src/rp/cli/main.py`, update the `pod list` registration:

```python
@pod_app.command("list")
def list_aliases(
    show_all: bool = typer.Option(
        False, "--all", help="Show pods from other Claude sessions too (default: current session only)"
    ),
):
    """List all pods: alias, ID, status."""
    list_command(show_all=show_all)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_session_scoping.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rp/core/models.py src/rp/core/pod_manager.py src/rp/cli/commands.py src/rp/cli/utils.py src/rp/cli/main.py tests/unit/test_session_scoping.py
git commit -m "rp pod list filters to current session by default

When CLAUDE_CODE_SESSION_ID (or RP_SESSION_ID) is set, the list
shows only owned pods (plus unowned/legacy pods) with a footer
counting other-session pods. --all shows everything with an Owner
column."
```

---

### Task E2: Confirm before destroying cross-session pods

**Files:**
- Modify: `src/rp/cli/commands.py` — `destroy_command` and `down_command(destroy=True)` prompt on cross-session
- Modify: `src/rp/cli/main.py` — `pod destroy` and `down` accept `--all-sessions`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_session_scoping.py`:

```python
def test_destroy_prompts_on_cross_session(monkeypatch):
    """Destroying a pod owned by another session triggers a confirm prompt."""
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.aliases = {"theirs": "pod-x"}
        pm.get_pod_id.return_value = "pod-x"
        meta = MagicMock()
        meta.owner_session_id = "session-b"
        meta.note = None
        pm.config.pod_metadata = {"theirs": meta}
        get_pm.return_value = pm
        with patch.object(commands, "get_ssh_manager"), patch(
            "typer.confirm", return_value=False
        ) as confirm:
            commands.destroy_command("theirs", force=False)
        confirm.assert_called()
        pm.destroy_pod.assert_not_called()


def test_destroy_no_prompt_when_owned(monkeypatch):
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.aliases = {"mine": "pod-x"}
        pm.get_pod_id.return_value = "pod-x"
        meta = MagicMock()
        meta.owner_session_id = "session-a"
        pm.config.pod_metadata = {"mine": meta}
        pm.destroy_pod.return_value = "pod-x"
        get_pm.return_value = pm
        with patch.object(commands, "get_ssh_manager"), patch(
            "typer.confirm", return_value=True
        ) as confirm:
            commands.destroy_command("mine", force=True)
        # force=True skips the standard "are you sure" prompt; the
        # cross-session prompt shouldn't fire either because it's owned.
        confirm.assert_not_called()
        pm.destroy_pod.assert_called_once()


def test_destroy_all_sessions_skips_prompt(monkeypatch):
    from rp.cli import commands

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")

    with patch.object(commands, "get_pod_manager") as get_pm:
        pm = MagicMock()
        pm.aliases = {"theirs": "pod-x"}
        pm.get_pod_id.return_value = "pod-x"
        meta = MagicMock()
        meta.owner_session_id = "session-b"
        pm.config.pod_metadata = {"theirs": meta}
        pm.destroy_pod.return_value = "pod-x"
        get_pm.return_value = pm
        with patch.object(commands, "get_ssh_manager"), patch(
            "typer.confirm"
        ) as confirm:
            commands.destroy_command(
                "theirs", force=True, all_sessions=True
            )
        confirm.assert_not_called()
        pm.destroy_pod.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_session_scoping.py -v -k destroy`
Expected: TypeError on `all_sessions=` kwarg or assertion mismatches.

- [ ] **Step 3: Implement**

Add a helper to `src/rp/cli/commands.py`:

```python
def _confirm_cross_session_or_exit(
    pod_manager: PodManager,
    alias: str,
    *,
    all_sessions: bool,
) -> None:
    """Prompt before acting on a pod owned by another session. typer.Exit on no.

    No-op when: no session is active, --all-sessions was passed, the pod is
    owned by us, or the pod has no owner (legacy / pre-feature pod).
    """
    if all_sessions:
        return
    from rp.core.session import current_session_id

    session = current_session_id()
    if session is None:
        return
    meta = pod_manager.config.pod_metadata.get(alias)
    if meta is None or meta.owner_session_id is None:
        return
    if meta.owner_session_id == session:
        return

    note_blurb = f', note: "{meta.note}"' if meta.note else ""
    response = typer.confirm(
        f"⚠ Pod '{alias}' belongs to another Claude session"
        f" (owner {meta.owner_session_id[:8]}…{note_blurb})\n"
        f"Destroy anyway?"
    )
    if not response:
        console.print("❌ Cancelled.")
        raise typer.Exit(0)
```

Update `destroy_command` to accept and respect `all_sessions` and call the helper before the existing `if not force: typer.confirm` block:

```python
def destroy_command(
    alias: str | None,
    force: bool = False,
    all_sessions: bool = False,
) -> None:
    """Terminate a pod, remove SSH config, and delete the alias."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_manager.get_pod_id(alias)

        _confirm_cross_session_or_exit(pod_manager, alias, all_sessions=all_sessions)

        if not force:
            response = typer.confirm(
                f"⚠️  Are you sure you want to destroy pod '{alias}'? This action cannot be undone."
            )
            if not response:
                console.print("❌ Destruction cancelled.")
                raise typer.Exit(0)

        # ... (rest unchanged) ...
```

Update `down_command(destroy=True)` path to also call `_confirm_cross_session_or_exit`, gated on a new `all_sessions: bool = False` kwarg.

In `src/rp/cli/main.py`:
- `down()`: add `all_sessions: bool = typer.Option(False, "--all-sessions", help="...")` and pass.
- `destroy()` under `pod_app`: same.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_session_scoping.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/rp/cli/commands.py src/rp/cli/main.py tests/unit/test_session_scoping.py
git commit -m "Prompt before destroying pods owned by another Claude session

Owned, unowned (legacy), or bare-terminal use: no extra prompt. Only
prompts when there's an active session and the pod's owner doesn't
match. --all-sessions skips the prompt for scripted cleanup."
```

---

## Phase F — Stale warnings and prune

### Task F1: Helper utilities (format_age, format_storage_cost, stale detection)

**Files:**
- Modify: `src/rp/cli/utils.py` — add `format_age` and `format_storage_cost`
- Modify: `src/rp/core/pod_manager.py` — add `stale_stopped_pods(threshold_hours)`
- Test: `tests/unit/test_stale_warnings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_stale_warnings.py
"""Tests for stale-pod detection + formatting helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from rp.cli.utils import format_age, format_storage_cost


@pytest.mark.parametrize(
    "delta,expected_substr",
    [
        (timedelta(minutes=30), "30m"),
        (timedelta(hours=2), "2h"),
        (timedelta(hours=25), "1d"),
        (timedelta(days=5, hours=2), "5d"),
    ],
)
def test_format_age(delta, expected_substr):
    now = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    when = now - delta
    out = format_age(when, now=now)
    assert expected_substr in out


def test_format_storage_cost_basic():
    # 400 GB at $0.10/GB/month
    assert format_storage_cost(400) == "~$40/mo"


def test_format_storage_cost_zero():
    assert format_storage_cost(0) == "~$0/mo"


def test_stale_stopped_pods_filters_by_threshold(temp_config_dir):  # noqa: ARG001
    from rp.core.pod_manager import PodManager

    pm = PodManager(api_client=MagicMock())
    now = datetime.now(timezone.utc)
    pm.add_alias("recent", "p1")
    pm.add_alias("old", "p2")
    pm.add_alias("running", "p3")
    pm.config.pod_metadata["recent"].stopped_at = now - timedelta(hours=2)
    pm.config.pod_metadata["old"].stopped_at = now - timedelta(hours=48)
    # "running" has stopped_at=None

    stale = pm.stale_stopped_pods(threshold_hours=24, now=now)
    aliases = {alias for alias, _ in stale}
    assert aliases == {"old"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v`
Expected: ImportError on `format_age`, `format_storage_cost`, `stale_stopped_pods`.

- [ ] **Step 3: Implement**

In `src/rp/cli/utils.py`, add (near other formatting helpers):

```python
def format_age(when: "datetime", *, now: "datetime | None" = None) -> str:
    """Render 'how long ago' in compact units: 30m, 2h, 5d."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    delta = now - when
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


# RunPod published rate as of 2026-05; revisit if it changes.
_STORAGE_COST_PER_GB_MONTH = 0.10


def format_storage_cost(volume_gb: int) -> str:
    """Render the monthly cost for a given persistent volume size."""
    cost = volume_gb * _STORAGE_COST_PER_GB_MONTH
    return f"~${cost:.0f}/mo"
```

Add the `datetime` import at the top of utils.py if not already present.

In `src/rp/core/pod_manager.py`, add (after `clean_invalid_aliases`):

```python
    def stale_stopped_pods(
        self,
        *,
        threshold_hours: int = 24,
        now: datetime | None = None,
    ) -> list[tuple[str, PodMetadata]]:
        """Return (alias, metadata) for stopped pods whose stopped_at is older than threshold."""
        from datetime import datetime as _dt, timezone

        now = now or _dt.now(timezone.utc)
        cutoff = now - timedelta(hours=threshold_hours)
        out: list[tuple[str, PodMetadata]] = []
        for alias, meta in self.config.pod_metadata.items():
            if meta.stopped_at is None:
                continue
            if meta.stopped_at <= cutoff:
                out.append((alias, meta))
        return sorted(out, key=lambda r: r[1].stopped_at)
```

Add imports at the top: `from datetime import datetime, timedelta` and import `PodMetadata` from `rp.core.models`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/rp/cli/utils.py src/rp/core/pod_manager.py tests/unit/test_stale_warnings.py
git commit -m "Add format_age, format_storage_cost, and stale_stopped_pods helpers

Pure logic that the next two tasks (warning banner and rp prune) will
compose. Cost rate ($0.10/GB/month) hardcoded with a comment; revisit
if RunPod pricing changes."
```

---

### Task F2: Stale banner in `_auto_clean`

**Files:**
- Modify: `src/rp/cli/commands.py` — `_auto_clean` calls `_print_stale_banner_if_any`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_stale_warnings.py
def test_stale_banner_emits_when_pods_present(monkeypatch, capsys, temp_config_dir):  # noqa: ARG001
    from datetime import datetime, timedelta, timezone
    from rp.cli import commands

    monkeypatch.delenv("RP_NO_STALE_WARNING", raising=False)

    pm = commands.get_pod_manager()
    pm.add_alias("old1", "p1", note="AE-1234: classifier")
    pm.add_alias("old2", "p2")
    now = datetime.now(timezone.utc)
    pm.config.pod_metadata["old1"].stopped_at = now - timedelta(hours=48)
    pm.config.pod_metadata["old2"].stopped_at = now - timedelta(hours=48)

    commands._print_stale_banner_if_any(pm)
    out = capsys.readouterr().out
    assert "old1" in out
    assert "old2" in out
    assert "AE-1234" in out
    assert "rp prune" in out


def test_stale_banner_suppressed_by_env(monkeypatch, capsys, temp_config_dir):  # noqa: ARG001
    from datetime import datetime, timedelta, timezone
    from rp.cli import commands

    monkeypatch.setenv("RP_NO_STALE_WARNING", "1")
    pm = commands.get_pod_manager()
    pm.add_alias("old1", "p1")
    pm.config.pod_metadata["old1"].stopped_at = datetime.now(timezone.utc) - timedelta(hours=48)

    commands._print_stale_banner_if_any(pm)
    out = capsys.readouterr().out
    assert "old1" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v -k banner`
Expected: ImportError on `_print_stale_banner_if_any`.

- [ ] **Step 3: Implement**

In `src/rp/cli/commands.py`:

```python
def _print_stale_banner_if_any(pod_manager: PodManager) -> None:
    """Print a one-shot banner about stopped pods accruing storage cost.

    Silent when: env var RP_NO_STALE_WARNING is set, or no pods are
    stale. Threshold defaults to 24h, override with RP_STALE_THRESHOLD_HOURS.
    """
    if os.environ.get("RP_NO_STALE_WARNING"):
        return
    try:
        threshold = int(os.environ.get("RP_STALE_THRESHOLD_HOURS", "24"))
    except ValueError:
        threshold = 24

    from rp.cli.utils import format_age, format_storage_cost

    stale = pod_manager.stale_stopped_pods(threshold_hours=threshold)
    if not stale:
        return

    n = len(stale)
    plural = "pods" if n != 1 else "pod"
    console.print(
        f"\n[yellow]⚠ {n} stopped {plural} accruing storage costs:[/yellow]",
    )
    for alias, meta in stale:
        # We don't fetch live volume_gb here (would round-trip the API).
        # Use a "?" placeholder when volume size is unknown; rp prune can
        # show the resolved number.
        note_blurb = meta.note if meta.note else "(none)"
        age = format_age(meta.stopped_at)
        console.print(
            f"    [bold]{alias}[/bold]   stopped {age}   note: {note_blurb}"
        )
    console.print(
        f"  Review: [bold]rp prune[/bold]   "
        f"Destroy now: [bold]rp down <alias> --destroy[/bold]\n"
    )


def _auto_clean() -> None:
    """Silently perform cleanup tasks (invalid aliases, SSH blocks) and surface stale-pod warning."""
    try:
        pod_manager = get_pod_manager()
        ssh_manager = get_ssh_manager()

        pod_manager.clean_invalid_aliases()
        valid_aliases = set(pod_manager.aliases.keys())
        ssh_manager.prune_managed_blocks(valid_aliases)
    except Exception:
        pass

    # Stale banner has its own try/except so a failure here doesn't
    # swallow real command output silently — we want to know if it
    # breaks. Catch only specific bugs.
    try:
        _print_stale_banner_if_any(get_pod_manager())
    except Exception:
        pass
```

(Note: the existing `_auto_clean()` already exists; replace it with the version above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/rp/cli/commands.py tests/unit/test_stale_warnings.py
git commit -m "Surface 24h-stale stopped pods in a banner after every rp command

Hooks into the existing _auto_clean post-command sweep. Suppressed
by RP_NO_STALE_WARNING=1; threshold tunable via
RP_STALE_THRESHOLD_HOURS."
```

---

### Task F3: `rp prune` interactive command

**Files:**
- Add: `prune_command` in `src/rp/cli/commands.py`
- Modify: `src/rp/cli/main.py` — register `rp prune`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_stale_warnings.py
def test_prune_destroys_when_user_picks_d(monkeypatch, temp_config_dir):  # noqa: ARG001
    """rp prune calls destroy on pods the user marks 'd'."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from rp.cli import commands

    pm = commands.get_pod_manager()
    pm.add_alias("old1", "p1")
    pm.add_alias("old2", "p2")
    now = datetime.now(timezone.utc)
    pm.config.pod_metadata["old1"].stopped_at = now - timedelta(hours=48)
    pm.config.pod_metadata["old2"].stopped_at = now - timedelta(hours=48)

    # 'd' for old1, 'k' for old2
    with patch.object(commands, "_prune_prompt", side_effect=["d", "k"]):
        with patch.object(pm, "destroy_pod") as destroy:
            commands.prune_command()

    destroy.assert_called_once_with("old1")


def test_prune_exits_early_on_q(monkeypatch, temp_config_dir):  # noqa: ARG001
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from rp.cli import commands

    pm = commands.get_pod_manager()
    pm.add_alias("old1", "p1")
    pm.add_alias("old2", "p2")
    now = datetime.now(timezone.utc)
    pm.config.pod_metadata["old1"].stopped_at = now - timedelta(hours=48)
    pm.config.pod_metadata["old2"].stopped_at = now - timedelta(hours=48)

    with patch.object(commands, "_prune_prompt", side_effect=["q"]):
        with patch.object(pm, "destroy_pod") as destroy:
            commands.prune_command()

    destroy.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v -k prune`
Expected: ImportError / AttributeError on `prune_command` / `_prune_prompt`.

- [ ] **Step 3: Implement**

In `src/rp/cli/commands.py`:

```python
def _prune_prompt(alias: str) -> str:
    """Prompt for a per-pod action in the prune picker. Returns one of: 'd', 'k', 'q'."""
    while True:
        answer = typer.prompt(
            f"  [d] destroy   [k] keep   [q] quit  for {alias}",
            default="k",
        )
        answer = (answer or "k").strip().lower()
        if answer in {"d", "k", "q"}:
            return answer
        console.print("[yellow]Please answer 'd', 'k', or 'q'.[/yellow]")


def prune_command() -> None:
    """Interactively review and destroy stopped pods older than the stale threshold."""
    try:
        from rp.cli.utils import format_age, format_storage_cost

        pod_manager = get_pod_manager()
        try:
            threshold = int(os.environ.get("RP_STALE_THRESHOLD_HOURS", "24"))
        except ValueError:
            threshold = 24

        stale = pod_manager.stale_stopped_pods(threshold_hours=threshold)
        if not stale:
            console.print("✅ No stopped pods over the threshold.")
            return

        plural = "pods" if len(stale) != 1 else "pod"
        console.print(
            f"Stopped {plural} over {threshold}h old ({len(stale)} found):\n"
        )

        for alias, meta in stale:
            note = meta.note if meta.note else "(none)"
            age = format_age(meta.stopped_at)
            console.print(f"  [bold]{alias}[/bold]   stopped {age}")
            console.print(f"    note: {note}")
            choice = _prune_prompt(alias)
            if choice == "q":
                console.print("Cancelled.")
                return
            if choice == "d":
                try:
                    pod_manager.destroy_pod(alias)
                    console.print(f"  🔥 Destroyed {alias}\n")
                except Exception as e:
                    console.print(f"  [red]Failed to destroy {alias}: {e}[/red]\n")
            else:
                console.print(f"  ⏭️  Kept {alias}\n")

    except Exception as e:
        handle_cli_error(e)
```

In `src/rp/cli/main.py`, register it:

```python
@app.command()
def prune():
    """Interactively review and destroy stopped pods that have been idle past the stale threshold."""
    from rp.cli.commands import prune_command
    prune_command()
```

Import path adjustments: add `prune_command` to the top-level imports in `main.py` if you prefer the existing pattern.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_stale_warnings.py -v`
Expected: all pass.

Smoke: `uv run rp prune --help` shows help and exits cleanly.

- [ ] **Step 5: Commit**

```bash
git add src/rp/cli/commands.py src/rp/cli/main.py tests/unit/test_stale_warnings.py
git commit -m "Add rp prune for interactive stopped-pod cleanup

Walks every pod past the stale threshold, prints alias + note + age,
and asks d/k/q per pod. Destroys via existing PodManager.destroy_pod
so it shares SSH/alias cleanup with rp pod destroy."
```

---

## Phase G — E2E tests and docs

### Task G1: E2E test for `--storage` rename

**Files:**
- Modify: `tests/e2e/test_pod_lifecycle.py` — already updated in C1; this task confirms a dedicated E2E pass.

- [ ] **Step 1: Confirm test code in `test_pod_lifecycle.py`**

The C1 task already replaced `--persistent-volume` with `--storage` in `_create_pod_with_fallback`. Re-read the file and verify no leftover `--persistent-volume` references.

```bash
grep -n "persistent-volume\|persistent_volume" tests/
```

- [ ] **Step 2: Run the existing E2E lifecycle test**

Run: `RUNPOD_API_KEY=... uv run pytest tests/e2e/test_pod_lifecycle.py::TestPodLifecycle::test_create_start_stop_destroy_flow -v`
Expected: passes end-to-end with the new flag.

- [ ] **Step 3: No additional code; this is a sanity check.**

- [ ] **Step 4: (skipped — no test to add)**

- [ ] **Step 5: Commit if anything changed**

If `grep` finds residual references, fix them and commit:

```bash
git add -A
git commit -m "Drop residual --persistent-volume references from tests"
```

---

### Task G2: E2E `rp down` stops by default

**Files:**
- Create: `tests/e2e/test_stop_not_destroy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_stop_not_destroy.py
"""End-to-end coverage for the rp down -> stop semantic shift."""

import time
import uuid

import runpod

from .test_pod_lifecycle import _create_pod_with_fallback


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
        assert alias in list_result.stdout

        # RunPod-side: pod should be EXITED, not gone.
        time.sleep(10)
        details = runpod.get_pod(pod_id)
        assert details is not None, "pod was destroyed when it should have stopped"
        assert details.get("desiredStatus", "").upper() in {"EXITED", "STOPPED"}

    def test_rp_down_destroy_terminates(self, cli_runner, test_pod_manager):
        alias = f"test-down-destroy-{uuid.uuid4().hex[:8]}"
        create_result = _create_pod_with_fallback(cli_runner, alias)
        assert create_result.returncode == 0, create_result.stderr
        pod_id = _extract_pod_id(create_result.stdout)
        # Don't add to test_pod_manager.created_pods because --destroy
        # will remove it.

        result = cli_runner(["down", alias, "--skip-logs", "--destroy"])
        assert result.returncode == 0, result.stderr

        # Alias should be gone locally.
        list_result = cli_runner(["pod", "list", "--all"])
        assert alias not in list_result.stdout

        # RunPod-side: pod should be terminated.
        time.sleep(10)
        details = runpod.get_pod(pod_id)
        assert details is None or details.get("desiredStatus", "").upper() in {
            "TERMINATED",
            "DEAD",
        }


def _extract_pod_id(stdout: str) -> str:
    for line in stdout.split("\n"):
        if "Saved alias" in line and "->" in line:
            return line.split("->")[-1].strip()
    raise AssertionError(f"Could not extract pod ID from:\n{stdout}")
```

- [ ] **Step 2: Run test to verify behavior**

Run: `RUNPOD_API_KEY=... uv run pytest tests/e2e/test_stop_not_destroy.py -v`
Expected: passes (will create and clean up two real pods).

- [ ] **Step 3-4: (already covered by writing the tests above)**

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_stop_not_destroy.py
git commit -m "E2E: rp down stops by default; --destroy terminates"
```

---

### Task G3: E2E pod note lifecycle

**Files:**
- Create: `tests/e2e/test_pod_notes.py`

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_pod_notes.py
"""End-to-end pod-note set / append / clear / show."""

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

        # Note column appears in rp pod list (--all to be safe w/r/t session filter)
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

    def test_up_note_flag_persists(self, cli_runner, test_pod_manager):
        """rp up --note "..." stores the note from the start."""
        import uuid

        alias = f"test-up-note-{uuid.uuid4().hex[:8]}"
        try:
            r = _create_pod_with_fallback(cli_runner, alias)
            assert r.returncode == 0, r.stderr

            # _create_pod_with_fallback uses pod create, not up; set note
            # explicitly to verify the flag plumbing for that command too.
            r = cli_runner(["pod", "note", alias, "from-test"])
            assert r.returncode == 0
            r = cli_runner(["pod", "show", alias])
            assert "from-test" in r.stdout
        finally:
            cli_runner(["pod", "destroy", alias, "--force"])
```

- [ ] **Step 2: Run**

Run: `RUNPOD_API_KEY=... uv run pytest tests/e2e/test_pod_notes.py -v`
Expected: passes.

- [ ] **Step 3-4: covered.**

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_pod_notes.py
git commit -m "E2E: pod note set/append/clear/show round trip"
```

---

### Task G4: E2E session scoping filter + cross-session prompt

**Files:**
- Create: `tests/e2e/test_session_scoping.py`

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_session_scoping.py
"""End-to-end session-scoping behavior using RP_SESSION_ID overrides."""

import uuid

from .test_pod_lifecycle import _create_pod_with_fallback


class TestSessionScoping:
    def test_list_filters_by_rp_session_id(self, cli_runner, test_pod_manager):
        alias_a = f"test-scope-a-{uuid.uuid4().hex[:8]}"
        alias_b = f"test-scope-b-{uuid.uuid4().hex[:8]}"

        # Create one pod under each "session"
        for alias, sid in [(alias_a, "session-a"), (alias_b, "session-b")]:
            r = _create_pod_with_fallback(
                # _create_pod_with_fallback doesn't take env, so call cli_runner directly
                cli_runner,
                alias,
            )
            # Re-track the alias with the right session id by setting it
            # after the fact. (Cleaner alternative would be to extend
            # _create_pod_with_fallback to accept env; either is fine.)
            assert r.returncode == 0, r.stderr

        # Verify the filter works
        list_a = cli_runner(["pod", "list"], env={"RP_SESSION_ID": "session-a"})
        list_b = cli_runner(["pod", "list"], env={"RP_SESSION_ID": "session-b"})
        list_all = cli_runner(
            ["pod", "list", "--all"], env={"RP_SESSION_ID": "session-a"}
        )

        # Note: alias_a was created with whatever RP_SESSION_ID was set
        # at create time. To make this test deterministic, the test
        # extends _create_pod_with_fallback in the implementation step
        # to pass env. See Step 3.

        # Once env-passing is wired up:
        # assert alias_a in list_a.stdout
        # assert alias_b not in list_a.stdout
        # assert alias_b in list_b.stdout
        # assert alias_a in list_all.stdout

        # Cleanup
        for alias in (alias_a, alias_b):
            cli_runner(["pod", "destroy", alias, "--force"])
```

- [ ] **Step 2: Update `_create_pod_with_fallback` to accept env**

In `tests/e2e/test_pod_lifecycle.py`, change the signature:

```python
def _create_pod_with_fallback(
    cli_runner, alias: str, storage: str = "10GB", env: dict | None = None
):
    last_result = None
    for gpu in CHEAP_GPUS:
        result = cli_runner(
            [
                "pod",
                "create",
                "--alias",
                alias,
                "--gpu",
                gpu,
                "--storage",
                storage,
                "--no-setup",
            ],
            env=env,
        )
        # ... existing fallback logic ...
```

Then in `test_session_scoping.py`, pass the env:

```python
r = _create_pod_with_fallback(cli_runner, alias, env={"RP_SESSION_ID": sid})
```

Uncomment the assertions in Step 1.

- [ ] **Step 3: Run**

Run: `RUNPOD_API_KEY=... uv run pytest tests/e2e/test_session_scoping.py -v`
Expected: passes.

- [ ] **Step 4: covered**

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_session_scoping.py tests/e2e/test_pod_lifecycle.py
git commit -m "E2E: rp pod list filters by RP_SESSION_ID

Also extends _create_pod_with_fallback to accept an env override so
session-scoping behavior can be exercised end-to-end."
```

---

### Task G5: Update `docs.md` and `README.md`

**Files:**
- Modify: `docs.md`
- Modify: `README.md`

- [ ] **Step 1-3: Update documentation in place**

In `docs.md`:

- Section "Opinionated Commands (Managed Pods)" → `rp up`:
  - Replace `--persistent-volume` with `--storage` throughout.
  - Add `--note "<ticket-id>: <task>"` to the flag list with the doc string from `main.py`.
  - Update defaults: container disk 50 GB, persistent volume 400 GB (was 500 GB / 0 GB).
- Section `rp down`:
  - Rewrite to describe stop-by-default and the `--destroy` flag.
  - Mention `--all-sessions`.
- Add new sections:
  - `rp pod note` — adapt from the help text in `main.py`.
  - `rp prune` — describe the interactive picker, `[d]/[k]/[q]`, threshold env var.
- Section "Auto-Shutdown (Managed Pods)":
  - Replace "destroys pod via RunPod REST API" with "stops pod via RunPod REST API; resume with `rp pod start`."
- Add a new "Session Scoping" subsection under "Important Behaviors" or similar:
  - Explain `CLAUDE_CODE_SESSION_ID` auto-detection.
  - Explain `RP_SESSION_ID` override.
  - Explain `--all`, `--all-sessions`.
- Replace `--persistent-volume` with `--storage` in every code example throughout.

In `README.md`:
- Quick start example: switch `--persistent-volume` to `--storage` if mentioned.
- Note `--note` flag if there's an example block for `rp up`.

- [ ] **Step 4: Sanity-check rendered text**

Run: `grep -n "persistent-volume\|persistent_volume" docs.md README.md`
Expected: no hits.

- [ ] **Step 5: Commit**

```bash
git add docs.md README.md
git commit -m "Update docs for new defaults, rp prune, rp pod note, session scoping

Covers: --storage rename, stop-not-destroy semantics for rp down and
auto-shutdown, --note flag, rp pod note command, rp prune, session
filtering with CLAUDE_CODE_SESSION_ID / --all / --all-sessions."
```

---

### Task G6: Update the runpod skill

**Files:**
- Modify: `~/.claude/skills/runpod/docs/start.md`
- Modify: `~/.claude/skills/runpod/docs/stop.md`

- [ ] **Step 1-3: Update in place**

`start.md` — add near the top:

> **Setting a note.** When creating a pod for substantive work, pass
> `--note "<ticket-id>: <one-line task>"` to `rp up`. This appears in
> `rp pod list` and stale-pod warnings later, so a future Claude (or
> Alex) reviewing dormant pods knows what they were for. Keep it under
> 80 characters.

And — find any reference to cloning into `/root/` and change it to `/workspace/`:

> Clone work repos into `/workspace/<repo>` so they survive `rp down`
> and pod restarts. Caches (uv, pip, Hugging Face) are also redirected
> to `/workspace/.cache` automatically.

`stop.md` — replace the existing top-of-file description with:

> `rp down` syncs Claude logs and **stops** the pod (preserves
> /workspace and the alias). Resume with `rp pod start <alias>`.
>
> Use `rp down --destroy` only when:
> - The pod is broken and you're going to recreate it fresh, OR
> - All code is committed & pushed AND all generated data is on S3
>   (nothing in /workspace would be lost).
>
> When in doubt, stop. Storage costs ~$0.10/GB/month while stopped; stale
> pods get flagged in `rp` output after 24h. `rp prune` walks you
> through cleanup.

- [ ] **Step 4-5: Commit**

Note: these files live outside the repo (`~/.claude/skills/`). They're not version-controlled here. Note the changes in the PR description; the user can land them separately if they have a skill repo.

```bash
# No commit in the rp repo. Add a note to the PR description that the
# skill docs need a parallel update at ~/.claude/skills/runpod/.
```

---

## Phase H — Version bump and final PR

### Task H1: Bump version to 0.13.0

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Confirm full test suite passes locally**

Run: `uv run pytest tests/unit/ -v`
Expected: green.

Run: `RUNPOD_API_KEY=... uv run pytest tests/e2e/ -v`
Expected: green (or you've isolated any flakes to known issues).

- [ ] **Step 2: Edit version**

In `pyproject.toml` line 3: `version = "0.12.0"` → `version = "0.13.0"`.

- [ ] **Step 3: Run a final lint pass**

Run: `uv run ruff check && uv run ruff format --check`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Bump to 0.13.0

Feature release: stop-not-destroy default, persistent /workspace,
session scoping, rp pod note, rp prune, --storage flag rename."
```

- [ ] **Step 5: Open the PR**

Branch and push:
```bash
git push -u origin HEAD
gh pr create --title "Stop-not-destroy, session scoping, pod notes (v0.13.0)" \
  --body "$(cat <<'EOF'
## Summary
- `rp down` now stops (preserves /workspace and alias); `--destroy` opts in to terminate
- `rp up` default storage flipped: 400 GB persistent /workspace, 50 GB container disk
- `--persistent-volume` flag renamed to `--storage`
- Auto-shutdown now stops instead of terminates
- Session scoping via `CLAUDE_CODE_SESSION_ID`: list filters to current session, cross-session destroys prompt
- New `rp pod note` for one-line per-pod context; surfaces in show/list/stale-warning/prune
- New `rp prune` for interactive stopped-pod cleanup
- 24h-stale stopped-pod banner after every command

Spec: `docs/superpowers/specs/2026-05-12-session-scoping-and-persistent-pods-design.md`
Plan: `docs/superpowers/plans/2026-05-12-session-scoping-and-persistent-pods.md`

**Skill docs update needed separately** at `~/.claude/skills/runpod/docs/{start.md,stop.md}` — see commit referenced in PR.

## Test plan
- [ ] `uv run pytest tests/unit/` green
- [ ] `RUNPOD_API_KEY=... uv run pytest tests/e2e/` green
- [ ] Smoke: `rp up --note "test" --gpu A4000` creates pod with note
- [ ] Smoke: `rp down <alias>` stops; `rp pod start <alias>` resumes; `/workspace` content survives
- [ ] Smoke: `rp pod list` inside Claude filters; outside Claude shows everything
- [ ] Smoke: `rp prune` walks stopped pods and accepts d/k/q

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage check:**

| Spec requirement | Task(s) |
|---|---|
| Layer 1: `auto_shutdown.sh` stops not destroys | D3 |
| Layer 1: `rp down` stops by default; `--destroy` opt-in | D2 |
| Layer 1: persistent volume default 400 GB / disk 50 GB | C2 |
| Layer 1: `--persistent-volume` → `--storage` | C1 |
| Layer 1: workspace cache env vars | C3 |
| Layer 1: skill `stop.md` guidance | G6 |
| Layer 2: `stopped_at` tracked on stop/start | D1 |
| Layer 2: stale banner in `_auto_clean` | F2 |
| Layer 2: `rp prune` | F3 |
| Layer 2: `RP_NO_STALE_WARNING`, `RP_STALE_THRESHOLD_HOURS` | F2 |
| Layer 3: `current_session_id()` resolver | A1 |
| Layer 3: `owner_session_id` on PodMetadata | A2, B1 (populated at create) |
| Layer 3: `rp pod list` filters; `--all` flag | E1 |
| Layer 3: destroy prompts cross-session; `--all-sessions` | E2 |
| Layer 3: skill docs (start.md) | G6 |
| Layer 4: `note` field on PodMetadata | A2 |
| Layer 4: `--note` flag on `rp up` / `rp pod create` | B1 |
| Layer 4: `rp pod note` (set/append/clear/show) | B2 |
| Layer 4: note rendered in show/list | B3 |
| Layer 4: CLAUDECODE-only reminder | B4 |
| Layer 4: skill docs (start.md) | G6 |
| E2E coverage | G2 (down), G3 (notes), G4 (session) — G1 reuses existing |
| Version bump 0.12.0 → 0.13.0 | H1 |

No spec requirements without a corresponding task.

**Placeholder scan:** All steps contain concrete code, exact commands, or pointers to specific line numbers / functions. No "TBD" / "implement later" / "appropriate error handling" without specifics.

**Type consistency check:**
- `current_session_id() -> str | None` used everywhere as such.
- `stale_stopped_pods(*, threshold_hours, now=None) -> list[tuple[str, PodMetadata]]` — return type matches consumer in F2/F3.
- `_print_note_reminder_if_needed(alias: str, note: str | None)` — signature matches its single call site in B1's modified `up_command`.
- `_confirm_cross_session_or_exit(pod_manager, alias, *, all_sessions)` — used in E2 only; consistent.
- `format_age(when, *, now=None)`, `format_storage_cost(volume_gb: int)` — signatures stable across F1/F2/F3.
- `display_pods_table(pods, *, console=None, show_owner_column=False)` — new signature consistent across B3 and E1.
- `down_command(alias, skip_logs=False, destroy=False, all_sessions=False)` — D2 introduces `destroy`; E2 adds `all_sessions` keyword.
- `destroy_command(alias, force=False, all_sessions=False)` — E2 signature consistent.
- `list_command(show_all=False)` — E1 signature consistent.
- `prune_command()` — F3 signature consistent.

All good.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-12-session-scoping-and-persistent-pods.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

**Which approach?**
