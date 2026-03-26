"""
CLI command implementations using the service layer.

This module implements all the CLI commands using the refactored service layer,
providing clean separation between CLI interface and business logic.
"""

from pathlib import Path

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from rp.cli.utils import (
    console,
    display_pods_table,
    handle_cli_error,
    parse_gpu_spec,
    parse_storage_spec,
    run_setup_scripts,
    select_pod_if_needed,
    setup_api_client,
)
from rp.core.models import PodCreateRequest, PodTemplate, SSHConfig
from rp.core.pod_manager import PodManager
from rp.core.ssh_manager import SSHManager
from rp.utils.errors import AliasError, APIError, PodError

# Initialize services (will be properly injected in production)
_pod_manager: PodManager | None = None
_ssh_manager: SSHManager | None = None


def get_pod_manager() -> PodManager:
    """Get or create PodManager instance."""
    global _pod_manager  # noqa: PLW0603
    if _pod_manager is None:
        api_client = setup_api_client()
        _pod_manager = PodManager(api_client)
    return _pod_manager


def get_ssh_manager() -> SSHManager:
    """Get or create SSHManager instance."""
    global _ssh_manager  # noqa: PLW0603
    if _ssh_manager is None:
        _ssh_manager = SSHManager()
    return _ssh_manager


def _auto_clean() -> None:
    """Silently perform cleanup tasks (invalid aliases, SSH blocks)."""
    try:
        pod_manager = get_pod_manager()
        ssh_manager = get_ssh_manager()

        # Clean invalid aliases
        pod_manager.clean_invalid_aliases()

        # Prune SSH blocks
        valid_aliases = set(pod_manager.aliases.keys())
        ssh_manager.prune_managed_blocks(valid_aliases)
    except Exception:
        # Silently fail - don't disrupt the user's workflow
        pass


def create_command(  # noqa: PLR0915  # Function complexity acceptable for main command
    alias: str | None = None,
    gpu: str | None = None,
    storage: str | None = None,
    container_disk: str | None = None,
    template: str | None = None,
    image: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    network_volume: str | None = None,
) -> None:
    """Create a new RunPod using PyTorch 2.8 image."""
    try:
        pod_manager = get_pod_manager()

        # Validate arguments
        if not template and not (alias and gpu and storage):
            raise ValueError(
                "Must specify either a template (as first argument) or all of (--alias, --gpu, --storage)"
            )

        if template:
            # Use template mode (with optional alias override)
            if alias:
                console.print(
                    f"🚀 Creating pod '[bold]{alias}[/bold]' from template '[bold]{template}[/bold]'"
                )
            else:
                console.print(
                    f"🚀 Creating pod from template '[bold]{template}[/bold]'"
                )

            if dry_run:
                # Show what would be created
                from rp.config import load_template_vars

                template_obj = pod_manager.get_template(template)
                if alias:
                    proposed_alias = alias
                else:
                    resolved_template = template_obj.resolve_alias_template(
                        load_template_vars()
                    )
                    next_index = pod_manager.config.find_next_alias_index(
                        resolved_template
                    )
                    proposed_alias = resolved_template.format(i=next_index)

                console.print("[bold]DRY RUN[/bold] Would create:")
                console.print(f"   Alias: {proposed_alias}")
                console.print(f"   GPU: {template_obj.gpu_spec}")
                console.print(f"   Storage: {template_obj.storage_spec}")
                return

            # Create pod with progress indication
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Creating pod from template…", total=None)
                pod = pod_manager.create_pod_from_template(
                    template,
                    force,
                    dry_run,
                    alias_override=alias,
                    network_volume_id=network_volume,
                )
                progress.update(task, description="Pod created successfully")

            final_alias = pod.alias
            template_used = template
        else:
            # Use direct specification mode - at this point we know these are not None due to validation
            assert alias is not None
            assert gpu is not None
            assert storage is not None

            gpu_spec = parse_gpu_spec(gpu)
            volume_gb = parse_storage_spec(storage)

            container_disk_gb = (
                parse_storage_spec(container_disk) if container_disk is not None else 20
            )

            request = PodCreateRequest(
                alias=alias,
                gpu_spec=gpu_spec,
                volume_gb=volume_gb,
                force=force,
                dry_run=dry_run,
                container_disk_gb=container_disk_gb,
                image=image or PodCreateRequest.model_fields["image"].default,
                network_volume_id=network_volume,
            )

            # Build storage description
            if network_volume:
                storage_desc = f"network_volume={network_volume}"
            elif volume_gb > 0:
                storage_desc = f"volume={volume_gb}GB"
            else:
                storage_desc = "no volume"

            console.print(
                f"🚀 Creating pod '[bold]{alias}[/bold]': "
                f"image=[dim]{request.image}[/dim], "
                f"GPU={gpu_spec}, {storage_desc}, container_disk={request.container_disk_gb}GB"
            )

            if dry_run:
                console.print("[bold]DRY RUN[/bold] No changes were made.")
                return

            # Create pod with progress indication
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Creating pod…", total=None)
                pod = pod_manager.create_pod(request)
                progress.update(task, description="Pod created successfully")

            final_alias = alias
            template_used = None
            # Store for summary
            final_gpu_spec = gpu_spec
            final_volume_gb = volume_gb

        # At this point final_alias should never be None
        assert final_alias is not None

        console.print(f"✅ Saved alias '[bold]{final_alias}[/bold]' -> {pod.id}")

        # Configure SSH
        if pod.ip_address and pod.ssh_port:
            console.print("📝 Updating SSH config…")
            ssh_config = SSHConfig(
                alias=final_alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")

        # Run setup scripts
        run_setup_scripts(final_alias)

        # Print summary
        if template_used:
            console.print(
                f"🎉 Created pod '[bold green]{final_alias}[/bold green]' from template '[bold blue]{template_used}[/bold blue]'"
            )
        else:
            console.print(
                f"🎉 Created pod '[bold green]{final_alias}[/bold green]' with [bold yellow]{final_gpu_spec}[/bold yellow] GPU"
                + (
                    f" and [bold yellow]{final_volume_gb}GB[/bold yellow] storage"
                    if final_volume_gb > 0
                    else ""
                )
            )

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def up_command(
    template: str | None = None,
    alias: str | None = None,
    gpu: str | None = None,
    storage: str | None = None,
    force: bool = False,
    network_volume: str | None = None,
) -> None:
    """Create a pod with full opinionated setup (tools, secrets, auto-shutdown)."""
    try:
        pod_manager = get_pod_manager()

        if not template and not (alias and gpu and storage):
            raise ValueError(
                "Must specify either a template or all of (--alias, --gpu, --storage)"
            )

        # Create the pod using existing create logic
        if template:
            console.print(
                f"🚀 Creating managed pod from template '[bold]{template}[/bold]'"
            )
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Creating pod from template…", total=None)
                pod = pod_manager.create_pod_from_template(
                    template,
                    force,
                    dry_run=False,
                    alias_override=alias,
                    network_volume_id=network_volume,
                )
                progress.update(task, description="Pod created successfully")
            final_alias = pod.alias
        else:
            assert alias is not None
            assert gpu is not None
            assert storage is not None

            gpu_spec = parse_gpu_spec(gpu)
            volume_gb = parse_storage_spec(storage)
            request = PodCreateRequest(
                alias=alias,
                gpu_spec=gpu_spec,
                volume_gb=volume_gb,
                force=force,
                network_volume_id=network_volume,
            )
            console.print(f"🚀 Creating managed pod '[bold]{alias}[/bold]'")
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Creating pod…", total=None)
                pod = pod_manager.create_pod(request)
                progress.update(task, description="Pod created successfully")
            final_alias = alias

        # Mark as managed
        pod_manager.set_managed(final_alias, managed=True)

        console.print(f"✅ Saved alias '[bold]{final_alias}[/bold]' -> {pod.id}")

        # Configure SSH immediately (even before confirming SSH works)
        # so the user can `ssh` in manually if setup fails
        if pod.ip_address and pod.ssh_port:
            ssh_config = SSHConfig(
                alias=final_alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated.")

        # Run opinionated setup — if this fails, the pod is still tracked
        # and the user can retry with `rp setup <alias>`
        try:
            from rp.core.pod_setup import PodSetup

            console.print("⚙️  Running managed setup…")
            setup = PodSetup(final_alias, pod.id, console)
            setup.run_full_setup()

            console.print(
                f"🎉 Managed pod '[bold green]{final_alias}[/bold green]' is ready."
            )
        except Exception as setup_err:
            console.print(
                f"\n[bold yellow]⚠️  Pod created but setup failed:[/bold yellow] {setup_err}",
            )
            console.print(
                f"    Pod is running and tracked as '[bold]{final_alias}[/bold]'."
            )
            console.print(
                f"    Run [bold green]rp setup {final_alias}[/bold green] to retry setup."
            )

        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def start_command(alias: str | None) -> None:
    """Start/resume a RunPod instance."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)

        console.print(f"🚀 Starting pod '[bold]{alias}[/bold]'…")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("Starting pod…", total=None)
            pod = pod_manager.start_pod(alias)
            progress.update(task, description="Pod is running")

        console.print("✅ Pod is now [bold green]RUNNING[/bold green].")

        # Update SSH config
        if pod.ip_address and pod.ssh_port:
            console.print(f"Found IP: [bold]{pod.ip_address}[/bold]")
            console.print(f"Found Port: [bold]{pod.ssh_port}[/bold]")

            ssh_config = SSHConfig(
                alias=alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")

        # Check if this is a managed pod — re-inject secrets and auto-shutdown
        metadata = pod_manager.config.pod_metadata.get(alias)
        if metadata and metadata.managed:
            from rp.core.pod_setup import PodSetup

            console.print("⚙️  Re-running managed setup…")
            setup = PodSetup(alias, pod.id, console)
            setup.run_managed_restart_setup()
        else:
            # Run legacy setup scripts for non-managed pods
            run_setup_scripts(alias)

        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def stop_command(alias: str | None) -> None:
    """Stop a RunPod instance."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_manager.get_pod_id(alias)  # Raises if not found

        console.print(f"🛑 Stopping pod '[bold]{alias}[/bold]'…")
        pod_manager.stop_pod(alias)
        console.print("✅ Pod has been stopped.")

        # Remove SSH config
        ssh_manager = get_ssh_manager()
        removed = ssh_manager.remove_host_config(alias)
        if removed:
            console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")

        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def destroy_command(alias: str | None, force: bool = False) -> None:
    """Terminate a pod, remove SSH config, and delete the alias."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)

        # Confirm destruction unless force is set
        if not force:
            response = typer.confirm(
                f"⚠️  Are you sure you want to destroy pod '{alias}'? This action cannot be undone."
            )
            if not response:
                console.print("❌ Destruction cancelled.")
                raise typer.Exit(0)

        console.print(f"🔥 Destroying pod '[bold]{alias}[/bold]'…")
        pod_id = pod_manager.destroy_pod(alias)
        console.print(f"✅ Terminated pod [bold]{pod_id}[/bold].")

        # Clean SSH config
        ssh_manager = get_ssh_manager()
        removed = ssh_manager.remove_host_config(alias)
        if removed:
            console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")

        console.print(
            f"🗑️  Removed alias '[bold]{alias}[/bold]' from local configuration."
        )

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def track_command(alias: str | None, pod_id: str, force: bool = False) -> None:
    """Track an existing RunPod pod with an alias."""
    try:
        pod_manager = get_pod_manager()
        api_client = setup_api_client()

        # Try to resolve pod_id — if it doesn't look like an ID, search by name
        resolved_by_name = False
        pod_data = None
        try:
            pod_data = api_client.get_pod(pod_id)
        except (APIError, PodError):
            # pod_id might be a pod name, try to find by name
            pod_data = api_client.find_pod_by_name(pod_id)
            if pod_data:
                pod_id = pod_data["id"]
                resolved_by_name = True
            else:
                raise AliasError(
                    f"Could not find pod with ID or name '{pod_id}'",
                    details="Check the pod ID/name and try again.",
                ) from None

        # If no alias provided, use the pod's name
        if alias is None:
            alias = pod_data.get("name", pod_id)

        pod_manager.add_alias(alias, pod_id, force)

        if resolved_by_name:
            console.print(f"ℹ️  Resolved pod name '[bold]{alias}[/bold]' to ID {pod_id}")
        console.print(f"✅ Now tracking '[bold]{alias}[/bold]' -> {pod_id}")

        # Update SSH config if pod is running
        pod = pod_manager.get_pod(alias)
        if pod.ip_address and pod.ssh_port:
            console.print(f"Found IP: [bold]{pod.ip_address}[/bold]")
            console.print(f"Found Port: [bold]{pod.ssh_port}[/bold]")

            ssh_config = SSHConfig(
                alias=alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")
        else:
            console.print("ℹ️  Pod is not running, SSH config not updated.")

    except Exception as e:
        handle_cli_error(e)


def untrack_command(alias: str | None, missing_ok: bool = False) -> None:
    """Stop tracking a pod (removes alias mapping)."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.remove_alias(alias, missing_ok)

        if pod_id:
            console.print(f"✅ Stopped tracking '[bold]{alias}[/bold]' (was {pod_id})")
        else:
            console.print(f"i  Alias '[bold]{alias}[/bold]' not found; nothing to do.")

    except Exception as e:
        handle_cli_error(e)


def list_command() -> None:
    """List all aliases with their status."""
    try:
        pod_manager = get_pod_manager()
        pods = pod_manager.list_pods()
        display_pods_table(pods)

    except Exception as e:
        handle_cli_error(e)


def show_command(alias: str | None) -> None:
    """Show detailed information about a pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)

        # Get pod details
        pod = pod_manager.get_pod(alias)

        console.print(f"\n[bold cyan]Pod Details: {alias}[/bold cyan]")
        console.print("=" * 60)

        # Basic info
        console.print(f"[bold]ID:[/bold]        {pod.id}")
        console.print(f"[bold]Status:[/bold]    {pod.status.value.upper()}")

        # GPU info
        if pod.gpu_spec:
            console.print(f"[bold]GPU:[/bold]       {pod.gpu_spec}")
        else:
            console.print("[bold]GPU:[/bold]       [dim](unknown)[/dim]")

        # Storage info
        if pod.volume_gb:
            console.print(f"[bold]Volume:[/bold]    {pod.volume_gb}GB")
        else:
            console.print("[bold]Volume:[/bold]    [dim]none[/dim]")

        if pod.container_disk_gb:
            console.print(f"[bold]Container:[/bold]  {pod.container_disk_gb}GB")

        # Cost info
        if pod.cost_per_hour:
            console.print(f"[bold]Cost:[/bold]      ${pod.cost_per_hour:.3f}/hour")
        else:
            console.print("[bold]Cost:[/bold]      [dim](unknown)[/dim]")

        # Network info (if running)
        if pod.ip_address and pod.ssh_port:
            console.print(f"[bold]IP:[/bold]        {pod.ip_address}:{pod.ssh_port}")

        # Image info
        if pod.image:
            # Truncate long image names
            image_display = (
                pod.image if len(pod.image) <= 50 else pod.image[:47] + "..."
            )
            console.print(f"[bold]Image:[/bold]     {image_display}")

        console.print("=" * 60 + "\n")

    except Exception as e:
        handle_cli_error(e)


def clean_command() -> None:
    """Remove invalid aliases and prune SSH blocks."""
    try:
        pod_manager = get_pod_manager()
        removed_aliases = pod_manager.clean_invalid_aliases()

        if removed_aliases:
            console.print(
                f"✅ Removed [bold]{removed_aliases}[/bold] invalid alias(es)."
            )
        else:
            console.print("✅ No invalid aliases found.")

        # Prune SSH blocks
        ssh_manager = get_ssh_manager()
        valid_aliases = set(pod_manager.aliases.keys())
        removed_blocks = ssh_manager.prune_managed_blocks(valid_aliases)

        if removed_blocks:
            console.print(
                f"🧹 Removed [bold]{removed_blocks}[/bold] orphaned SSH config blocks."
            )
        else:
            console.print("✅ No orphaned SSH config blocks to prune.")

    except Exception as e:
        handle_cli_error(e)


def template_create_command(
    identifier: str,
    alias_template: str,
    gpu: str,
    storage: str,
    container_disk: str | None = None,
    image: str | None = None,
    network_volume: str | None = None,
    force: bool = False,
) -> None:
    """Create a new pod template."""
    try:
        template_kwargs: dict[str, str] = {
            "identifier": identifier,
            "alias_template": alias_template,
            "gpu_spec": gpu,
            "storage_spec": storage,
        }

        if container_disk is not None:
            template_kwargs["container_disk_spec"] = container_disk

        if image is not None:
            template_kwargs["image"] = image

        if network_volume is not None:
            template_kwargs["network_volume_id"] = network_volume

        template = PodTemplate(**template_kwargs)

        pod_manager = get_pod_manager()
        pod_manager.add_template(template, force)

        console.print(f"✅ Created template '[bold]{identifier}[/bold]'")
        console.print(f"   Alias template: {alias_template}")
        console.print(f"   GPU: {gpu}")
        console.print(f"   Storage: {storage}")
        if container_disk is not None:
            console.print(f"   Container disk: {container_disk}")
        if image is not None:
            console.print(f"   Image: {image}")
        if network_volume is not None:
            console.print(f"   Network volume: {network_volume}")

    except Exception as e:
        handle_cli_error(e)


def template_list_command() -> None:
    """List all pod templates."""
    try:
        from rp.core.default_templates import is_default_template

        pod_manager = get_pod_manager()
        templates = pod_manager.list_templates()

        if not templates:
            console.print("No templates found.")
            return

        from rich.table import Table

        table = Table(title="Pod Templates")
        table.add_column("Identifier", style="cyan", no_wrap=True)
        table.add_column("Alias Template", style="magenta")
        table.add_column("GPU", style="green")
        table.add_column("Storage", style="yellow")
        table.add_column("Container Disk", style="yellow")
        table.add_column("Network Volume", style="yellow")
        table.add_column("Image", style="blue")
        table.add_column("Source", style="dim")

        for template in templates:
            image_display = template.image if template.image else "(default)"
            container_disk_display = (
                template.container_disk_spec
                if template.container_disk_spec
                else "(default: 20GB)"
            )
            nv_display = template.network_volume_id or "-"
            source = "default" if is_default_template(template.identifier) else "user"
            table.add_row(
                template.identifier,
                template.alias_template,
                template.gpu_spec,
                template.storage_spec,
                container_disk_display,
                nv_display,
                image_display,
                source,
            )

        console.print(table)

    except Exception as e:
        handle_cli_error(e)


def template_delete_command(identifier: str, missing_ok: bool = False) -> None:
    """Delete a pod template."""
    try:
        pod_manager = get_pod_manager()
        template = pod_manager.remove_template(identifier, missing_ok)

        if template:
            console.print(f"✅ Deleted template '[bold]{identifier}[/bold]'")
        else:
            console.print(
                f"i  Template '[bold]{identifier}[/bold]' not found; nothing to do."
            )

    except Exception as e:
        handle_cli_error(e)


def code_command(alias: str | None, path: str | None = None) -> None:
    """Open VS Code editor with remote SSH connection to pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_manager.get_pod_id(alias)  # Validate alias exists

        import subprocess

        if path is None:
            path = "/workspace"

        remote_uri = f"vscode-remote://ssh-remote+{alias}{path}"
        console.print(f"🖥️  Opening VS Code at '[bold]{alias}:{path}[/bold]'…")

        subprocess.run(["code", "--folder-uri", remote_uri], check=True)
        console.print("✅ VS Code opened successfully.")

    except FileNotFoundError:
        console.print(
            "❌ VS Code command not found. Please ensure VS Code is installed and 'code' is in your PATH.",
            style="red",
        )
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e)


def shell_command(alias: str | None) -> None:
    """Open an interactive SSH shell to the pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_manager.get_pod_id(alias)  # Validate alias exists

        import subprocess

        console.print(f"🐚 Connecting to '[bold]{alias}[/bold]'…")
        subprocess.run(["ssh", "-A", alias], check=False)

    except Exception as e:
        handle_cli_error(e)


def run_command(alias: str | None, command: list[str]) -> None:
    """Execute a command on a remote pod via SSH."""
    try:
        import shlex
        import subprocess

        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_manager.get_pod_id(alias)  # Validate alias exists

        full_command = " ".join(command)
        console.print(f"Running on '[bold]{alias}[/bold]': {full_command}")
        subprocess.run(
            ["ssh", "-A", alias, f"bash -l -c {shlex.quote(full_command)}"],
            check=False,
        )

    except Exception as e:
        handle_cli_error(e)


def setup_command(alias: str | None) -> None:
    """Re-run pod setup on an existing pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.get_pod_id(alias)

        metadata = pod_manager.config.pod_metadata.get(alias)

        if metadata and metadata.managed:
            from rp.core.pod_setup import PodSetup

            console.print(f"⚙️  Running managed setup on '[bold]{alias}[/bold]'…")
            setup = PodSetup(alias, pod_id, console)
            setup.run_full_setup()
        else:
            console.print(f"⚙️  Running setup scripts on '[bold]{alias}[/bold]'…")
            run_setup_scripts(alias)

        console.print(f"✅ Setup complete for '[bold green]{alias}[/bold green]'.")

    except Exception as e:
        handle_cli_error(e)


def secrets_list_command(as_json: bool = False) -> None:
    """List secrets resolved from .rp_settings.json hierarchy."""
    try:
        from rp.core.secret_manager import SecretManager
        from rp.core.settings import resolve_settings

        sm = SecretManager()
        resolved = resolve_settings()

        # Fall back to legacy manifest if no .rp_settings.json hierarchy found
        if not resolved.secrets:
            legacy_names = sm.list_names()
            if not legacy_names:
                console.print(
                    "[yellow]No secrets stored. Use 'rp secrets set <name>' to add one.[/yellow]"
                )
                return

            if as_json:
                import json

                data = [
                    {
                        "name": name,
                        "source": "~/.config/rp/secrets.json (legacy)",
                        "set": sm.exists(name),
                    }
                    for name in legacy_names
                ]
                console.print(json.dumps(data, indent=2))
                return

            from rich.table import Table

            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Name", style="green")
            table.add_column("Source", style="dim")
            table.add_column("Set", style="white")

            for name in legacy_names:
                has_value = "yes" if sm.exists(name) else "missing from keychain"
                table.add_row(name, "legacy manifest", has_value)

            console.print(table)
            console.print(
                "\n[dim]These secrets are from the legacy central manifest. "
                "Use 'rp secrets set <name>' in a project directory to migrate.[/dim]"
            )
            return

        if as_json:
            import json

            data = [
                {
                    "name": s.name,
                    "source": str(s.source_dir),
                    "set": sm.get_resolved(s) is not None,
                }
                for s in resolved.secrets
            ]
            if resolved.person:
                console.print(
                    json.dumps(
                        {
                            "person": resolved.person,
                            "project": resolved.project,
                            "secrets": data,
                        },
                        indent=2,
                    )
                )
            else:
                console.print(json.dumps({"secrets": data}, indent=2))
            return

        from rich.table import Table

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Source", style="dim")
        table.add_column("Set", style="white")

        for secret in resolved.secrets:
            source = str(secret.source_dir).replace(str(Path.home()), "~")
            has_value = (
                "yes"
                if sm.get_resolved(secret) is not None
                else "missing from keychain"
            )
            table.add_row(secret.name, source, has_value)

        console.print(table)

        if resolved.person or resolved.project:
            parts = []
            if resolved.project:
                parts.append(f"project=[bold]{resolved.project}[/bold]")
            if resolved.person:
                parts.append(f"person=[bold]{resolved.person}[/bold]")
            console.print(f"\n  Settings: {', '.join(parts)}")

    except Exception as e:
        handle_cli_error(e)


def secrets_set_command(
    name: str, value: str | None = None, is_global: bool = False
) -> None:
    """Store a secret in macOS Keychain, scoped to a .rp_settings.json file.

    By default, writes to the nearest .rp_settings.json walking up from cwd.
    If none exists, creates one in cwd. Use --global to write to ~/.rp_settings.json.
    """
    try:
        import sys

        from rp.core.secret_manager import SecretManager
        from rp.core.settings import find_nearest_settings_file

        if value is not None:
            # Value provided via --value flag
            secret_value = value.strip()
        elif not sys.stdin.isatty():
            # Piped input: echo "token" | rp secrets set NAME
            secret_value = sys.stdin.read().strip()
        else:
            # Interactive prompt
            import getpass

            secret_value = getpass.getpass(f"Enter value for '{name}': ").strip()

        if not secret_value:
            console.print("❌ Empty value provided.", style="red")
            raise typer.Exit(1)

        if is_global:
            target_dir = Path.home()
        else:
            nearest = find_nearest_settings_file()
            target_dir = nearest.parent if nearest is not None else Path.cwd()

        sm = SecretManager()
        sm.set(name, secret_value, source_dir=target_dir)
        display_dir = str(target_dir).replace(str(Path.home()), "~")
        console.print(f"✅ Stored '{name}' in Keychain (scope: {display_dir}).")

    except (EOFError, KeyboardInterrupt):
        console.print("\n❌ Cancelled.", style="red")
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e)


def secrets_remove_command(name: str, is_global: bool = False) -> None:
    """Remove a secret from Keychain and its .rp_settings.json entry."""
    try:
        from rp.core.secret_manager import SecretManager
        from rp.core.settings import resolve_settings

        sm = SecretManager()

        if is_global:
            target_dir = Path.home()
            if sm.remove(name, source_dir=target_dir):
                console.print(f"✅ Removed '{name}' from Keychain (scope: ~).")
            else:
                console.print(f"ℹ️  '{name}' not found in Keychain at global scope.")
            return

        # Find which level defines this secret
        resolved = resolve_settings()
        for secret in resolved.secrets:
            if secret.name == name:
                if sm.remove(name, source_dir=secret.source_dir):
                    display_dir = str(secret.source_dir).replace(str(Path.home()), "~")
                    console.print(
                        f"✅ Removed '{name}' from Keychain (scope: {display_dir})."
                    )
                else:
                    console.print(f"ℹ️  '{name}' not found in Keychain.")
                return

        # Fall back to legacy removal
        if sm.remove(name):
            console.print(f"✅ Removed '{name}' from Keychain (legacy).")
        else:
            console.print(f"ℹ️  '{name}' not found.")

    except Exception as e:
        handle_cli_error(e)


def secrets_inject_command(alias: str | None) -> None:
    """Push secrets from Keychain to a running pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)

        pod_id = pod_manager.get_pod_id(alias)

        from rp.core.pod_setup import PodSetup

        setup = PodSetup(alias, pod_id, console)
        setup.inject_secrets()
        console.print(f"✅ Secrets injected into '[bold]{alias}[/bold]'.")

    except Exception as e:
        handle_cli_error(e)


def claude_command(
    alias: str | None,
    prompt: str | None = None,
    working_dir: str | None = None,
) -> None:
    """Launch remote Claude on a managed pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.get_pod_id(alias)

        from rp.core.claude_remote import ClaudeRemote

        remote = ClaudeRemote(alias, pod_id, console)
        remote.launch(
            working_dir=working_dir or "/workspace",
            prompt=prompt,
        )

    except Exception as e:
        handle_cli_error(e)


def status_command(alias: str | None) -> None:
    """Check remote Claude progress on a pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.get_pod_id(alias)

        from rp.core.claude_remote import ClaudeRemote

        remote = ClaudeRemote(alias, pod_id, console)
        status = remote.get_status()

        if status["running"]:
            console.print("[bold green]STATUS: running[/bold green]")
        else:
            console.print("[bold yellow]STATUS: finished[/bold yellow]")

        if status["output"]:
            console.print("\n--- Recent output ---")
            console.print(status["output"])

        if status["report"]:
            console.print("\n--- Report ---")
            console.print(status["report"])

    except Exception as e:
        handle_cli_error(e)


def logs_command(alias: str | None) -> None:
    """Sync and view logs from a remote pod."""
    try:
        pod_manager = get_pod_manager()
        alias = select_pod_if_needed(alias, pod_manager)
        pod_id = pod_manager.get_pod_id(alias)

        from rp.core.claude_remote import ClaudeRemote

        remote = ClaudeRemote(alias, pod_id, console)
        local_dir = remote.sync_logs()
        console.print(f"✅ Logs synced to [bold]{local_dir}[/bold]")

    except Exception as e:
        handle_cli_error(e)
