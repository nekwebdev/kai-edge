from __future__ import annotations

import subprocess

from .errors import EdgeRuntimeError


def run_command(command: list[str], action: str) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EdgeRuntimeError(f"{action} failed because {command[0]!r} is not installed") from exc

    if completed.returncode == 0:
        return

    details = completed.stderr.strip() or completed.stdout.strip() or "no command output"
    raise EdgeRuntimeError(f"{action} failed with exit code {completed.returncode}: {details}")
