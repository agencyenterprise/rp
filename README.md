# rp — RunPod CLI

A CLI for managing RunPod GPU pods. Two tiers: **low-level** (`rp create`) for bare pods, and **opinionated** (`rp up`) for fully configured pods with tools, secrets, auto-shutdown, and remote Claude support.

## Install

```bash
uv tool install https://github.com/Arrrlex/rp.git
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

# Destroy when done
rp destroy h100-1 -f
```

## Commands

### Managed Pods (opinionated)

| Command | Description |
|---|---|
| `rp up [template] [--alias NAME --gpu SPEC --storage SIZE]` | Create pod + install tools + inject secrets + deploy auto-shutdown |
| `rp claude <alias> [-p PROMPT] [-d DIR]` | Launch Claude in tmux on pod (autonomous or interactive) |
| `rp status <alias>` | Check remote Claude progress and read report |
| `rp logs <alias>` | Sync remote Claude logs locally |
| `rp secrets list\|set\|remove` | Manage secrets in macOS Keychain |

### Low-Level

| Command | Description |
|---|---|
| `rp create [template] [--alias --gpu --storage ...]` | Create bare pod, run setup.sh |
| `rp start <alias>` | Resume stopped pod (re-injects secrets for managed pods) |
| `rp stop <alias>` | Stop pod |
| `rp destroy <alias> [-f]` | Terminate pod permanently |
| `rp track <pod_id_or_name> [alias]` | Track existing pod by ID or name |
| `rp untrack <alias>` | Stop tracking (doesn't terminate) |
| `rp list` | List all pods with status |
| `rp show <alias>` | Detailed pod info |
| `rp clean` | Remove invalid aliases and orphaned SSH config |
| `rp run <alias> -- <cmd>` | Execute command on pod via SSH |
| `rp shell <alias>` | Interactive SSH shell |
| `rp code <alias> [path]` | Open VS Code remote SSH |

### Templates

Built-in: `h100`, `2h100`, `5090`, `a40` (all 500GB storage, using `{project}_{person}_{i}` naming).

```bash
# Set naming variables in ~/.config/rp/.env
echo "PROJECT=ast\nPERSON=alex" > ~/.config/rp/.env

rp create h100      # creates ast_alex_1, ast_alex_2, etc.

# Override per-command
RP_PROJECT=other rp create h100   # creates other_alex_1

# Custom templates
rp template create ml --alias-pattern "{project}_{person}_{i}" --gpu 2xA100 --storage 1TB
rp template list
rp template delete ml
```

## Configuration

All config in `~/.config/rp/`. See [docs.md](docs.md) for details.

- `pods.json` — aliases, metadata, templates
- `.env` — template variables for pod naming (`PROJECT`, `PERSON`, etc.)
- `secrets.json` — Keychain secret manifest
- `setup.sh` — script for bare pods (customizable)
