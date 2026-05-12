# Session-scoped pods, stop-not-destroy, and pod notes

**Date:** 2026-05-12
**Status:** Approved for planning

## Problem

Two failure modes have been hitting Alex repeatedly when multiple Claude Code
sessions touch the same `rp` install:

1. **Alias recycling.** A pod is auto-destroyed (idle shutdown) or manually
   destroyed; auto-numbering reuses its `{i}` slot for a new, unrelated pod.
   The original Claude session, when it resumes, sees a pod with the expected
   alias but it's a different pod — leading to confusion about state.

2. **Cross-session overreach.** Claude #1 lists pods, sees an alias it didn't
   create ("rogue `soo_alex_4` appeared"), and destroys it as "cleanup,"
   wiping out work being done by Claude #2 or by Alex directly.

Both stem from the same root: `rp` has no concept of *who owns this pod* and
no default behavior that would preserve work across the gap between "I think
I'm done with it" and "it's actually gone."

## Goals

- **Survive destruction:** A pod going idle should not lose the user's work
  by default.
- **Avoid alias recycling:** While work is preserved, the alias slot stays
  occupied, so auto-numbering naturally avoids reuse.
- **Soft cross-session isolation:** Sessions see and operate on their own
  pods by default; destructive ops on another session's pods require a
  conscious confirmation.
- **Stay usable without Claude:** Every behavior degrades cleanly when no
  session is active (bare terminal, scripts, CI).
- **Stay simple to operate:** No new long-running daemon, no hook-installation
  step, no separate config file format.

## Non-goals

- **Network-volume-first storage model.** Considered and deferred. Region
  pinning hurts GPU availability too much; the CPU-bridge workaround is too
  complex. Existing `--network-volume` flag continues to work; we may
  revisit once per-pod-volume defaults are battle-tested.
- **Long-form note editor.** `rp pod note --editor` or similar is out of
  scope. Notes are one-line strings, set at creation or via a short CLI.
- **Cross-session adoption / handoff commands.** No `rp adopt`, no
  `rp pod transfer`. If you legitimately need to operate on another
  session's pod, the confirmation prompt + `--force` is the entire
  escape hatch.
- **Auto-derived note content.** No git-branch or cwd magic feeding into
  the `note` field. The note is explicit-intent only; auxiliary auto-data
  is YAGNI for now.

---

## Design

The design has four layers. Each is independent in code but they're
designed together because they share data structures and reinforce each
other's value.

### Layer 1 — Stop-not-destroy + persistent `/workspace`

**Behavioral changes:**

- `auto_shutdown.sh` (the cron-driven idle killer) currently sends
  `DELETE /v1/pods/<id>`. Change to `POST /v1/pods/<id>/stop`. Pod stays in
  the user's account, alias is preserved, per-pod persistent volume keeps
  its contents. The user resumes with `rp pod start <alias>`.
- `rp down` semantics flip: it now syncs Claude logs and **stops** the pod
  (instead of terminating). The destroy-and-forget path moves to
  `rp down --destroy`.

**Storage defaults:**

| Knob | Old default | New default | Flag |
|---|---|---|---|
| Persistent volume at `/workspace` | `0GB` (none) | `400GB` | `--storage SIZE` |
| Container disk | `500GB` | `50GB` | `--disk SIZE` |
| Network volume (existing shared) | unset | unset | `--network-volume ID` |

The persistent volume is sized to accommodate model weights (a typical
70B-parameter model is ~140 GB) and reasonable scratch space, while
staying small enough that a forgotten stopped pod is a $40/month problem,
not a $200 one. The container disk shrinks because everything valuable
now lives on the persistent volume.

**Flag rename:** `--persistent-volume SIZE` is replaced by `--storage SIZE`
across `rp up`, `rp pod create`, and `rp template create`. The old flag is
removed (not aliased) — this is pre-1.0 software and the project's
versioning convention treats this kind of breaking rename as a minor bump.

**Workspace conventions** (injected into `/etc/profile.d/rp-env.sh` by
`PodSetup.install_tools`):

```bash
export XDG_CACHE_HOME=/workspace/.cache
export UV_CACHE_DIR=/workspace/.cache/uv
export PIP_CACHE_DIR=/workspace/.cache/pip
export HF_HOME=/workspace/.cache/huggingface
```

Existing `chown -R user:user /workspace` in `_CREATE_USER_SCRIPT` already
handles ownership. The `runpod` skill's `start.md` is updated to direct
`git clone` into `/workspace/<repo>` rather than `/root/<repo>`.

**Migration:** Existing templates with `storage_spec: "0GB"` continue to
work — `0GB` means "no persistent volume," same as today. The default in
`get_default_templates()` flips from `"0GB"` to `"400GB"` and
`container_disk_spec` from `"500GB"` to `"50GB"`. Users with existing
custom templates in `pods.json` are unaffected.

**Guidance update** (added to `~/.claude/skills/runpod/docs/stop.md`):

> `rp down` stops the pod (preserves /workspace and the alias). Use
> `rp down --destroy` only when:
> - The pod is broken and you're going to recreate it fresh, OR
> - All code is committed & pushed AND all generated data is on S3
>   (nothing in /workspace would be lost).
>
> When in doubt, stop. Storage costs ~$0.10/GB/month while stopped; stale
> pods get flagged in `rp` output after 24h.

### Layer 2 — Stale stopped-pod warnings

**Tracking:** Add `stopped_at: datetime | None` to `PodMetadata`. Set in
`PodManager.stop_pod` and on the new `rp down` stop path; cleared in
`start_pod`.

**Warning banner:** Hook into the existing `_auto_clean()` (which runs
after every command in `cli/commands.py`). After cleanup, scan for pods
where `status == STOPPED` and `stopped_at` is more than **24 hours** ago.
If any exist, print to stderr:

```
⚠ 2 stopped pods accruing storage costs:
    ast_alex_2    stopped 5d ago   400 GB   ~$40/mo   note: AE-1234: fine-tune classifier
    ast_alex_3    stopped 8d ago   400 GB   ~$40/mo   note: (none)
  Review: rp prune    Destroy now: rp down <alias> --destroy
```

Suppressed when `RP_NO_STALE_WARNING=1` is set. Threshold can be tuned via
`RP_STALE_THRESHOLD_HOURS` for power users (default `24`).

**Cost calculation:** Hard-coded `$0.10/GB/month` for per-pod persistent
volume. This is RunPod's published rate as of design time; if it changes
substantially we update the constant. Not worth pulling a live price.

**`rp prune` command:** New interactive command. Walks every stopped pod
older than the threshold and prompts:

```
Stopped pods over 24h old (3 found):

  ast_alex_2   stopped 2d ago, 400 GB, ~$40/mo
    note: AE-1234: fine-tune classifier on cleaned dataset
    [d] destroy   [k] keep   [q] quit

  ast_alex_3   stopped 8d ago, 400 GB, ~$40/mo
    note: (none)
    [d] destroy   [k] keep   [q] quit
```

`destroy` calls `pod_manager.destroy_pod(alias)`. `keep` leaves it alone
for this run (no state persisted — fresh 24h+ pods will reappear in the
next prune). `quit` exits the picker early.

### Layer 3 — Medium session scoping

**Session resolution** (in a new helper, e.g. `rp.core.session.current_session_id()`):

```python
def current_session_id() -> str | None:
    if explicit := os.environ.get("RP_SESSION_ID"):
        return explicit
    if claude := os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return claude
    return None
```

That's the entire mechanism. Verified empirically: Claude Code
(≥ 2.1.139) sets `CLAUDE_CODE_SESSION_ID` in the env that subprocess
tools inherit, alongside `CLAUDECODE=1`. Older Claude Code versions
without this variable just see `None` — the safe fallback.

**Data model:** Add `owner_session_id: str | None` to `PodMetadata`,
populated at pod-creation time via `current_session_id()`. Existing pods
in `pods.json` will deserialize with `owner_session_id = None`, which
means "visible to everyone" — backward compatible.

**Behavior matrix** (where "current session" means `current_session_id()`
returned a non-None value, and "owned" means
`metadata.owner_session_id == current_session_id()`):

| Operation | No current session | Current session, target owned | Current session, target unowned |
|---|---|---|---|
| `rp pod list` | All pods, no filter | Owned only; footer `+N pods from other sessions` | n/a (target is the list itself) |
| `rp pod list --all` | All pods, no filter | All pods, with `owner` column | All pods, with `owner` column |
| `rp pod start`, `rp run`, `rp shell`, `rp claude`, `rp scp` | Work as today | Work | Work (no prompt — using ≠ destroying) |
| `rp pod stop`, `rp down` (stop path) | Work as today | Work | Work (no prompt — stopping is reversible) |
| `rp pod destroy`, `rp down --destroy` | Work as today | Work | **Interactive confirmation** (see below) |

Confirmation prompt format:

```
⚠ Pod 'ast_alex_3' belongs to another Claude session
  (last seen 2h ago, note: "AE-1234: eval harness")
Destroy anyway? [y/N]
```

`--force` (existing flag on `destroy`) and `--all-sessions` (new flag,
behaves as if no session were active) both bypass the prompt.

**Track command behavior:** `rp pod track` records the current session as
owner. `rp pod untrack` doesn't touch ownership of the underlying RunPod
record (untrack only removes the local alias mapping; the field becomes
moot when the row is deleted).

### Layer 4 — Pod notes

**Storage:** One new field on `PodMetadata`:

```python
class PodMetadata(BaseModel):
    pod_id: str
    managed: bool = False
    owner_session_id: str | None = None
    stopped_at: datetime | None = None
    note: str | None = None
```

(No `created_at` / `created_from`. YAGNI per design discussion.)

**CLI:**

```bash
rp up h100 --note "AE-1234: fine-tune classifier on cleaned dataset"
rp pod create --gpu H100 --note "AE-1234: probe-only training run"

rp pod note ast_alex_4 "AE-1234: now testing eval harness"
rp pod note ast_alex_4 --append "checkpoint at /workspace/runs/v3"
rp pod note ast_alex_4 --clear
rp pod note ast_alex_4                                       # print current note
```

Notes appear in:

- `rp pod show <alias>` — always
- `rp pod list` — third column, only rendered when at least one pod has
  a note (keeps the table clean otherwise); truncated at terminal width
- The stale-pod warning banner — inline with the alias
- `rp prune` interactive picker — full text

**How Claude learns to set notes:**

1. **`--note` flag on creation commands.** Primary path. Picks up the
   ticket/task context at the moment of creation, before Claude's context
   drifts to something else.
2. **In-output reminder when missing.** When `rp up` succeeds and
   `--note` wasn't passed and `os.environ.get("CLAUDECODE") == "1"`, print:
   ```
   ℹ️  No note set. Run: rp pod note ast_alex_4 "<ticket-id>: <task>"
   ```
   Single line, after the success banner. Not a prompt, not a hard
   error. Suppressed outside Claude.
3. **One sentence in the runpod skill** (added to `start.md`):
   > When creating a pod for substantive work, pass
   > `--note "<ticket-id>: <one-line task>"` to `rp up`. This appears in
   > `rp pod list` and stale-pod warnings later. Keep it under 80 chars.

Total skill-instruction footprint: one sentence with one example. The
flag's discoverability via `--help` and the reminder line carry the rest.

---

## Affected files (rough sketch — not exhaustive)

- `src/rp/core/models.py` — `PodMetadata` field additions
- `src/rp/core/pod_manager.py` — populate new fields in create/stop/start
- `src/rp/core/pod_setup.py` — XDG cache env vars, container-disk default
- `src/rp/core/default_templates.py` — storage/disk defaults
- `src/rp/core/session.py` — **new file** with `current_session_id()`
- `src/rp/cli/main.py` — register `rp prune`, `rp pod note`
- `src/rp/cli/commands.py` — `--storage` rename, `--note` flag, session-aware list/destroy, stale banner in `_auto_clean()`, prune command, note command
- `src/rp/cli/utils.py` — maybe a `format_age()` helper for the stale display
- `src/rp/assets/auto_shutdown.sh` — `DELETE` → `POST .../stop`
- `docs.md`, `README.md` — flag rename, new commands, new defaults
- `~/.claude/skills/runpod/docs/start.md` — note-writing instruction,
  workspace-as-clone-target
- `~/.claude/skills/runpod/docs/stop.md` — `--destroy` guidance

## Testing

**Unit tests** (existing pattern under `tests/unit/`):

- Session resolution: `current_session_id()` returns the right thing across
  the three cases (RP_SESSION_ID set, CLAUDE_CODE_SESSION_ID set, neither
  set, both set with RP_SESSION_ID winning).
- Stale detection: given a `pod_metadata` dict with mixed `stopped_at`
  values, the banner-eligible subset is computed correctly.
- Note manipulation: `rp pod note --append` concatenates with a separator;
  `--clear` nulls the field; bare `rp pod note <alias>` prints.
- PodMetadata serialization round-trips the new fields and tolerates old
  JSON (no `owner_session_id`, no `stopped_at`, no `note`).
- Cost-display helper produces the right `~$X/mo` string.

**E2E tests** (under `tests/e2e/`, using the existing `shared_test_pod` /
`managed_pod` fixtures so we don't multiply pod creation cost):

- **`test_storage_flag_rename`** — `rp pod create --storage 100GB ...`
  succeeds; the pod has the expected `volumeInGb`.
- **`test_rp_down_stops_by_default`** — after `rp down <alias>`, the pod
  is in `STOPPED` state (not removed from the account); alias is still
  present; SSH config block was removed; `stopped_at` is set in
  `pods.json`. A subsequent `rp pod start <alias>` brings it back.
- **`test_rp_down_destroy_terminates`** — `rp down <alias> --destroy`
  removes the pod from RunPod and deletes the alias.
- **`test_pod_note_lifecycle`** — `rp up --note "test note"` sets the note;
  `rp pod note <alias>` prints it; `--append " more"` extends it;
  `--clear` removes it. Note appears in `rp pod show` output.
- **`test_session_scoping_filter`** — with two different
  `RP_SESSION_ID` values, `rp pod list` shows only the matching pods;
  `--all` shows both. (Reuses two pre-existing pods; doesn't need to
  create more.)
- **`test_destroy_cross_session_prompt`** — destroy of a pod owned by a
  different session prompts (the test passes `--force` to confirm the
  prompt path doesn't break the underlying destroy).

We intentionally don't E2E-test the stale banner (would require
fast-forwarding time on a real pod; not worth the complexity — covered
by unit tests over a synthetic `pod_metadata` dict).

## Versioning

This is a feature release with one breaking flag rename
(`--persistent-volume` → `--storage`). Per project convention
(`CLAUDE.md`), bump the minor version: `0.12.0 → 0.13.0`. Single bump in
the final PR; no version bumps per intermediate commit.

## Implementation order

Not prescriptive, but a natural sequence:

1. `current_session_id()` helper + `owner_session_id` field + listing
   filter (the new mechanism without behavior changes that could surprise
   the user mid-task).
2. `note` field + `--note` flag + `rp pod note` command + display in
   `show`/`list` (additive; no migration risk).
3. `--storage` rename + new defaults in templates and create commands +
   workspace env vars.
4. `stopped_at` field + `rp down` stop semantics +
   `auto_shutdown.sh` stop-instead-of-destroy + `--destroy` flag.
5. Stale banner + `rp prune` command.
6. Destroy-cross-session prompt.
7. Skill docs and `docs.md` / `README.md` updates.
8. E2E tests for each layer (can interleave with the above).

Each step lands as a separate atomic commit; the version bump rides on
the final PR.
