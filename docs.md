# rp — RunPod CLI

CLI wrapper around the RunPod Python API for managing GPU pods. Two tiers of pod management: **low-level** (bare pods) and **opinionated** (managed pods with tools, secrets, auto-shutdown, remote Claude).

## Installation

Requires Python 3.13+, `uv`, and a RunPod API key.

```bash
uv tool install https://github.com/agencyenterprise/rp.git
rp --install-completion  # optional tab completion
```

API key priority: `RUNPOD_API_KEY` env var → macOS Keychain → interactive prompt (saves to Keychain).

---

## Command Reference

### Opinionated Commands (Managed Pods)

#### `rp up [template] [--alias NAME] [--gpu SPEC] [--storage SIZE] [--network-volume ID] [-f]`

Create a pod with full opinionated setup. This is the recommended way to create pods.

Uses `rp create` under the hood, then layers on: tool installation (uv, tmux, aws CLI, claude CLI, node), non-root `user` account (required by Claude CLI), secret injection from Keychain, and GPU idle auto-shutdown cron (120 min default).

Managed pods are marked with `managed: true` in metadata. On `rp start`, managed pods get secrets re-injected and auto-shutdown redeployed.

```bash
rp up h100                              # from template
rp up --alias my-pod --gpu 2xH100 --storage 500GB  # explicit
rp up h100 --alias custom-name          # template with alias override
rp up h100 --network-volume vol_abc123  # attach a network volume
```

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

#### `rp setup <alias>`

Re-run pod setup on an existing pod. For managed pods, runs the full setup (tools, non-root user, secrets, auto-shutdown). For bare pods, re-runs the setup script.

Useful for recovery when `rp up` creates the pod but setup fails mid-way (e.g. SSH connection timeout), or for setting up pods tracked from the RunPod web UI.

```bash
rp setup my-pod   # retry failed setup
```

### Low-Level Commands (Bare Pods)

#### `rp create [template] [--alias NAME] [--gpu SPEC] [--storage SIZE] [--container-disk SIZE] [--image IMAGE] [--network-volume ID] [-f] [--dry-run]`

Create a pod, add alias, wait for SSH, run `~/.config/rp/setup.sh`. No secret injection or auto-shutdown.

```bash
rp create --alias my-pod --gpu 2xA100 --storage 500GB
rp create h100                          # from template (auto-numbered alias)
rp create h100 --alias custom-name      # template with alias override
rp create --alias my-pod --gpu H100 --storage 0GB --network-volume vol_abc123
```

#### `rp start <alias>`

Resume a stopped pod. Updates SSH config. For managed pods, re-injects secrets and redeploys auto-shutdown. For bare pods, re-runs setup script.

#### `rp stop <alias>`

Stop a running pod immediately. Removes SSH config entry.

#### `rp destroy <alias> [-f]`

Terminate pod permanently, remove alias and SSH config. Prompts for confirmation unless `-f`.

### Alias & Tracking

#### `rp track <pod_id_or_name> [alias] [-f]`

Track an existing RunPod pod. First arg can be a pod ID or name. If no alias given, uses the pod's RunPod name. Updates SSH config if pod is running.

#### `rp untrack <alias> [--missing-ok]`

Remove alias mapping. Does not terminate the pod.

#### `rp list`

Table of all pods: alias, ID, status (running/stopped/invalid).

#### `rp show <alias>`

Detailed pod info: ID, status, GPU, storage, cost, IP, image.

#### `rp clean`

Remove aliases pointing to deleted pods, prune orphaned SSH config blocks. Runs automatically after API commands.

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

### Templates

#### `rp template create <id> --alias-pattern PATTERN --gpu SPEC --storage SIZE [--container-disk SIZE] [--image IMAGE] [--network-volume ID] [-f]`

Create a reusable pod template. Pattern must contain `{i}` placeholder for auto-numbering. Can also include variable placeholders like `{project}` and `{person}` that are resolved from `~/.config/rp/.env` or `RP_`-prefixed environment variables.

```bash
rp template create ml --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --storage 1TB
rp template create ml-nv --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --storage 0GB --network-volume vol_abc123
```

#### `rp template list` / `rp template delete <id>`

List or delete templates. Built-in defaults: `h100`, `2h100`, `5090`, `a40` (all use `{project}_{person}_{i}` pattern).

---

## Configuration

### .rp_settings.json (Hierarchical Settings)

Settings are defined in `.rp_settings.json` files at any directory level. Resolution walks from cwd to filesystem root; closer files win for scalar values and same-named secrets.

```json
{
  "person": "alex",
  "project": "ast",
  "secrets": ["HF_TOKEN", "WANDB_API_KEY"]
}
```

All fields are optional. Place a file in `~` for global defaults, in a repo root for project-specific overrides.

**Template variables**: `person` and `project` feed into alias templates (e.g., `{project}_{person}_{i}` → `ast_alex_1`). `RP_`-prefixed environment variables still override settings values.

**Secrets**: The `secrets` list names env vars whose values are stored in macOS Keychain. A project-level `.rp_settings.json` can override a global secret (e.g., a project-specific `HF_TOKEN`).

### Legacy Configuration

The following files in `~/.config/rp/` are still supported:

| File | Purpose |
|------|---------|
| `pods.json` | Aliases, pod metadata (including `managed` flag), templates |
| `setup.sh` | Script run on bare pods during create/start |
| `.env` | Legacy template variables (overridden by `.rp_settings.json`, overridden by `RP_` env vars) |

### pods.json

```json
{
  "pod_metadata": {
    "ast_alex_1": { "pod_id": "89qgenjznh5t2j", "managed": true }
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

Each alias maps to a `PodMetadata` with `pod_id` and `managed` flag.

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

RunPod network volumes are persistent storage that can be shared across pods and survive pod termination. Pass a network volume ID via `--network-volume` to attach one at `/workspace`. When a network volume is specified, it takes precedence over the `--storage` volume parameter (set `--storage 0GB` to avoid allocating a separate pod volume).

Network volume IDs can be found in the RunPod web console under Storage. Templates can include a `network_volume_id` to automatically attach a volume to all pods created from that template.

### Template Auto-Numbering

Templates support variable placeholders (e.g., `{project}`, `{person}`) resolved from `.rp_settings.json` hierarchy (or `~/.config/rp/.env` / `RP_`-prefixed env vars), plus `{i}` for auto-numbering.

`find_next_alias_index()` finds lowest `i ≥ 1` where the resolved template with that `i` doesn't exist in aliases. Destroying `ast_alex_1` then creating from template gives `ast_alex_1` again.

### Auto-Shutdown (Managed Pods)

Cron runs `auto_shutdown.sh` every 5 minutes. Checks `nvidia-smi` GPU utilization. If all GPUs at 0% for 120 minutes (configurable via `AUTO_SHUTDOWN_IDLE_MINUTES`), destroys pod via RunPod REST API. State tracked in `/tmp/gpu_idle_since`.

### Remote Claude

- Runs as non-root `user` (Claude CLI refuses `--dangerously-skip-permissions` as root)
- Tmux session name: `claude-task`
- Launcher script written to `/home/user/run_claude.sh` to avoid SSH escaping
- OAuth token extracted from `Claude Code-credentials` Keychain entry
- Stream-json output logged to `/home/user/.claude_output.log`
- Structured report at `/home/user/.claude_report.md`

### Error Classes

`RunPodCLIError` base with `message`, `details`, `exit_code`. Subclasses: `AliasError`, `PodError`, `APIError`, `SSHError`, `SetupScriptError`. All caught by `handle_cli_error()` for consistent CLI output.
