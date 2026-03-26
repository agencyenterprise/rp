"""Hierarchical settings resolution via .rp_settings.json files.

Walks from a starting directory up to filesystem root, collecting and merging
.rp_settings.json files. Closer files win for scalar values; for secrets,
closer entries override same-named entries from further up the tree.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

SETTINGS_FILENAME = ".rp_settings.json"


class RpSettings(BaseModel):
    """Contents of a single .rp_settings.json file."""

    person: str | None = Field(
        default=None, description="Person name for cost tracking"
    )
    project: str | None = Field(
        default=None, description="Project name for cost tracking"
    )
    secrets: list[str] = Field(
        default_factory=list, description="Secret env var names managed at this level"
    )


class ResolvedSecret:
    """A secret with its resolved source directory (for keychain key encoding)."""

    __slots__ = ("name", "source_dir")

    def __init__(self, name: str, source_dir: Path):
        self.name = name
        self.source_dir = source_dir

    def keychain_account(self) -> str:
        """Keychain account string: '<dir_path>:<SECRET_NAME>'."""
        return f"{self.source_dir}:{self.name}"

    def __repr__(self) -> str:
        return f"ResolvedSecret({self.name!r}, source={self.source_dir})"


class ResolvedSettings:
    """Merged settings from the full directory hierarchy."""

    def __init__(
        self,
        person: str | None,
        project: str | None,
        secrets: list[ResolvedSecret],
        sources: list[Path],
    ):
        self.person = person
        self.project = project
        self.secrets = secrets
        self.sources = sources  # settings files found, closest first

    def template_vars(self) -> dict[str, str]:
        """Return template variables for pod alias resolution."""
        vars: dict[str, str] = {}
        if self.person:
            vars["person"] = self.person
        if self.project:
            vars["project"] = self.project
        return vars

    def secret_names(self) -> list[str]:
        """Return deduplicated secret names in resolution order."""
        return [s.name for s in self.secrets]


def _walk_to_root(start: Path) -> list[Path]:
    """Yield directories from start up to filesystem root."""
    dirs = []
    current = start.resolve()
    while True:
        dirs.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return dirs


def _load_settings_file(path: Path) -> RpSettings | None:
    """Load a .rp_settings.json file, returning None if it doesn't exist or is invalid."""
    settings_file = path / SETTINGS_FILENAME
    if not settings_file.is_file():
        return None
    try:
        data = json.loads(settings_file.read_text())
        return RpSettings.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def resolve_settings(start: Path | None = None) -> ResolvedSettings:
    """Resolve settings by walking from start directory to filesystem root.

    Closer .rp_settings.json files win for scalar values (person, project).
    For secrets, closer entries override same-named entries from further up.
    """
    if start is None:
        start = Path.cwd()
    start = Path(start).resolve()

    person: str | None = None
    project: str | None = None
    # Track secrets: name → ResolvedSecret (first/closest wins)
    seen_secrets: dict[str, ResolvedSecret] = {}
    sources: list[Path] = []

    for directory in _walk_to_root(start):
        settings = _load_settings_file(directory)
        if settings is None:
            continue

        settings_path = directory / SETTINGS_FILENAME
        sources.append(settings_path)

        # Closest wins for scalars
        if person is None and settings.person is not None:
            person = settings.person
        if project is None and settings.project is not None:
            project = settings.project

        # Closest wins for same-named secrets
        for secret_name in settings.secrets:
            if secret_name not in seen_secrets:
                seen_secrets[secret_name] = ResolvedSecret(secret_name, directory)

    # Preserve order: closest-first
    secrets = list(seen_secrets.values())

    return ResolvedSettings(
        person=person,
        project=project,
        secrets=secrets,
        sources=sources,
    )


def find_nearest_settings_file(start: Path | None = None) -> Path | None:
    """Find the nearest .rp_settings.json file walking up from start."""
    if start is None:
        start = Path.cwd()
    for directory in _walk_to_root(Path(start).resolve()):
        settings_file = directory / SETTINGS_FILENAME
        if settings_file.is_file():
            return settings_file
    return None


def save_settings(directory: Path, settings: RpSettings) -> Path:
    """Write a .rp_settings.json file to the given directory."""
    settings_file = directory / SETTINGS_FILENAME
    settings_file.write_text(
        json.dumps(
            settings.model_dump(exclude_none=True, exclude_defaults=True), indent=2
        )
        + "\n"
    )
    return settings_file
