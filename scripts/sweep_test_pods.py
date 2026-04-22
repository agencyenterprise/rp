"""Terminate any leaked e2e test pods.

Used as an `always()` safety net in CI and as a local rescue tool after a
failed/cancelled test run. Any pod whose name starts with `TEST_PREFIX`
is destroyed.

Run locally:   uv run python scripts/sweep_test_pods.py
"""

from __future__ import annotations

import os
import sys

import runpod

TEST_PREFIX = "test-e2e-"


def resolve_api_key() -> str:
    if key := os.environ.get("RUNPOD_API_KEY"):
        return key
    try:
        from rp.core.secret_manager import SecretManager

        if key := SecretManager().get("RUNPOD_API_KEY"):
            return key
    except Exception:
        pass
    print("RUNPOD_API_KEY not found in env or Keychain", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    runpod.api_key = resolve_api_key()

    pods = runpod.get_pods() or []
    if isinstance(pods, dict) and "pods" in pods:
        pods = pods["pods"]

    leaked = [p for p in pods if str(p.get("name", "")).startswith(TEST_PREFIX)]

    if not leaked:
        print(f"No leaked pods matching '{TEST_PREFIX}*'.")
        return 0

    print(f"Found {len(leaked)} leaked test pod(s). Terminating…")
    errors = 0
    for pod in leaked:
        pid = pod.get("id")
        name = pod.get("name")
        try:
            runpod.terminate_pod(pid)
            print(f"  terminated {pid} ({name})")
        except Exception as e:
            errors += 1
            print(f"  FAILED to terminate {pid} ({name}): {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
