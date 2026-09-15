"""Run prompts through the locally installed Claude Code CLI.

No Anthropic API key is used. This shells out to the `claude` CLI in one-shot
print mode (`claude -p`), which relies on the machine's existing Claude login -
the same approach the sibling `db` dashboard uses for its "Ask Claude" tab.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ClaudeCliError(RuntimeError):
    """Raised when the Claude Code CLI is missing or fails to produce output."""


def find_claude_cli() -> str | None:
    """Locate the Claude Code CLI executable.

    Prefers the .cmd shim npm installs over a bare 'claude' - some shells
    resolve a bare name to a .ps1 shim that misbehaves under subprocess.
    PATH is re-read on each call so a CLI installed after startup is found.
    """
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "npm" / "claude.cmd",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("claude.cmd") or shutil.which("claude")
    if found:
        return found

    fallbacks = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude",
    ]
    for candidate in fallbacks:
        if candidate.is_file():
            return str(candidate)
    return None


def run_claude(prompt: str, *, timeout: int = 240, cwd: str | None = None) -> str:
    """Invoke `claude -p`, feeding the prompt on stdin. Returns the response text.

    Raises ClaudeCliError with a user-facing message on any failure.
    """
    claude_bin = find_claude_cli()
    if not claude_bin:
        raise ClaudeCliError(
            "Claude Code CLI ('claude') was not found on PATH. Install it, or if you "
            "just installed it, restart start_dashboard.bat so the new PATH is picked up."
        )
    try:
        # The prompt (a full diff) can be tens of KB; passing it on stdin avoids
        # the ~8191-char Windows command-line limit that a prompt argument hits.
        proc = subprocess.run(
            [claude_bin, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(
            f"Claude Code took longer than {timeout}s to respond. Try a smaller diff."
        ) from exc
    except OSError as exc:
        raise ClaudeCliError(f"Could not run Claude Code ({claude_bin}): {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Claude Code exited with an error.").strip()
        raise ClaudeCliError(detail[:600])
    output = (proc.stdout or "").strip()
    if not output:
        raise ClaudeCliError("Claude Code returned an empty response.")
    return output
