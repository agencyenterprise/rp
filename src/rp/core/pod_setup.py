"""Opinionated pod setup for managed pods.

This module handles the full setup that 'rp up' performs: installing tools,
creating a non-root user, injecting secrets, and deploying auto-shutdown.
"""

import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

from rp.core.secret_manager import SecretManager

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets"
ENV_FILE_ROOT = "/root/.rp-env"
ENV_FILE_USER = "/home/user/.rp-env"


class PodSetup:
    """Handles full opinionated setup of a managed pod."""

    def __init__(self, ssh_alias: str, pod_id: str, console: Console | None = None):
        self.ssh_alias = ssh_alias
        self.pod_id = pod_id
        self.console = console or Console()
        self.secret_manager = SecretManager()

    def run_full_setup(self) -> None:
        """Run complete opinionated setup (tools, user, secrets, auto-shutdown)."""
        self.install_tools()
        self.create_non_root_user()
        self.inject_secrets()
        self.deploy_auto_shutdown()

    def run_managed_restart_setup(self) -> None:
        """Re-run setup needed after a managed pod restarts (secrets + auto-shutdown)."""
        self.inject_secrets()
        self.deploy_auto_shutdown()

    def install_tools(self) -> None:
        """Install essential tools on the pod."""
        self.console.print("    Installing tools on pod...")
        setup_script = _TOOL_INSTALL_SCRIPT
        self._ssh_run_script(setup_script)

    def create_non_root_user(self) -> None:
        """Create non-root 'user' with sudo access for Claude CLI."""
        self.console.print("    Creating non-root user...")
        self._ssh_run_script(_CREATE_USER_SCRIPT)

    def inject_secrets(self) -> None:
        """Inject secrets from Keychain into the pod's environment file."""
        self.console.print("    Injecting secrets...")

        lines: list[str] = []

        # Always include pod identity
        api_key = self.secret_manager.get("RUNPOD_API_KEY")
        if api_key:
            lines.append(f"export RUNPOD_API_KEY={api_key}")
        lines.append(f"export RUNPOD_POD_ID={self.pod_id}")

        # GitHub token (from gh CLI)
        gh_token = _get_gh_token()
        if gh_token:
            lines.append(f"export GH_TOKEN={gh_token}")

        # Claude OAuth token (from Keychain)
        oauth_token = _get_claude_oauth_token()
        if oauth_token:
            lines.append(f"export CLAUDE_CODE_OAUTH_TOKEN={oauth_token}")

        # AWS credentials (from aws CLI)
        aws_creds = _get_aws_credentials()
        for key, value in aws_creds.items():
            lines.append(f"export {key}={value}")

        # Custom secrets from Keychain manifest
        for name in self.secret_manager.list_names():
            if name == "RUNPOD_API_KEY":
                continue  # Already handled
            value = self.secret_manager.get(name)
            if value:
                lines.append(f"export {name}={value}")

        # Write env file to pod
        env_content = "\n".join(lines) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(env_content)
            tmp_path = f.name

        try:
            self._scp_to_pod(tmp_path, ENV_FILE_ROOT)
            # Copy to user home and set up sourcing
            self._ssh_run_script(f"""
cp {ENV_FILE_ROOT} {ENV_FILE_USER} 2>/dev/null || true
chown user:user {ENV_FILE_USER} 2>/dev/null || true
# Set up git credentials if GH_TOKEN is available
source {ENV_FILE_ROOT}
if [ -n "${{GH_TOKEN:-}}" ]; then
    echo "https://x-access-token:${{GH_TOKEN}}@github.com" > /root/.git-credentials
    git config --global credential.helper store
    cp /root/.git-credentials /home/user/.git-credentials 2>/dev/null || true
    cp /root/.gitconfig /home/user/.gitconfig 2>/dev/null || true
    chown user:user /home/user/.git-credentials /home/user/.gitconfig 2>/dev/null || true
fi
""")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def deploy_auto_shutdown(self, idle_minutes: int = 120) -> None:
        """Deploy GPU idle auto-shutdown cron on the pod."""
        self.console.print("    Deploying auto-shutdown cron...")

        auto_shutdown_script = ASSETS_DIR / "auto_shutdown.sh"
        if not auto_shutdown_script.exists():
            self.console.print(
                "    [yellow]Warning: auto_shutdown.sh not found, skipping.[/yellow]"
            )
            return

        self._scp_to_pod(str(auto_shutdown_script), "/usr/local/bin/auto_shutdown.sh")
        self._ssh_run_script(f"""
chmod +x /usr/local/bin/auto_shutdown.sh
# Set idle threshold via env
grep -q 'AUTO_SHUTDOWN_IDLE_MINUTES' {ENV_FILE_ROOT} 2>/dev/null || \
    echo "export AUTO_SHUTDOWN_IDLE_MINUTES={idle_minutes}" >> {ENV_FILE_ROOT}
# Install cron job (every 5 minutes)
CRON_LINE="*/5 * * * * /usr/local/bin/auto_shutdown.sh >> /var/log/auto_shutdown.log 2>&1"
(crontab -l 2>/dev/null | grep -v auto_shutdown; echo "$CRON_LINE") | crontab -
# Ensure cron is running
pgrep -x cron >/dev/null 2>&1 || {{
    dpkg -s cron >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq cron > /dev/null 2>&1)
    service cron start 2>/dev/null || cron 2>/dev/null || true
}}
""")

    def _ssh_run_script(self, script: str) -> None:
        """Run a bash script on the pod via SSH."""
        subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=accept-new",
                self.ssh_alias,
                "bash -s",
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
        )

    def _scp_to_pod(self, local_path: str, remote_path: str) -> None:
        """Copy a file to the pod via SCP."""
        subprocess.run(
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=accept-new",
                local_path,
                f"{self.ssh_alias}:{remote_path}",
            ],
            capture_output=True,
            check=True,
        )


def _get_gh_token() -> str | None:
    """Get GitHub token from gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_claude_oauth_token() -> str | None:
    """Get Claude OAuth token from macOS Keychain."""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        import json

        data = json.loads(result.stdout.strip())
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        json.JSONDecodeError,
        KeyError,
    ):
        return None


def _get_aws_credentials() -> dict[str, str]:
    """Get AWS credentials from aws CLI."""
    try:
        result = subprocess.run(
            ["aws", "configure", "export-credentials", "--format", "env-no-export"],
            capture_output=True,
            text=True,
            check=True,
        )
        creds: dict[str, str] = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                if key.startswith("AWS_"):
                    creds[key] = value.strip()
        return creds
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}


# --- Inline setup scripts ---

_TOOL_INSTALL_SCRIPT = """\
set -e
export DEBIAN_FRONTEND=noninteractive
command -v uv >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh; }
command -v unzip >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq unzip > /dev/null 2>&1)
command -v tmux >/dev/null 2>&1 || apt-get install -y -qq tmux > /dev/null 2>&1
command -v rsync >/dev/null 2>&1 || apt-get install -y -qq rsync > /dev/null 2>&1
command -v sudo >/dev/null 2>&1 || apt-get install -y -qq sudo > /dev/null 2>&1
command -v jq >/dev/null 2>&1 || apt-get install -y -qq jq > /dev/null 2>&1
command -v htop >/dev/null 2>&1 || apt-get install -y -qq htop > /dev/null 2>&1
if ! command -v aws >/dev/null 2>&1; then
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
    cd /tmp && unzip -qo awscliv2.zip && ./aws/install && cd -
fi
if ! command -v claude >/dev/null 2>&1; then
    if ! command -v node >/dev/null 2>&1; then
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
        apt-get install -y -qq nodejs >/dev/null 2>&1
    fi
    npm install -g @anthropic-ai/claude-code >/dev/null 2>&1
fi
# Ensure cron daemon is available
pgrep -x cron >/dev/null 2>&1 || {
    dpkg -s cron >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq cron > /dev/null 2>&1)
    service cron start 2>/dev/null || cron 2>/dev/null || true
}
# Environment sourcing
cat > /etc/profile.d/rp-env.sh << 'PROFILED'
export PATH="$HOME/.local/bin:$PATH"
[ -f /root/.rp-env ] && source /root/.rp-env
PROFILED
chmod 644 /etc/profile.d/rp-env.sh
grep -q 'rp-env' /root/.bashrc 2>/dev/null || cat >> /root/.bashrc << 'BASHRC'

# rp managed environment
[ -f /etc/profile.d/rp-env.sh ] && source /etc/profile.d/rp-env.sh
BASHRC
"""

_CREATE_USER_SCRIPT = """\
set -e
# Create non-root user for Claude CLI (refuses --dangerously-skip-permissions as root)
if ! id -u user >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo user
    echo "user ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/user
    chmod 440 /etc/sudoers.d/user
fi
USER_HOME=/home/user
# Set up user's bashrc to source environment
grep -q 'rp-env' "$USER_HOME/.bashrc" 2>/dev/null || cat >> "$USER_HOME/.bashrc" << 'USERBASHRC'

# rp managed environment
export PATH="$HOME/.local/bin:$PATH"
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
USERBASHRC
# Give user access to workspace
chown -R user:user "$USER_HOME"
[ -d /workspace ] && chown -R user:user /workspace || true
"""
