# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`rp` is a CLI wrapper around the RunPod Python API for managing GPU pods. It provides two tiers of pod management: low-level (`rp pod create/start/stop/destroy`) and opinionated (top-level `rp up` with full setup, secret injection, auto-shutdown). Also includes remote Claude session management, macOS Keychain secret storage, alias system, template-based deployment, and SSH config management.

**Key documentation**: `docs.md` contains comprehensive technical documentation including all commands, configuration files, and internal behavior. Read this first for complete context.

**IMPORTANT**: When making implementation changes, always update `docs.md` and `README.md` to reflect the changes. These are the primary user-facing documentation and must stay in sync with the code.

## Development Commands

### Environment Setup

```bash
# Sync dependencies
uv sync

# Install in development mode
uv pip install -e .
```

### Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/test_cli_utils.py

# Run specific test
uv run pytest tests/unit/test_cli_utils.py::test_parse_gpu_spec

# Run with coverage
uv run pytest --cov=rp --cov-report=html

# E2E tests (requires RUNPOD_API_KEY)
uv run pytest tests/e2e/
```

### Linting & Formatting

```bash
# Check code style
ruff check

# Auto-fix issues
ruff check --fix

# Format code
ruff format
```

### Running the Tool Locally

```bash
# Run from source
uv run rp --help

# After installation
rp --help
```

## Architecture

The codebase follows a layered architecture with clear separation of concerns:

### Layer Overview

1. **CLI Layer** (`src/rp/cli/`)
   - `main.py`: Typer-based CLI entry point, command routing
   - `commands.py`: Command implementations that orchestrate service layer
   - `utils.py`: CLI utilities (error handling, parsing, display)

2. **Service Layer** (`src/rp/core/`)
   - `pod_manager.py`: Pod CRUD operations, template management
   - `pod_setup.py`: Opinionated pod setup (tools, secrets, auto-shutdown, non-root user)
   - `secret_manager.py`: macOS Keychain secret management (path-scoped keys)
   - `settings.py`: Hierarchical `.rp_settings.json` resolution (walks cwd→root)
   - `claude_remote.py`: Remote Claude session management (tmux, OAuth, logs)
   - `ssh_manager.py`: SSH config file manipulation (marker-based block management)

3. **Data Layer** (`src/rp/core/models.py`)
   - Pydantic models for type safety and validation
   - `AppConfig`: Application state with dual alias format (legacy dict + new PodMetadata)
   - `Pod`, `PodTemplate`, `PodMetadata`, `SSHConfig`, etc.

4. **API Layer** (`src/rp/utils/api_client.py`)
   - `RunPodAPIClient`: Wrapper around runpod SDK with error handling
   - GPU type resolution: queries available GPUs, matches by substring, prefers highest VRAM

### Configuration Storage

**`.rp_settings.json`** (hierarchical): Settings files at any directory level define `person`, `project`, and `secrets`. Resolution walks cwd→root; closest wins. Keychain keys encode `<dir_path>:<SECRET_NAME>` for scoping.

**`~/.config/rp/`** (central):
- `pods.json`: Pod metadata (including `managed` flag), templates
- `setup.sh`: Script run on non-managed pods during startup (optional, default provided)

### Key Design Patterns

**Pod Metadata**: All aliases stored as `PodMetadata` in `pod_metadata` dict. Includes a `managed` flag to distinguish pods created with `rp up` from bare pods.

**SSH Block Management**: `SSHManager` uses marker comments (`# rp:managed alias=... pod_id=...`) to identify managed blocks in `~/.ssh/config`, allowing safe updates without touching user configs.

**GPU Resolution**: Two-stage process:
1. Parse string (`[count]xmodel`) into `GPUSpec`
2. Resolve model substring to RunPod GPU type ID via API, preferring highest VRAM

**Auto-Shutdown**: Managed pods (`rp up`) get a cron job that checks GPU utilization every 5 minutes. After 120 min of all GPUs at 0%, the pod self-destructs via RunPod REST API.

**Template Auto-numbering**: `find_next_alias_index()` finds lowest `i ≥ 1` where `template.format(i=i)` doesn't exist in aliases.

## Code Patterns

### Error Handling

Use custom error classes from `utils/errors.py`:
- `RunPodCLIError` base class with `message`, `details`, `exit_code`
- Specific errors: `AliasError`, `PodError`, `APIError`, `SSHError`, `SetupScriptError`
- CLI commands catch all exceptions and call `handle_cli_error()` for consistent output

### Service Instantiation

Services use lazy singleton pattern via module-level functions in `cli/commands.py`:
```python
def get_pod_manager() -> PodManager:
    global _pod_manager
    if _pod_manager is None:
        api_client = setup_api_client()
        _pod_manager = PodManager(api_client)
    return _pod_manager
```

### Pydantic Models

All data classes use Pydantic for validation:
- Type annotations with `Field()` for validation rules
- Factory methods: `Pod.from_runpod_response()`, `Pod.from_alias_and_id()`
- Validators: `@field_validator` for custom validation logic

### Configuration Persistence

Both `PodManager` and `Scheduler` follow pattern:
1. Load config on first property access (`@property config`)
2. Mutating operations use `_locked_config()` context manager: acquire exclusive file lock (`pods.lock`), re-read from disk, yield config for mutation, write back on exit. This prevents concurrent `rp` processes from clobbering each other.
3. Use `model_dump_json()` for serialization

## Testing Notes

### Test Structure

- `tests/unit/`: Unit tests for utilities, parsers, models
- `tests/e2e/`: End-to-end tests requiring real RunPod API (uses fixtures to create/destroy pods)
- `tests/conftest.py`: Shared fixtures including CLI runner with environment setup

### E2E Test Patterns

E2E tests use a shared pod fixture (`shared_test_pod`) to avoid creating pods for every test. Tests track aliases temporarily and clean up after themselves:

```python
def test_something(cli_runner, shared_test_pod):
    alias = "test-alias"
    pod_id = shared_test_pod["pod_id"]

    # Track alias
    result = cli_runner(["track", alias, pod_id])

    # ... test logic ...

    # Clean up
    result = cli_runner(["untrack", alias])
```

## Versioning

Every change must include a version bump in `pyproject.toml`. Use **minor** version bumps for new features or breaking changes, and **patch** bumps for bug fixes and small improvements. Commit the version bump together with (or as part of) the change — don't leave it for a separate follow-up.

## Important Constraints

- **Python 3.13+** required (uses modern type syntax: `dict[str, str]`, `str | None`)
- **macOS** for Keychain-based secret management (uses `security` CLI)
- **SSH config**: Assumes `~/.ssh/config` exists and is writable
- **API Key**: Priority: env var `RUNPOD_API_KEY` → Keychain → interactive prompt (saves to Keychain)

## Common Gotchas

1. **GPU Parsing**: `x` in model name is allowed (e.g., `rtx4090`). Only treated as count separator if prefix is numeric.

2. **SSH Config Markers**: Never remove or modify marker comments manually. `SSHManager.remove_host_config()` relies on them to find blocks to remove.

3. **Template Placeholders**: Only `{i}` placeholder is supported. Validation ensures it exists in `alias_template`.

4. **Managed vs Bare Pods**: `rp up` creates managed pods (with `managed: true` in PodMetadata). `rp start` checks this flag and re-injects secrets + redeploys auto-shutdown on managed pods. Bare pods (`rp create`) use the setup script.

5. **SecretManager naming**: The `SecretManager` class has a `set()` method which shadows the Python builtin `set` type. Internal methods use `builtins.set[str]` for type annotations.
