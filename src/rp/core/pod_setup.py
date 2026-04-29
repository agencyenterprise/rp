"""Opinionated pod setup for managed pods.

This module handles the full setup that 'rp up' performs: installing tools,
creating a non-root user, injecting secrets, and deploying auto-shutdown.
"""

import importlib.resources
import os
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

from rp.core.secret_manager import SecretManager

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
        self._wait_for_ssh()
        self.install_tools()
        self.create_non_root_user()
        self.inject_secrets()
        self.deploy_auto_shutdown()

    def run_managed_restart_setup(self) -> None:
        """Re-run setup needed after a managed pod restarts (secrets + auto-shutdown)."""
        self._wait_for_ssh()
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
        """Inject secrets from Keychain into the pod's environment file.

        Uses hierarchical .rp_settings.json resolution to determine which
        secrets to inject.
        """
        self.console.print("    Injecting secrets...")

        from rp.core.settings import resolve_settings

        lines: list[str] = []

        # Always include pod identity
        resolved = resolve_settings()
        api_key_secret = next(
            (s for s in resolved.secrets if s.name == "RUNPOD_API_KEY"), None
        )
        if api_key_secret:
            api_key = self.secret_manager.get_resolved(api_key_secret)
        else:
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

        # AWS credentials (from aws CLI). Use the profile pinned in
        # .rp_settings.json when present so we don't silently inject the
        # shell's default-profile creds into a project that expects another
        # account.
        aws_creds = _get_aws_credentials(profile=resolved.aws_profile)
        if aws_creds and resolved.aws_profile:
            self.console.print(
                f"      AWS profile: [cyan]{resolved.aws_profile}[/cyan]"
            )
        for key, value in aws_creds.items():
            lines.append(f"export {key}={value}")

        # Custom secrets from hierarchical settings
        injected: set[str] = {"RUNPOD_API_KEY"}  # Already handled above
        for secret in resolved.secrets:
            if secret.name in injected:
                continue
            value = self.secret_manager.get_resolved(secret)
            if value:
                lines.append(f"export {secret.name}={value}")
                injected.add(secret.name)

        # Write env file to pod
        env_content = "\n".join(lines) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(env_content)
            tmp_path = f.name

        try:
            self._scp_to_pod(tmp_path, ENV_FILE_ROOT)
            # Copy to user home, ensure sourcing hooks exist, set up git
            self._ssh_run_script(f"""
cp {ENV_FILE_ROOT} {ENV_FILE_USER} 2>/dev/null || true
chown user:user {ENV_FILE_USER} 2>/dev/null || true

# Ensure /etc/profile.d sourcing hook exists (for login shells)
if [ ! -f /etc/profile.d/rp-env.sh ]; then
    cat > /etc/profile.d/rp-env.sh << 'PROFILED'
export PATH="$HOME/.local/bin:$PATH"
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
PROFILED
    chmod 644 /etc/profile.d/rp-env.sh
fi

# Ensure /etc/bash.bashrc sources the env file (for all interactive shells)
grep -q 'rp-env' /etc/bash.bashrc 2>/dev/null || cat >> /etc/bash.bashrc << 'SYSBASHRC'

# rp managed environment
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
SYSBASHRC

# Ensure root .bashrc sources the env file
grep -q 'rp-env' /root/.bashrc 2>/dev/null || cat >> /root/.bashrc << 'BASHRC'

# rp managed environment
[ -f /etc/profile.d/rp-env.sh ] && source /etc/profile.d/rp-env.sh
BASHRC

# Ensure user .bashrc sources the env file (if user exists)
if id -u user >/dev/null 2>&1; then
    grep -q 'rp-env' /home/user/.bashrc 2>/dev/null || cat >> /home/user/.bashrc << 'USERBASHRC'

# rp managed environment
export PATH="$HOME/.local/bin:$PATH"
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
USERBASHRC
    chown user:user /home/user/.bashrc 2>/dev/null || true
fi

# Create .env symlinks for python-dotenv compatibility
ln -sf {ENV_FILE_ROOT} /root/.env
if id -u user >/dev/null 2>&1; then
    ln -sf {ENV_FILE_USER} /home/user/.env
    chown -h user:user /home/user/.env 2>/dev/null || true
fi

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

        auto_shutdown_ref = importlib.resources.files("rp.assets").joinpath(
            "auto_shutdown.sh"
        )
        with importlib.resources.as_file(auto_shutdown_ref) as auto_shutdown_path:
            if not auto_shutdown_path.exists():
                raise FileNotFoundError(
                    f"auto_shutdown.sh missing from installed rp package at "
                    f"{auto_shutdown_path}. The package data is not being shipped — "
                    "reinstall rp (e.g. `uv tool install --reinstall rp`)."
                )
            self._scp_to_pod(str(auto_shutdown_path), "/usr/local/bin/auto_shutdown.sh")
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

    def _wait_for_ssh(self, timeout: int = 300, interval: int = 5) -> None:
        """Wait until SSH is accepting connections, updating SSH config if port changes."""
        import time

        from rp.core.models import SSHConfig
        from rp.core.ssh_manager import SSHManager
        from rp.utils.api_client import RunPodAPIClient

        self.console.print("    Waiting for SSH...")
        start = time.time()
        last_port = None

        while time.time() - start < timeout:
            # Re-check the API for current network info (port can change on restart)
            try:
                api_client = RunPodAPIClient()
                pod_data = api_client.get_pod(self.pod_id)
                ip, port = api_client.extract_network_info(pod_data)
                if ip and port and port != last_port:
                    last_port = port
                    ssh_config = SSHConfig(
                        alias=self.ssh_alias,
                        pod_id=self.pod_id,
                        hostname=ip,
                        port=port,
                    )
                    SSHManager().update_host_config(ssh_config)
            except Exception:
                pass

            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-o",
                    "ConnectTimeout=5",
                    self.ssh_alias,
                    "echo ok",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(interval)
        raise TimeoutError(f"SSH not ready on {self.ssh_alias} after {timeout}s")

    def _ssh_run_script(self, script: str) -> subprocess.CompletedProcess:
        """Run a bash script on the pod via SSH."""
        result = subprocess.run(
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
            check=False,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                f"ssh {self.ssh_alias} bash -s",
                result.stdout,
                result.stderr,
            )
        return result

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


def _get_aws_credentials(profile: str | None = None) -> dict[str, str]:
    """Get AWS credentials from aws CLI.

    When *profile* is provided, AWS_PROFILE is set in the subprocess env so
    `aws configure export-credentials` returns creds for that named profile
    instead of falling back to the shell's default. Avoids silently injecting
    the wrong account into managed pods.
    """
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
    try:
        result = subprocess.run(
            ["aws", "configure", "export-credentials", "--format", "env-no-export"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
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
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
PROFILED
chmod 644 /etc/profile.d/rp-env.sh
grep -q 'rp-env' /etc/bash.bashrc 2>/dev/null || cat >> /etc/bash.bashrc << 'SYSBASHRC'

# rp managed environment
[ -f "$HOME/.rp-env" ] && source "$HOME/.rp-env"
SYSBASHRC
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
