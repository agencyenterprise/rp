"""
Pydantic data models for the RunPod CLI wrapper.

This module defines the core data structures used throughout the application,
providing type safety, validation, and serialization capabilities.
"""

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class PodStatus(str, Enum):
    """Enumeration of possible pod statuses."""

    RUNNING = "running"
    STOPPED = "stopped"
    INVALID = "invalid"


class GPUSpec(BaseModel):
    """GPU specification for pod creation."""

    count: int = Field(ge=1, description="Number of GPUs")
    model: str = Field(description="GPU model (e.g., 'A100', 'H100')")

    @field_validator("model")
    @classmethod
    def validate_model(cls, v):
        if not v or not v.strip():
            raise ValueError("GPU model cannot be empty")
        return v.strip().upper()

    def __str__(self) -> str:
        return f"{self.count}x{self.model}"


class Pod(BaseModel):
    """Represents a RunPod instance with its metadata."""

    id: str = Field(description="RunPod instance ID")
    alias: str = Field(description="User-friendly alias for the pod")
    status: PodStatus = Field(description="Current status of the pod")
    name: str | None = Field(None, description="Pod name")
    image: str | None = Field(None, description="Docker image used")
    gpu_spec: GPUSpec | None = Field(None, description="GPU configuration")
    volume_gb: int | None = Field(None, description="Storage volume size in GB")
    container_disk_gb: int | None = Field(None, description="Container disk size in GB")

    # Network information (populated when pod is running)
    ip_address: str | None = Field(None, description="Public IP address")
    ssh_port: int | None = Field(None, description="SSH port number")

    # Timestamps
    created_at: datetime | None = Field(None, description="Pod creation time")
    updated_at: datetime | None = Field(None, description="Last update time")

    # Cost and usage
    cost_per_hour: float | None = Field(None, description="Cost per hour in USD")
    uptime_seconds: int | None = Field(None, description="Total uptime in seconds")

    @classmethod
    def from_alias_and_id(
        cls, alias: str, pod_id: str, status: PodStatus = PodStatus.INVALID
    ) -> "Pod":
        """Create a Pod instance from alias and ID."""
        return cls(id=pod_id, alias=alias, status=status)

    @classmethod
    def from_runpod_response(cls, alias: str, pod_data: dict) -> "Pod":
        """Create a Pod instance from RunPod API response."""
        pod_id = pod_data.get("id", "")

        # Determine status
        desired_status = str(pod_data.get("desiredStatus", "")).upper()
        if desired_status == "RUNNING":
            status = PodStatus.RUNNING
        elif desired_status == "EXITED":
            status = PodStatus.STOPPED
        else:
            status = PodStatus.INVALID

        # Extract network info
        ip_address = None
        ssh_port = None
        runtime = pod_data.get("runtime", {})
        if runtime and isinstance(runtime, dict):
            ports = runtime.get("ports", [])
            for port in ports:
                if port.get("privatePort") == 22 and port.get("isIpPublic"):
                    ip_address = port.get("ip")
                    ssh_port = port.get("publicPort")
                    break

        # Extract GPU information
        gpu_spec = None
        gpu_count = pod_data.get("gpuCount")
        machine = pod_data.get("machine", {})
        if machine and isinstance(machine, dict):
            gpu_type_id = machine.get("gpuTypeId", "")
            gpu_display_name = machine.get("gpuDisplayName", "")
            if gpu_count and (gpu_type_id or gpu_display_name):
                # Extract model name from display name or ID
                model = gpu_display_name or gpu_type_id
                # Clean up model name (e.g., "NVIDIA H100 PCIe" -> "H100 PCIE")
                model = model.replace("NVIDIA ", "").replace(" ", "")
                gpu_spec = GPUSpec(count=gpu_count, model=model)

        # Extract storage information
        volume_gb = pod_data.get("volumeInGb")
        container_disk_gb = pod_data.get("containerDiskInGb")

        # Extract cost information
        cost_per_hour = pod_data.get("costPerHr")

        # Extract uptime
        uptime_seconds = pod_data.get("uptimeSeconds")

        return cls(
            id=pod_id,
            alias=alias,
            status=status,
            name=pod_data.get("name"),
            image=pod_data.get("imageName"),
            gpu_spec=gpu_spec,
            volume_gb=volume_gb,
            container_disk_gb=container_disk_gb,
            ip_address=ip_address,
            ssh_port=ssh_port,
            cost_per_hour=cost_per_hour,
            uptime_seconds=uptime_seconds,
        )


class SSHConfig(BaseModel):
    """Represents SSH configuration for a pod."""

    alias: str = Field(description="Host alias")
    pod_id: str = Field(description="Associated pod ID")
    hostname: str = Field(description="SSH hostname/IP")
    port: int = Field(ge=1, le=65535, description="SSH port")
    user: str = Field(default="root", description="SSH username")
    identity_file: str | None = Field(default=None, description="SSH key file path")

    def to_ssh_block(self, updated_timestamp: str) -> list[str]:
        """Generate SSH config block lines."""
        lines = [
            f"Host {self.alias}\n",
            f"    # rp:managed alias={self.alias} pod_id={self.pod_id} updated={updated_timestamp}\n",
            f"    HostName {self.hostname}\n",
            f"    User {self.user}\n",
            f"    Port {self.port}\n",
        ]
        if self.identity_file:
            lines.append("    IdentitiesOnly yes\n")
            lines.append(f"    IdentityFile {self.identity_file}\n")
        lines.append("    ForwardAgent yes\n")
        lines.append("    StrictHostKeyChecking no\n")
        lines.append("    UserKnownHostsFile /dev/null\n")
        return lines


class PodCreateRequest(BaseModel):
    """Request model for creating a new pod."""

    alias: str = Field(description="Alias for the pod")
    gpu_spec: GPUSpec = Field(description="GPU specification")
    volume_gb: int = Field(ge=10, description="Storage volume size in GB")
    container_disk_gb: int = Field(
        default=20, ge=10, description="Container disk size in GB"
    )
    force: bool = Field(default=False, description="Overwrite existing alias")
    dry_run: bool = Field(default=False, description="Show actions without executing")

    # Pod configuration
    image: str = Field(
        default="runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
        description="Docker image to use",
    )
    ports: str = Field(default="22/tcp,8888/http", description="Port configuration")


class PodTemplate(BaseModel):
    """Template for creating pods with predefined configurations."""

    identifier: str = Field(description="Unique template identifier (e.g., 'alex-ast')")
    alias_template: str = Field(
        description="Alias template with {i} placeholder and optional variable "
        "placeholders (e.g., '{project}_{person}_{i}')"
    )
    gpu_spec: str = Field(description="GPU specification string (e.g., '2xA100')")
    storage_spec: str = Field(
        description="Storage specification string (e.g., '500GB')"
    )
    container_disk_spec: str | None = Field(
        default=None, description="Container disk specification string (e.g., '20GB')"
    )
    image: str | None = Field(
        default=None, description="Docker image to use (None uses default)"
    )

    @field_validator("alias_template")
    @classmethod
    def validate_alias_template(cls, v):
        if "{i}" not in v:
            raise ValueError("Alias template must contain '{i}' placeholder")
        return v

    def get_variable_names(self) -> list[str]:
        """Return placeholder names in the template, excluding 'i'."""
        return [
            name
            for name in re.findall(r"\{(\w+)\}", self.alias_template)
            if name != "i"
        ]

    def resolve_alias_template(self, template_vars: dict[str, str]) -> str:
        """Resolve variable placeholders in alias_template, leaving {i} intact.

        Raises ValueError if any required variable is missing from template_vars.
        """
        var_names = self.get_variable_names()
        missing = [name for name in var_names if name not in template_vars]
        if missing:
            raise ValueError(
                f"Template '{self.identifier}' requires variables {missing} "
                f"but they are not set. Define them in ~/.config/rp/.env or "
                f"as RP_-prefixed environment variables "
                f"(e.g. RP_{missing[0].upper()}=value)."
            )
        # Substitute all vars except {i}
        resolved = self.alias_template
        for name in var_names:
            resolved = resolved.replace(f"{{{name}}}", template_vars[name])
        return resolved


class PodMetadata(BaseModel):
    """Pod metadata including ID."""

    pod_id: str = Field(description="RunPod instance ID")
    managed: bool = Field(
        default=False, description="Whether this pod was created with 'rp up'"
    )


class AppConfig(BaseModel):
    """Application configuration and state."""

    pod_metadata: dict[str, PodMetadata] = Field(
        default_factory=dict, description="Pod metadata by alias"
    )
    pod_templates: dict[str, PodTemplate] = Field(
        default_factory=dict, description="Pod templates by identifier"
    )

    def add_alias(self, alias: str, pod_id: str, force: bool = False) -> bool:
        """Add or update an alias mapping."""
        existing_id = self.get_pod_id(alias)
        if existing_id is not None:
            if existing_id == pod_id:
                return True  # Already tracking same pod, idempotent
            if not force:
                return False

        self.pod_metadata[alias] = PodMetadata(pod_id=pod_id)
        return True

    def remove_alias(self, alias: str) -> str | None:
        """Remove an alias mapping, return the pod ID if it existed."""
        if alias in self.pod_metadata:
            metadata = self.pod_metadata.pop(alias)
            return metadata.pod_id
        return None

    def get_pod_id(self, alias: str) -> str | None:
        """Get pod ID for an alias."""
        if alias in self.pod_metadata:
            return self.pod_metadata[alias].pod_id
        return None

    def get_all_aliases(self) -> dict[str, str]:
        """Get all alias->pod_id mappings."""
        return {alias: meta.pod_id for alias, meta in self.pod_metadata.items()}

    def add_template(self, template: PodTemplate, force: bool = False) -> bool:
        """Add or update a pod template."""
        if template.identifier in self.pod_templates and not force:
            return False
        self.pod_templates[template.identifier] = template
        return True

    def get_template(self, identifier: str) -> PodTemplate | None:
        """Get a pod template by identifier."""
        return self.pod_templates.get(identifier)

    def remove_template(self, identifier: str) -> PodTemplate | None:
        """Remove a template, return the template if it existed."""
        return self.pod_templates.pop(identifier, None)

    def find_next_alias_index(self, alias_template: str) -> int:
        """Find the lowest i ≥ 1 where alias_template.format(i=i) doesn't exist."""
        all_aliases = self.get_all_aliases()
        i = 1
        while True:
            candidate_alias = alias_template.format(i=i)
            if candidate_alias not in all_aliases:
                return i
            i += 1
