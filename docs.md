# rp — RunPod CLI

CLI wrapper around the RunPod Python API for managing GPU pods. Two tiers of pod management: **low-level** (`rp pod` subcommands for bare pods) and **opinionated** (top-level commands for managed pods with tools, secrets, auto-shutdown, remote Claude).

## Installation

Requires Python 3.13+, `uv`, and a RunPod API key.

```bash
uv tool install https://github.com/agencyenterprise/rp.git
rp --install-completion  # optional tab completion
```

API key priority: `RUNPOD_API_KEY` env var → macOS Keychain → interactive prompt (saves to Keychain).

### Update notifications

After each command, `rp` prints a one-line notice on stderr if a newer version is available on `main` of the GitHub repo. The check fetches `pyproject.toml` from `raw.githubusercontent.com` (no auth, public repo), caches the result for 24h in `~/.config/rp/version_check.json`, and degrades silently on any network or parse error. Set `RP_NO_VERSION_CHECK=1` to disable.

---

## Command Reference

### Opinionated Commands (Managed Pods)

#### `rp up [template] [--alias NAME] [--gpu SPEC] [--disk SIZE] [--storage SIZE] [--network-volume ID] [--note TEXT] [--all-sessions] [-f]`

Create a pod with full opinionated setup. This is the recommended way to create pods.

Uses `rp pod create` under the hood, then layers on: tool installation (uv, tmux, aws CLI, claude CLI, node), non-root `user` account (required by Claude CLI), secret injection from Keychain, and GPU idle auto-shutdown cron (120 min default).

Managed pods are marked with `managed: true` in metadata. On `rp start`, managed pods get secrets re-injected and auto-shutdown redeployed.

The tool-install step waits for cloud-init / unattended-upgrades to release the dpkg lock before running apt, and the whole step is retried up to 3× (30s backoff) on transient apt failures (exit 100 / "Could not get lock"). When something does fail, the full transcript is teed to `/tmp/rp-setup.log` on the pod — `ssh <alias> 'cat /tmp/rp-setup.log'` for post-mortem.

When the requested GPU type is fully out of capacity, the error message includes the five closest-VRAM alternatives as copy-pasteable `rp up --gpu '<count>x<id>'` commands.

**Flags:**

- `--disk SIZE` — Ephemeral container disk (deleted with pod). Defaults to template value or 50GB.
- `--storage SIZE` — Persistent volume mounted at `/workspace` (survives stop/start, deleted on destroy). Defaults to template value or 400GB.
- `--network-volume ID` — Attach an existing shared network volume at `/workspace`. Overrides `--storage`.
- `--note TEXT` — One-line description shown in `rp pod list` and stale-pod warnings (e.g. `'AE-1234: classifier eval'`).
- `--all-sessions` — Skip the cross-session confirmation prompt when `--destroy` is used with `rp down`.

```bash
rp up h100                                       # from template (400GB /workspace, 50GB container)
rp up --gpu 2xH100                               # explicit GPU (400GB persistent volume by default)
rp up --gpu 2xH100 --disk 1TB                    # bigger container disk
rp up --gpu 2xH100 --storage 500GB               # custom persistent volume size
rp up h100 --alias custom-name                   # template with alias override
rp up h100 --network-volume vol_abc123           # attach an existing network volume
rp up h100 --note "AE-1234: classifier eval"     # annotate pod purpose
```

When no `--alias` is given (with or without a template), the alias is auto-generated from `{project}_{person}_{i}` using variables from `.rp_settings.json`.

When running inside Claude Code (`CLAUDECODE=1`) and `--note` was not passed, `rp up` prints a one-line reminder to set a note via `rp pod note`.

#### `rp claude <alias> [-p PROMPT] [-d DIR]`

Launch Claude Code in a tmux session on the pod as non-root `user`.

- With `-p`: autonomous mode with `--output-format stream-json`, output logged to `/home/user/.claude_output.log`
- Without `-p`: interactive mode (SSH in and attach to tmux)
- OAuth token refreshed from local Keychain before each launch
- Prompt passed via temp file on pod to avoid SSH escaping issues

```bash
rp claude my-pod -p "Run the training script" -d /workspace/project
rp claude my-pod  # interactive, attach with: ssh my-pod -t sudo -u user tmux attach -t claude-task
```

#### `rp status <alias>`

Check remote Claude progress. Shows whether tmux session is alive, last 30 lines of parsed stream-json output, and the structured report from `~/.claude_report.md` if it exists.

#### `rp logs <alias>`

Sync remote Claude logs to `~/.claude/remote-sessions/<pod_id>/` via rsync. Excludes debug/, cache/, telemetry/, etc.

#### `rp secrets list|set|remove|inject`

Manage secrets in macOS Keychain (service name: `rp`). Secrets are scoped to directories via `.rp_settings.json` files and automatically injected into managed pods during `rp up` and `rp start`.

```bash
rp secrets list                          # show secrets resolved from .rp_settings.json hierarchy
rp secrets list --json                   # JSON output (for machine consumption)
rp secrets set HF_TOKEN                  # prompt for value, store scoped to nearest .rp_settings.json
rp secrets set HF_TOKEN --global         # store scoped to ~/.rp_settings.json
rp secrets set HF_TOKEN --value "hf_..." # non-interactive
echo "hf_..." | rp secrets set HF_TOKEN  # piped input
rp secrets remove HF_TOKEN               # remove from Keychain + settings file
rp secrets remove HF_TOKEN --global      # remove from global scope
rp secrets inject my-pod                 # push secrets to a running pod
```

Secrets are resolved by walking from cwd to filesystem root, collecting `.rp_settings.json` files. Closer files win for same-named secrets (allowing project-level overrides of global tokens). Keychain keys are encoded as `<dir_path>:<SECRET_NAME>` to allow different values at different scopes.

Additionally injected: `RUNPOD_API_KEY`, `RUNPOD_POD_ID`, `GH_TOKEN` (from `gh auth token`), `CLAUDE_CODE_OAUTH_TOKEN` (from Keychain), AWS credentials (from `aws configure export-credentials`). When `GH_TOKEN` is available, HTTPS git credentials are configured in `~/.git-credentials` for both root and user, enabling `git clone/push/pull` via HTTPS URLs. Pods do not have GitHub SSH keys — always use HTTPS URLs for git operations.

Stored in `/root/.rp-env` and `/home/user/.rp-env` on the pod (with `~/.env` symlinks for python-dotenv compatibility). The inject command ensures sourcing hooks exist: `/etc/profile.d/rp-env.sh` (login shells), `/etc/bash.bashrc` (all interactive shells), and per-user `.bashrc` entries. This makes `rp secrets inject` self-contained — it works on any pod, not just those set up with `rp up`.

**Note:** Secrets are available in login shells (`bash -l`), interactive shells (`ssh pod`), and via `rp run`. They are **not** available in non-interactive SSH commands (`ssh pod "echo $VAR"`) because bash skips all rc files for non-interactive sessions. Use `rp run <alias> -- command` instead, or `source ~/.rp-env` at the start of scripts.

#### `rp down <alias> [--skip-logs] [--destroy] [--all-sessions]`

Sync Claude logs and stop a pod. This is the counterpart to `rp up` — it syncs remote Claude logs to `~/.claude/remote-sessions/<pod_id>/` via rsync, then stops the pod and removes its SSH config entry. The alias is retained so the pod can be resumed with `rp pod start`. Log sync failures are non-fatal.

Use `--destroy` to permanently terminate instead of stop. This removes the alias, deletes the SSH config block, and **destroys all data in `/workspace`**. Only use `--destroy` when (a) the pod is broken, or (b) all code is committed/pushed and all data is on S3 or another persistent store.

`--all-sessions` skips the cross-session confirmation prompt when `--destroy` is used on a pod owned by another session.

```bash
rp down my-pod                        # sync logs, then stop (alias kept; resume with rp pod start)
rp down my-pod --skip-logs            # stop immediately without syncing logs
rp down my-pod --destroy              # sync logs, then terminate permanently (/workspace will be lost)
rp down my-pod --destroy --skip-logs  # terminate immediately, no log sync
```

#### `rp setup <alias>`

Re-run pod setup on an existing pod. For managed pods, runs the full setup (tools, non-root user, secrets, auto-shutdown). For bare pods, re-runs the setup script.

Useful for recovery when `rp up` creates the pod but setup fails mid-way (e.g. SSH connection timeout), or for setting up pods tracked from the RunPod web UI.

```bash
rp setup my-pod   # retry failed setup
```

#### `rp prune`

Interactively review and destroy stopped pods that have been stopped longer than the stale threshold (default: 24 hours). Walks each stale pod one at a time and prompts for an action:

- `d` — destroy the pod permanently
- `k` — keep the pod and move on
- `q` — quit without processing remaining pods

The stale threshold can be overridden with `RP_STALE_THRESHOLD_HOURS`. Set `RP_NO_STALE_WARNING=1` to silence the stale-pod banner that appears after every `rp` command.

```bash
rp prune                        # interactive picker for all pods stopped > 24h
RP_STALE_THRESHOLD_HOURS=48 rp prune   # only pods stopped > 48h
```

### Connection

#### `rp run <alias> -- <command>`

Execute a command on the pod via SSH in a login shell (environment variables sourced).

```bash
rp run my-pod -- nvidia-smi
rp run my-pod -- ls -la /workspace
```

#### `rp shell <alias>`

Interactive SSH shell with agent forwarding.

#### `rp code <alias> [path]`

Open VS Code via remote SSH. Default path: `/workspace`.

#### `rp scp <src> <dest>`

Copy files to/from a pod via SCP. Uses standard scp syntax with pod aliases as hostnames. Automatically passes `-r` for recursive copying.

```bash
rp scp ./local_file my-pod:/workspace/
rp scp my-pod:/workspace/results ./results
```

If no alias is detected in the arguments, an interactive pod selector is shown.

### Low-Level Commands (`rp pod`)

All low-level pod management commands live under the `rp pod` subcommand.

#### `rp pod create [template] [--alias NAME] [--gpu SPEC] [--disk SIZE] [--storage SIZE] [--image IMAGE] [--network-volume ID] [--note TEXT] [-f] [--dry-run]`

Create a bare pod, add alias, wait for SSH, run `~/.config/rp/setup.sh`. No secret injection or auto-shutdown. Use `rp up` for full managed setup.

`--disk` sets the ephemeral container disk (default: 500GB for bare `rp pod create`). `--storage` adds a per-pod persistent volume mounted at `/workspace` (default: 0GB / no volume) — survives stop/start, deleted on destroy. `--network-volume` attaches an existing shared network volume at `/workspace` and overrides `--storage`. `--note` stores a one-line description in metadata, shown in `rp pod list` and stale-pod warnings.

```bash
rp pod create --gpu 2xA100                                          # 500GB disk, no persistent volume
rp pod create --gpu 2xA100 --disk 1TB --alias my-pod                # bigger disk, explicit alias
rp pod create h100                                                  # from template (auto-numbered alias)
rp pod create h100 --alias custom-name                              # template with alias override
rp pod create --gpu H100 --network-volume vol_abc123                # attach an existing network volume
rp pod create h100 --note "AE-5678: pretraining run"               # annotate pod purpose
```

#### `rp pod start <alias>`

Resume a stopped pod. Updates SSH config. For managed pods, re-injects secrets and redeploys auto-shutdown. For bare pods, re-runs setup script.

#### `rp pod stop <alias>`

Stop a running pod immediately. Removes SSH config entry.

#### `rp pod destroy <alias> [-f] [--all-sessions]`

Terminate pod permanently, remove alias and SSH config. Prompts for confirmation unless `-f`. When the target pod belongs to a different session, an additional cross-session confirmation is shown; pass `--all-sessions` to skip it.

#### `rp pod track <pod_id_or_name> [alias] [-f]`

Track an existing RunPod pod. First arg can be a pod ID or name. If no alias given, uses the pod's RunPod name. Updates SSH config if pod is running.

#### `rp pod untrack <alias> [--missing-ok]`

Remove alias mapping. Does not terminate the pod.

#### `rp pod list [--all]`

Table of all pods: alias, ID, status (running/stopped/invalid).

When a session env var is active (`CLAUDE_CODE_SESSION_ID` or `RP_SESSION_ID`), only pods owned by the current session are shown by default. Pass `--all` to show every pod; this adds an Owner column identifying which session created each pod, and a footer line counting any hidden pods. Pods without an `owner_session_id` (legacy/unowned) are always visible. In bare-terminal use (no session env var), all pods are shown without filtering.

A Note column appears automatically when at least one pod has a note set.

#### `rp pod note <alias> [TEXT] [--append|-a] [--clear]`

Set, append to, clear, or show the one-line note attached to a pod. Notes are stored in `pods.json` and displayed in `rp pod list`, `rp pod show`, and stale-pod banners.

```bash
rp pod note my-pod "AE-1234: classifier eval"           # set note
rp pod note my-pod --append "checkpoint at /workspace/runs/v3"   # append to existing note
rp pod note my-pod --clear                              # remove note
rp pod note my-pod                                      # print current note
```

#### `rp pod show <alias>`

Detailed pod info: ID, status, GPU, storage, cost, IP, image. For managed running pods, also shows Claude session status and the last few lines of activity if Claude is running. The note (if set) is always shown.

#### `rp pod clean`

Remove aliases pointing to deleted pods, prune orphaned SSH config blocks. Runs automatically after API commands.

#### `rp pod gpus [--filter EXPR]`

List available GPU types from RunPod, sorted by VRAM descending. Optionally filter by VRAM with comparison expressions.

```bash
rp pod gpus                    # list all available GPU types
rp pod gpus -f 'vram>=80'      # only GPUs with 80+ GB VRAM
rp pod gpus -f 'vram<24'       # only GPUs with less than 24 GB VRAM
```

The GPU ID or display name substring can be used as the `--gpu` argument in `rp pod create`, `rp up`, and `rp template create`.

### Templates

#### `rp template create <id> --alias-pattern PATTERN --gpu SPEC [--disk SIZE] [--storage SIZE] [--image IMAGE] [--network-volume ID] [-f]`

Create a reusable pod template. Pattern must contain `{i}` placeholder for auto-numbering. Can also include variable placeholders like `{project}` and `{person}` that are resolved from `~/.config/rp/.env` or `RP_`-prefixed environment variables.

`--disk` defaults to `500GB`; `--storage` defaults to `0GB` (no volume). Override either at `rp up` / `rp pod create` time with the same flag names.

```bash
rp template create ml --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --storage 1TB
rp template create ml-nv --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --network-volume vol_abc123
```

#### `rp template list` / `rp template delete <id>`

List or delete templates. Built-in defaults: `h100`, `2h100`, `4h100`, `8h100`, `h200`, `2h200`, `4h200`, `8h200`, `b200`, `2b200`, `4b200`, `8b200`, `5090`, `a40` (all use `{project}_{person}_{i}` pattern, 400GB persistent volume, 50GB container disk).

---

## Configuration

### .rp_settings.json (Hierarchical Settings)

Settings are defined in `.rp_settings.json` files at any directory level. Resolution walks from cwd to filesystem root; closer files win for scalar values and same-named secrets.

```json
{
  "person": "alex",
  "project": "ast",
  "aws_profile": "amaranth-mfa",
  "secrets": ["HF_TOKEN", "WANDB_API_KEY"]
}
```

All fields are optional. Place a file in `~` for global defaults, in a repo root for project-specific overrides.

**Template variables**: `person` and `project` feed into alias templates (e.g., `{project}_{person}_{i}` → `ast_alex_1`). `RP_`-prefixed environment variables still override settings values.

**Secrets**: The `secrets` list names env vars whose values are stored in macOS Keychain. A project-level `.rp_settings.json` can override a global secret (e.g., a project-specific `HF_TOKEN`).

**AWS profile**: `aws_profile` pins which AWS named profile is used when injecting AWS credentials into managed pods. Without it, `aws configure export-credentials` falls back to the shell's default profile, which may not match the project's AWS account. Setting this in a project-level `.rp_settings.json` keeps the right account's creds going to the pod regardless of the shell's `AWS_PROFILE`.

### Legacy Configuration

The following files in `~/.config/rp/` are still supported:

| File | Purpose |
|------|---------|
| `pods.json` | Aliases, pod metadata (including `managed` flag), templates |
| `setup.sh` | Script run on bare pods during create/start |
| `.env` | Legacy template variables (overridden by `.rp_settings.json`, overridden by `RP_` env vars) |
| `version_check.json` | Cached result of the GitHub update check; refreshed every 24h |

### pods.json

```json
{
  "pod_metadata": {
    "ast_alex_1": {
      "pod_id": "89qgenjznh5t2j",
      "managed": true,
      "note": "AE-1234: classifier eval",
      "owner_session_id": "sess_abc123",
      "stopped_at": "2025-05-10T14:22:00"
    }
  },
  "pod_templates": {
    "ml": {
      "identifier": "ml",
      "alias_template": "{project}_{person}_{i}",
      "gpu_spec": "2xA100",
      "storage_spec": "1TB",
      "network_volume_id": null
    }
  }
}
```

Each alias maps to a `PodMetadata` with `pod_id`, `managed` flag, optional `note`, optional `owner_session_id` (set from `CLAUDE_CODE_SESSION_ID` or `RP_SESSION_ID` at creation time), and optional `stopped_at` (ISO timestamp, set when the pod is stopped, cleared on start).

All writes to `pods.json` are protected by an exclusive file lock (`pods.lock`) so that concurrent `rp` processes (e.g. two Claude Code sessions) don't clobber each other's changes. Every mutating operation re-reads from disk under the lock before writing back.

### SSH Config

Managed blocks in `~/.ssh/config` identified by `# rp:managed alias=<alias> pod_id=<id> updated=<timestamp>`. Created on start, removed on stop/destroy, pruned by clean. Don't edit manually.

Blocks include `StrictHostKeyChecking no` and `UserKnownHostsFile /dev/null` because RunPod IPs are ephemeral and reused across customers — host key verification provides no security value and would cause `Host key verification failed` errors on IP reuse.

### setup.sh

Created on first use with prompted git identity. Runs on bare pods (not managed pods — those use `PodSetup`). Customize at `~/.config/rp/setup.sh`. See `assets/default_setup.sh` for the template.

---

## Technical Details

### GPU Specs

Format: `[<count>x]<model>`. Count defaults to 1. Model is case-insensitive, normalized to uppercase.

Resolution: queries RunPod GPU list, matches model as substring in GPU ID or display name, prefers highest VRAM variant. `H100` matches `H100 SXM 80GB` over `H100 PCIe`.

Edge case: `x` in model name (e.g., `rtx4090`) is fine — only treated as count separator if prefix is numeric.

### Storage Specs

`500GB`, `1TB`, `2.5TB`, `100GiB`. Converted to integer GB. Minimum 10GB, or `0GB` for no volume.

### Network Volumes

RunPod network volumes are persistent storage that can be shared across pods and survive pod termination. Pass a network volume ID via `--network-volume` to attach one at `/workspace`. When a network volume is specified, it takes precedence over `--storage` — leave `--storage` at its default to avoid allocating a separate per-pod volume.

Network volume IDs can be found in the RunPod web console under Storage. Templates can include a `network_volume_id` to automatically attach a volume to all pods created from that template.

### Template Auto-Numbering

Templates support variable placeholders (e.g., `{project}`, `{person}`) resolved from `.rp_settings.json` hierarchy (or `~/.config/rp/.env` / `RP_`-prefixed env vars), plus `{i}` for auto-numbering.

`find_next_alias_index()` finds lowest `i ≥ 1` where the resolved template with that `i` doesn't exist in aliases. Destroying `ast_alex_1` then creating from template gives `ast_alex_1` again.

### Auto-Shutdown (Managed Pods)

Cron runs `auto_shutdown.sh` every 5 minutes. Checks `nvidia-smi` GPU utilization. If all GPUs at 0% for 120 minutes (configurable via `AUTO_SHUTDOWN_IDLE_MINUTES`), stops the pod via the RunPod REST API (`POST /v1/pods/<id>/stop`). State tracked in `/tmp/gpu_idle_since`. The pod remains in your RunPod account and can be resumed with `rp pod start`.

### /workspace Cache Env Vars (Managed Pods)

Managed pods export the following environment variables pointing at `/workspace/.cache`, so that package/model caches survive stop/start cycles:

| Variable | Value |
|---|---|
| `XDG_CACHE_HOME` | `/workspace/.cache` |
| `UV_CACHE_DIR` | `/workspace/.cache/uv` |
| `PIP_CACHE_DIR` | `/workspace/.cache/pip` |
| `HF_HOME` | `/workspace/.cache/huggingface` |

Clone repos to `/workspace/<repo>` so that code also survives stop/start.

### Session Scoping

`rp` reads `CLAUDE_CODE_SESSION_ID` (auto-set by Claude Code ≥ 2.1.139) or `RP_SESSION_ID` (manual override) to determine the active session. Each pod records its creating session as `owner_session_id` in `pods.json`.

- `rp pod list` filters to the current session's pods by default; pass `--all` to see everything (adds an Owner column and a footer counting hidden pods).
- `rp pod destroy` and `rp down --destroy` prompt for confirmation when targeting a pod owned by another session; `--all-sessions` skips this prompt.
- Bare-terminal use (no session env var set) behaves as before: no filtering, no extra prompts.
- Legacy/unowned pods (`owner_session_id` unset) are always visible regardless of session.

### Stale-Pod Warnings

After every `rp` command, a warning banner is printed to stderr when any stopped pods have been stopped for longer than the stale threshold (default: 24 hours). The banner lists each stale pod with its age and note (if set) and suggests `rp prune` or `rp down <alias> --destroy`.

**Environment variables:**

| Variable | Effect |
|---|---|
| `RP_STALE_THRESHOLD_HOURS` | Override the stale threshold (default: `24`) |
| `RP_NO_STALE_WARNING=1` | Silence the banner entirely |

Use `rp prune` for an interactive walk through all stale pods with per-pod `d/k/q` prompts.

### Remote Claude

- Runs as non-root `user` (Claude CLI refuses `--dangerously-skip-permissions` as root)
- Tmux session name: `claude-task`
- Launcher script written to `/home/user/run_claude.sh` to avoid SSH escaping
- OAuth token extracted from `Claude Code-credentials` Keychain entry
- Stream-json output logged to `/home/user/.claude_output.log`
- Structured report at `/home/user/.claude_report.md`

### Error Classes

`RunPodCLIError` base with `message`, `details`, `exit_code`. Subclasses: `AliasError`, `PodError`, `APIError`, `SSHError`, `SetupScriptError`. All caught by `handle_cli_error()` for consistent CLI output.
