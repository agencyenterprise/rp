"""Remote Claude Code session management.

This module handles launching Claude Code on remote pods in tmux sessions,
checking status, and syncing logs.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

TMUX_SESSION = "claude-task"
REMOTE_USER = "user"
REMOTE_USER_HOME = "/home/user"
LOG_FILE = f"{REMOTE_USER_HOME}/.claude_output.log"
REPORT_FILE = f"{REMOTE_USER_HOME}/.claude_report.md"
LOCAL_SESSIONS_DIR = Path.home() / ".claude" / "remote-sessions"


class ClaudeRemote:
    """Manage remote Claude Code sessions on pods."""

    def __init__(self, ssh_alias: str, pod_id: str, console: Console | None = None):
        self.ssh_alias = ssh_alias
        self.pod_id = pod_id
        self.console = console or Console()

    def launch(
        self,
        working_dir: str = "/workspace",
        prompt: str | None = None,
    ) -> None:
        """Launch Claude in a tmux session on the pod."""
        self._refresh_oauth_token()

        if self.is_running():
            self.console.print(
                f"Claude is already running in tmux session '{TMUX_SESSION}'."
            )
            self.console.print(
                f"  Attach: ssh {self.ssh_alias} -t sudo -u {REMOTE_USER} tmux attach -t {TMUX_SESSION}"
            )
            return

        # Build the claude command
        claude_cmd = f"source {REMOTE_USER_HOME}/.rp-env && cd {working_dir}"

        if prompt:
            # Write prompt to file on pod (avoids escaping hell)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(prompt)
                prompt_tmp = f.name

            try:
                # Clean up any stale prompt file first
                subprocess.run(
                    ["ssh", self.ssh_alias, "rm -f /tmp/.claude_prompt"],
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    [
                        "scp",
                        "-q",
                        prompt_tmp,
                        f"{self.ssh_alias}:/tmp/.claude_prompt",
                    ],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "ssh",
                        self.ssh_alias,
                        f"chown {REMOTE_USER}:{REMOTE_USER} /tmp/.claude_prompt",
                    ],
                    check=True,
                    capture_output=True,
                )
            finally:
                Path(prompt_tmp).unlink(missing_ok=True)

            claude_cmd += (
                f" && claude --dangerously-skip-permissions --verbose"
                f" --output-format stream-json"
                f' -p "$(cat /tmp/.claude_prompt)"'
                f" 2>&1 | tee {LOG_FILE}"
            )
        else:
            claude_cmd += " && claude --dangerously-skip-permissions"

        # Write launcher script on pod (avoids multi-layer escaping)
        launcher = f"{REMOTE_USER_HOME}/run_claude.sh"
        launcher_content = f"#!/bin/bash -l\n{claude_cmd}\n"
        subprocess.run(
            [
                "ssh",
                self.ssh_alias,
                f"cat > {launcher} && chown {REMOTE_USER}:{REMOTE_USER} {launcher} && chmod +x {launcher}",
            ],
            input=launcher_content,
            text=True,
            capture_output=True,
            check=True,
        )

        # Launch in tmux as non-root user
        subprocess.run(
            [
                "ssh",
                self.ssh_alias,
                f"sudo -u {REMOTE_USER} tmux new-session -d -s {TMUX_SESSION} -x 200 -y 50 {launcher}",
            ],
            check=True,
            capture_output=True,
        )

        self.console.print(f"Claude is running in tmux session '{TMUX_SESSION}'.")
        self.console.print(
            f"  Attach: ssh {self.ssh_alias} -t sudo -u {REMOTE_USER} tmux attach -t {TMUX_SESSION}"
        )

    def is_running(self) -> bool:
        """Check if the tmux session is still alive."""
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=5",
                self.ssh_alias,
                f"sudo -u {REMOTE_USER} tmux has-session -t {TMUX_SESSION} 2>/dev/null && echo yes || echo no",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() == "yes"

    def get_status(self, lines: int = 30) -> dict:
        """Get session status and recent output.

        Returns dict with keys: running (bool), output (str), report (str|None)
        """
        running = self.is_running()

        # Get recent output
        result = subprocess.run(
            ["ssh", self.ssh_alias, f"tail -n {lines} {LOG_FILE} 2>/dev/null"],
            capture_output=True,
            text=True,
            check=False,
        )
        raw_output = result.stdout.strip()

        # Parse stream-json output
        parsed_lines: list[str] = []
        for raw_line in raw_output.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                msg_type = obj.get("type", "")
                if msg_type == "assistant":
                    content = obj.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parsed_lines.append(f"[assistant] {block['text'][:200]}")
                        elif (
                            isinstance(block, dict) and block.get("type") == "tool_use"
                        ):
                            parsed_lines.append(f"[tool_use] {block.get('name', '?')}")
                elif msg_type == "result":
                    cost = obj.get("cost_usd", 0)
                    duration = obj.get("duration_ms", 0)
                    turns = obj.get("num_turns", 0)
                    parsed_lines.append(
                        f"[result] {turns} turns, ${cost:.4f}, {duration / 1000:.1f}s"
                    )
                    if obj.get("is_error"):
                        parsed_lines.append(
                            f"[ERROR] {obj.get('result', 'unknown')[:300]}"
                        )
            except json.JSONDecodeError:
                parsed_lines.append(line[:200])

        output = "\n".join(parsed_lines) if parsed_lines else raw_output

        # Get report if available
        report_result = subprocess.run(
            ["ssh", self.ssh_alias, f"cat {REPORT_FILE} 2>/dev/null"],
            capture_output=True,
            text=True,
            check=False,
        )
        report = report_result.stdout.strip() or None

        return {"running": running, "output": output, "report": report}

    def sync_logs(self) -> Path:
        """Sync remote Claude logs to local machine. Returns local path."""
        local_dir = LOCAL_SESSIONS_DIR / self.pod_id
        local_dir.mkdir(parents=True, exist_ok=True)

        # Check SSH connectivity first
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=5",
                self.ssh_alias,
                "echo ok",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.console.print(
                f"[yellow]Cannot connect to {self.ssh_alias} — pod may be down.[/yellow]"
            )
            return local_dir

        rsync_excludes = [
            "--exclude=debug/",
            "--exclude=shell-snapshots/",
            "--exclude=cache/",
            "--exclude=statsig/",
            "--exclude=telemetry/",
            "--exclude=plugins/",
            "--exclude=paste-cache/",
            "--exclude=backups/",
            "--exclude=file-history/",
        ]

        # Try non-root user first (where Claude runs), fall back to root
        check = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=5",
                self.ssh_alias,
                f"test -d {REMOTE_USER_HOME}/.claude",
            ],
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            source = f"{self.ssh_alias}:{REMOTE_USER_HOME}/.claude/"
        else:
            source = f"{self.ssh_alias}:/root/.claude/"

        subprocess.run(
            ["rsync", "-az", "--delete", *rsync_excludes, source, str(local_dir) + "/"],
            capture_output=True,
            check=False,
        )

        return local_dir

    def _refresh_oauth_token(self) -> None:
        """Get OAuth token from local Keychain and inject into pod."""
        from rp.core.pod_setup import _get_claude_oauth_token

        token = _get_claude_oauth_token()
        if not token:
            self.console.print(
                "[yellow]Warning: Claude OAuth token not found in Keychain.[/yellow]"
            )
            return

        token_line = f"export CLAUDE_CODE_OAUTH_TOKEN={token}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(token_line + "\n")
            tmp_path = f.name

        try:
            subprocess.run(
                ["scp", "-q", tmp_path, f"{self.ssh_alias}:/tmp/.oauth_token_line"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "ssh",
                    self.ssh_alias,
                    """bash -s << 'EOF'
for ENV_FILE in /root/.rp-env /home/user/.rp-env; do
    [ -f "$ENV_FILE" ] || continue
    grep -v "^export CLAUDE_CODE_OAUTH_TOKEN=" "$ENV_FILE" > "${ENV_FILE}.tmp" || true
    cat /tmp/.oauth_token_line >> "${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
done
chown user:user /home/user/.rp-env 2>/dev/null || true
rm -f /tmp/.oauth_token_line
EOF""",
                ],
                check=True,
                capture_output=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
