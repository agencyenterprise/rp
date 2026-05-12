"""Resolve the current Claude Code / rp session ID for pod scoping."""

import os


def current_session_id() -> str | None:
    """Return the active session ID, or None if no session is active.

    Precedence:
      1. RP_SESSION_ID (explicit user override; empty string = unset)
      2. CLAUDE_CODE_SESSION_ID (auto-set by Claude Code ≥ 2.1.139)
      3. None — bare terminal, scripts, CI, etc.
    """
    explicit = os.environ.get("RP_SESSION_ID")
    if explicit:
        return explicit
    claude = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if claude:
        return claude
    return None
