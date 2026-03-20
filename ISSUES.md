# RunPod Skill / `rp` CLI Issues

Encountered during a session on 2026-03-20. Context: creating a 2xH100 pod via `rp up 2h100` and running an experiment on it.

---

## Issues to fix in `rp` CLI

### 1. `rp up` partial failure leaves orphan pod with no SSH config

**What happened:** `rp up 2h100` successfully created the pod on RunPod (got back a pod ID), but then failed during the SSH connection/setup phase with `Connection reset by peer`. The pod was left running and billed, but:
- No SSH config was written to `~/.ssh/config`
- No setup scripts ran (no uv, no secrets injected, no auto-shutdown cron)
- The alias was partially tracked (showed up in `rp list`) but `rp run` couldn't connect

**Fix needed:** `rp up` should split pod creation from setup and handle each phase's failure independently:
1. After the API creates the pod, immediately write SSH config (even before confirming SSH works). This lets the user `ssh` in manually if everything else fails.
2. Add retry logic for the SSH connection phase — RunPod pods often take 10-30s to start sshd after the API reports them as ready. A simple exponential backoff (e.g. 5s, 10s, 20s, up to ~60s) would cover most cases.
3. If setup still fails after retries, print recovery instructions: `"Pod created but setup failed. Run 'rp setup <alias>' to retry."`
4. Never leave the user in a state where the pod is running + billed but unreachable without manual intervention.

### 2. `rp secrets set` doesn't work non-interactively

**What happened:** `rp secrets set HF_TOKEN` uses `getpass` which requires a TTY. When called from Claude Code's bash tool, it prints a warning and then cancels.

**Fix needed:** Add a `--value` flag: `rp secrets set HF_TOKEN --value "hf_..."`. Keep the interactive `getpass` prompt as the default when `--value` is omitted. Also support stdin piping: `echo "hf_..." | rp secrets set HF_TOKEN`.

**Skill workaround (done):** Updated skill docs to use `security add-generic-password` directly as a fallback.

### 3. Host key conflicts from RunPod IP reuse

**What happened:** SSH refused to connect with `Host key verification failed` because the IP:port had been used by a previous pod with a different host key.

**Fix needed:** Add `StrictHostKeyChecking no` and `UserKnownHostsFile /dev/null` to the SSH config blocks that `rp track` / `rp up` generate. RunPod IPs are ephemeral and shared across customers — host key verification provides zero security value and actively breaks reconnection. This is the simplest and most robust fix.

**Skill workaround (done):** Added host key troubleshooting instructions to `run.md` for when it happens with existing pods.

### 5. No standalone `rp setup` command for recovery

**What happened:** Because `rp up` failed mid-setup, secrets were never injected. There's no way to re-run just the setup phase.

**Fix needed:** Extract the setup phase of `rp up` into a standalone `rp setup <alias>` command that:
- Installs tools (uv, tmux, aws, claude CLI, node)
- Creates non-root user
- Injects secrets from Keychain
- Configures GPU idle auto-shutdown cron

This would also be useful for pods created outside of `rp up` (e.g. via the RunPod web UI and then `rp track`'d).

---

## Resolved

### 4. `rp destroy` non-interactive — ALREADY FIXED

`rp destroy` already has `--force` / `-f` flag. The skill's `stop.md` already uses it. No changes needed.

### 6. Docs: template variables — ALREADY FIXED

Updated `start.md` to document `RP_PROJECT` and `RP_PERSON` env vars.
