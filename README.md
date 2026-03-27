# rp — RunPod CLI

A CLI for managing RunPod GPU pods. Two tiers: **low-level** (`rp pod create`) for bare pods, and **opinionated** (`rp up`) for fully configured pods with tools, secrets, auto-shutdown, and remote Claude support.

## Install

```bash
uv tool install https://github.com/agencyenterprise/rp.git
rp --install-completion  # optional tab completion
```

## Quick Start

```bash
# Store your API key in macOS Keychain
rp secrets set RUNPOD_API_KEY

# Create a fully configured pod (tools, secrets, auto-shutdown)
rp up h100

# Run commands on it
rp run h100-1 -- nvidia-smi

# Launch remote Claude
rp claude h100-1 -p "Run the training script in /workspace/project"

# Check progress
rp status h100-1

# Sync logs and destroy when done
rp down h100-1
```

## Commands

### Managed Pods (opinionated)

| Command | Description |
|---|---|
| `rp up [template] [--gpu SPEC --storage SIZE] [--alias NAME] [--network-volume ID]` | Create pod + install tools + inject secrets + deploy auto-shutdown |
| `rp claude <alias> [-p PROMPT] [-d DIR]` | Launch Claude in tmux on pod (autonomous or interactive) |
| `rp status <alias>` | Check remote Claude progress and read report |
| `rp logs <alias>` | Sync remote Claude logs locally |
| `rp down <alias> [--skip-logs]` | Sync logs and destroy a pod (counterpart to `rp up`) |
| `rp setup <alias>` | Re-run pod setup (recovery from partial failures) |
| `rp secrets list\|set\|remove\|inject` | Manage secrets in macOS Keychain; `inject` pushes to a running pod |

### Connection & Execution

| Command | Description |
|---|---|
| `rp run <alias> -- <cmd>` | Execute command on pod via SSH |
| `rp shell <alias>` | Interactive SSH shell |
| `rp code <alias> [path]` | Open VS Code remote SSH |

### Low-Level (`rp pod`)

| Command | Description |
|---|---|
| `rp pod create [template] [--gpu --storage ...] [--alias]` | Create bare pod, run setup.sh |
| `rp pod start <alias>` | Resume stopped pod (re-injects secrets for managed pods) |
| `rp pod stop <alias>` | Stop pod |
| `rp pod destroy <alias> [-f]` | Terminate pod permanently |
| `rp pod track <pod_id_or_name> [alias]` | Track existing pod by ID or name |
| `rp pod untrack <alias>` | Stop tracking (doesn't terminate) |
| `rp pod list` | List all pods with status |
| `rp pod show <alias>` | Detailed pod info |
| `rp pod clean` | Remove invalid aliases and orphaned SSH config |
| `rp pod gpus [-f FILTER]` | List available GPU types (e.g. `-f 'vram>=80'`) |

### Templates

Built-in: `h100`, `2h100`, `5090`, `a40` (all 500GB container disk, no volume, using `{project}_{person}_{i}` naming).

```bash
# Set naming variables in .rp_settings.json (or ~/.rp_settings.json for global)
echo '{"project": "ast", "person": "alex"}' > .rp_settings.json

rp pod create h100      # creates ast_alex_1, ast_alex_2, etc.

# Override per-command
RP_PROJECT=other rp pod create h100   # creates other_alex_1

# Custom templates
rp template create ml --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --storage 1TB
rp template list
rp template delete ml
```

## Configuration

Settings are defined in `.rp_settings.json` files at any directory level (walks cwd → root, closest wins). See [docs.md](docs.md) for details.

```json
{"person": "alex", "project": "ast", "secrets": ["HF_TOKEN", "WANDB_API_KEY"]}
```

Additional config in `~/.config/rp/`:
- `pods.json` — aliases, metadata, templates
- `setup.sh` — script for bare pods (customizable)
