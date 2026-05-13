# rp — RunPod CLI

[![Unit tests](https://github.com/agencyenterprise/rp/actions/workflows/unit.yml/badge.svg)](https://github.com/agencyenterprise/rp/actions/workflows/unit.yml)
[![E2E tests](https://github.com/agencyenterprise/rp/actions/workflows/e2e.yml/badge.svg?branch=main)](https://github.com/agencyenterprise/rp/actions/workflows/e2e.yml)
[![Last E2E pass](https://img.shields.io/github/last-commit/agencyenterprise/rp/e2e-last-pass?label=last%20e2e%20pass)](https://github.com/agencyenterprise/rp/actions/workflows/e2e.yml)

A CLI for managing RunPod GPU pods. Two tiers: **low-level** (`rp pod create`) for bare pods, and **opinionated** (`rp up`) for fully configured pods with tools, secrets, auto-shutdown, and remote Claude support.

## Install

```bash
uv tool install https://github.com/agencyenterprise/rp.git
rp --install-completion  # optional tab completion
```

After each command, `rp` checks GitHub for a newer version and prints a one-line upgrade hint on stderr when behind. The check is cached for 24h and silent on network errors. Set `RP_NO_VERSION_CHECK=1` to disable.

## Quick Start

```bash
# Store your API key in macOS Keychain
rp secrets set RUNPOD_API_KEY

# Create a fully configured pod (tools, secrets, auto-shutdown, 400GB /workspace)
rp up h100 --note "AE-1234: classifier eval"

# Run commands on it
rp run h100-1 -- nvidia-smi

# Launch remote Claude
rp claude h100-1 -p "Run the training script in /workspace/project"

# Check progress
rp status h100-1

# Sync logs and stop when done (alias kept; resume with rp pod start)
rp down h100-1

# Terminate permanently when all data is saved elsewhere
rp down h100-1 --destroy
```

## Commands

### Managed Pods (opinionated)

| Command | Description |
|---|---|
| `rp up [template] [--gpu SPEC] [--disk SIZE] [--storage SIZE] [--alias NAME] [--network-volume ID] [--note TEXT]` | Create pod + install tools + inject secrets + deploy auto-shutdown |
| `rp claude <alias> [-p PROMPT] [-d DIR]` | Launch Claude in tmux on pod (autonomous or interactive) |
| `rp status <alias>` | Check remote Claude progress and read report |
| `rp logs <alias>` | Sync remote Claude logs locally |
| `rp down <alias> [--skip-logs] [--destroy]` | Sync logs and stop a pod (use `--destroy` to terminate permanently) |
| `rp prune` | Interactively review and destroy stale stopped pods |
| `rp setup <alias>` | Re-run pod setup (recovery from partial failures) |
| `rp secrets list\|set\|remove\|inject` | Manage secrets in macOS Keychain; `inject` pushes to a running pod |

### Connection & Execution

| Command | Description |
|---|---|
| `rp run <alias> -- <cmd>` | Execute command on pod via SSH |
| `rp shell <alias>` | Interactive SSH shell |
| `rp code <alias> [path]` | Open VS Code remote SSH |
| `rp scp <src> <dest>` | Copy files to/from pod via SCP |

### Low-Level (`rp pod`)

| Command | Description |
|---|---|
| `rp pod create [template] [--gpu] [--disk SIZE] [--storage SIZE] [--alias] [--note TEXT]` | Create bare pod, run setup.sh |
| `rp pod start <alias>` | Resume stopped pod (re-injects secrets for managed pods) |
| `rp pod stop <alias>` | Stop pod |
| `rp pod destroy <alias> [-f] [--all-sessions]` | Terminate pod permanently |
| `rp pod track <pod_id_or_name> [alias]` | Track existing pod by ID or name |
| `rp pod untrack <alias>` | Stop tracking (doesn't terminate) |
| `rp pod list [--all]` | List pods (current session by default; `--all` shows all sessions) |
| `rp pod show <alias>` | Detailed pod info |
| `rp pod note <alias> [TEXT] [--append] [--clear]` | Set/append/clear/show the pod's note |
| `rp pod clean` | Remove invalid aliases and orphaned SSH config |
| `rp pod gpus [-f FILTER]` | List available GPU types (e.g. `-f 'vram>=80'`) |

### Templates

Built-in: `h100`, `2h100`, `4h100`, `8h100`, `h200`, `2h200`, `4h200`, `8h200`, `b200`, `2b200`, `4b200`, `8b200`, `5090`, `a40` (all 400GB persistent volume, 50GB container disk, using `{project}_{person}_{i}` naming).

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
