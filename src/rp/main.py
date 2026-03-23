"""
Main entry point for the RunPod CLI wrapper (refactored version).

This module provides the main application entry point and command-line interface
using the refactored service layer architecture.
"""

import json

import click
import typer
from typer.core import TyperGroup

from rp.cli.commands import (
    claude_command,
    clean_command,
    code_command,
    create_command,
    destroy_command,
    list_command,
    logs_command,
    run_command,
    secrets_inject_command,
    secrets_list_command,
    secrets_remove_command,
    secrets_set_command,
    setup_command,
    shell_command,
    show_command,
    start_command,
    status_command,
    stop_command,
    template_create_command,
    template_delete_command,
    template_list_command,
    track_command,
    untrack_command,
    up_command,
)
from rp.cli.utils import console
from rp.config import POD_CONFIG_FILE
from rp.core.models import AppConfig


def complete_alias(incomplete: str) -> list[str]:
    """Provide tab completion for pod aliases."""
    try:
        with POD_CONFIG_FILE.open("r") as f:
            config = AppConfig.model_validate(json.load(f))
        return [a for a in config.get_all_aliases() if a.startswith(incomplete)]
    except Exception:
        return []


def complete_template(incomplete: str) -> list[str]:
    """Provide tab completion for template identifiers."""
    try:
        with POD_CONFIG_FILE.open("r") as f:
            config = AppConfig.model_validate(json.load(f))
        return [t for t in config.pod_templates if t.startswith(incomplete)]
    except Exception:
        return []


class OrderedGroup(TyperGroup):
    """Custom group to control command order in help."""

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: ARG002
        preferred = ["create", "destroy", "track"]
        all_cmds = list(self.commands.keys())
        rest = [c for c in all_cmds if c not in preferred]
        return preferred + rest


# Main application
app = typer.Typer(
    help="RunPod utility for starting and stopping pods", cls=OrderedGroup
)

# Template sub-application
template_app = typer.Typer(help="Manage pod templates")

# Secrets sub-application
secrets_app = typer.Typer(help="Manage secrets stored in macOS Keychain")


@app.command()
def create(
    template: str = typer.Argument(
        None,
        help="Template identifier to use (e.g., 'training-template')",
        autocompletion=complete_template,
    ),
    alias: str = typer.Option(
        None, "--alias", help="SSH host alias to assign (e.g., alexs-machine)"
    ),
    gpu: str = typer.Option(None, "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        None, "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    container_disk: str = typer.Option(
        None, "--container-disk", help="Container disk size like '20GB' (default: 20GB)"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite alias if it exists"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show actions without creating"
    ),
):
    """Create a new RunPod instance, add alias, wait for SSH, and run setup scripts."""
    create_command(alias, gpu, storage, container_disk, template, image, force, dry_run)


@app.command()
def up(
    template: str = typer.Argument(
        None,
        help="Template identifier to use",
        autocompletion=complete_template,
    ),
    alias: str = typer.Option(None, "--alias", help="SSH host alias to assign"),
    gpu: str = typer.Option(None, "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        None, "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite alias if it exists"
    ),
):
    """Create a pod with full opinionated setup (tools, secrets, auto-shutdown)."""
    up_command(template, alias, gpu, storage, force)


@app.command()
def start(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
):
    """Start and configure a RunPod instance."""
    start_command(host_alias)


@app.command()
def stop(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
):
    """Stop a RunPod instance."""
    stop_command(host_alias)


@app.command()
def destroy(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
):
    """Terminate a pod, remove SSH config, and delete the alias mapping."""
    destroy_command(host_alias, force)


@app.command()
def track(
    pod_id: str = typer.Argument(..., help="RunPod pod ID or pod name"),
    alias: str = typer.Argument(
        None, help="Alias name (optional, defaults to pod name)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite if alias already exists"
    ),
):
    """Track an existing RunPod pod with an alias."""
    track_command(alias, pod_id, force)


@app.command()
def untrack(
    alias: str = typer.Argument(
        None, help="Alias name to remove", autocompletion=complete_alias
    ),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if alias is missing"
    ),
):
    """Stop tracking a pod (removes alias mapping)."""
    untrack_command(alias, missing_ok)


@app.command("list")
def list_aliases():
    """List all aliases as a table: Alias, ID, Status (running, stopped, invalid)."""
    list_command()


@app.command()
def show(
    alias: str = typer.Argument(
        None, help="Pod alias to show details for", autocompletion=complete_alias
    ),
):
    """Show detailed information about a pod."""
    show_command(alias)


@app.command()
def clean():
    """Remove invalid aliases and prune rp-managed SSH blocks no longer valid."""
    clean_command()


@app.command()
def setup(
    alias: str = typer.Argument(
        None,
        help="Pod alias to run setup on",
        autocompletion=complete_alias,
    ),
):
    """Re-run pod setup (tools, secrets, auto-shutdown). Useful for recovery after partial failures."""
    setup_command(alias)


@template_app.command("create")
def template_create(
    identifier: str = typer.Argument(
        ..., help="Template identifier (e.g., 'alex-ast')"
    ),
    alias_pattern: str = typer.Option(
        ...,
        "--alias-pattern",
        help="Alias pattern with {i} placeholder (e.g., 'alex-ast-{i}')",
    ),
    gpu: str = typer.Option(..., "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        ..., "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    container_disk: str = typer.Option(
        None, "--container-disk", help="Container disk size like '20GB' (default: 20GB)"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite template if it exists"
    ),
):
    """Create a new pod template."""
    template_create_command(
        identifier, alias_pattern, gpu, storage, container_disk, image, force
    )


@template_app.command("list")
def template_list():
    """List all pod templates."""
    template_list_command()


@template_app.command("delete")
def template_delete(
    identifier: str = typer.Argument(
        ..., help="Template identifier to delete", autocompletion=complete_template
    ),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if template is missing"
    ),
):
    """Delete a pod template."""
    template_delete_command(identifier, missing_ok)


@app.command()
def code(
    alias: str = typer.Argument(
        None, help="Pod alias to connect to", autocompletion=complete_alias
    ),
    path: str = typer.Argument(None, help="Remote path to open (default: /workspace)"),
):
    """Open VS Code editor with remote SSH connection to pod."""
    code_command(alias, path)


@app.command()
def shell(
    alias: str = typer.Argument(
        None, help="Pod alias to connect to", autocompletion=complete_alias
    ),
):
    """Open an interactive SSH shell to the pod."""
    shell_command(alias)


@app.command(
    "run",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)
def run(
    ctx: typer.Context,
    alias: str = typer.Argument(
        ..., help="Pod alias to run command on", autocompletion=complete_alias
    ),
):
    """Execute a command on a remote pod via SSH.

    Example: rp run my-pod -- ls -la /workspace
    """
    args = [a for a in ctx.args if a != "--"]
    if not args:
        console.print(
            "❌ No command specified. Usage: rp run <alias> <command>", style="red"
        )
        raise typer.Exit(1)
    run_command(alias, args)


@app.command("claude")
def claude_cmd(
    alias: str = typer.Argument(None, help="Pod alias", autocompletion=complete_alias),
    prompt: str = typer.Option(
        None, "--prompt", "-p", help="Prompt for autonomous mode"
    ),
    working_dir: str = typer.Option(
        None, "--dir", "-d", help="Working directory on pod"
    ),
):
    """Launch remote Claude on a pod."""
    claude_command(alias, prompt, working_dir)


@app.command()
def status(
    alias: str = typer.Argument(None, help="Pod alias", autocompletion=complete_alias),
):
    """Check remote Claude progress on a pod."""
    status_command(alias)


@app.command()
def logs(
    alias: str = typer.Argument(None, help="Pod alias", autocompletion=complete_alias),
):
    """Sync and view logs from a remote pod."""
    logs_command(alias)


@secrets_app.command("list")
def secrets_list():
    """List managed secrets."""
    secrets_list_command()


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name (e.g., HF_TOKEN)"),
    value: str = typer.Option(
        None, "--value", help="Secret value (if omitted, reads from stdin or prompts)"
    ),
):
    """Store a secret in macOS Keychain."""
    secrets_set_command(name, value)


@secrets_app.command("remove")
def secrets_remove(
    name: str = typer.Argument(..., help="Secret name to remove"),
):
    """Remove a secret from macOS Keychain."""
    secrets_remove_command(name)


@secrets_app.command("inject")
def secrets_inject(
    alias: str = typer.Argument(None, help="Pod alias", autocompletion=complete_alias),
):
    """Push secrets from Keychain to a running pod."""
    secrets_inject_command(alias)


def main():
    """Main entry point."""
    app.add_typer(template_app, name="template")
    app.add_typer(secrets_app, name="secrets")
    app()


if __name__ == "__main__":
    main()
