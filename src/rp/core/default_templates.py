"""
Default pod templates shipped with rp.

These templates provide quick-start configurations for common GPU types.
Users can override these by creating templates with the same identifier.
"""

from rp.core.models import PodTemplate

# Default Docker image for all templates
DEFAULT_IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"

# Default container disk size
DEFAULT_CONTAINER_DISK = "500GB"


def get_default_templates() -> dict[str, PodTemplate]:
    """
    Get the built-in default templates.

    Returns:
        Dictionary mapping template identifiers to PodTemplate objects
    """
    return {
        "h100": PodTemplate(
            identifier="h100",
            alias_template="{project}_{person}_{i}",
            gpu_spec="h100",
            storage_spec="0GB",
            container_disk_spec=DEFAULT_CONTAINER_DISK,
            image=DEFAULT_IMAGE,
        ),
        "2h100": PodTemplate(
            identifier="2h100",
            alias_template="{project}_{person}_{i}",
            gpu_spec="2xh100",
            storage_spec="0GB",
            container_disk_spec=DEFAULT_CONTAINER_DISK,
            image=DEFAULT_IMAGE,
        ),
        "5090": PodTemplate(
            identifier="5090",
            alias_template="{project}_{person}_{i}",
            gpu_spec="rtx5090",
            storage_spec="0GB",
            container_disk_spec=DEFAULT_CONTAINER_DISK,
            image=DEFAULT_IMAGE,
        ),
        "4h100": PodTemplate(
            identifier="4h100",
            alias_template="{project}_{person}_{i}",
            gpu_spec="4xh100",
            storage_spec="0GB",
            container_disk_spec=DEFAULT_CONTAINER_DISK,
            image=DEFAULT_IMAGE,
        ),
        "a40": PodTemplate(
            identifier="a40",
            alias_template="{project}_{person}_{i}",
            gpu_spec="a40",
            storage_spec="0GB",
            container_disk_spec=DEFAULT_CONTAINER_DISK,
            image=DEFAULT_IMAGE,
        ),
    }


def is_default_template(identifier: str) -> bool:
    """Check if a template identifier refers to a default template."""
    return identifier in get_default_templates()
