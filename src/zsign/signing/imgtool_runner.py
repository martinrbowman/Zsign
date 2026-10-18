from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class ImgtoolError(RuntimeError):
    """Raised when the pinned imgtool subprocess exits non-zero."""


def _command(executable: str | Path) -> list[str]:
    """A console-script name (e.g. "imgtool") is run directly; a path to
    an imgtool.py (e.g. a pinned MCUboot tree's scripts/imgtool.py) is
    run via the current interpreter, since relying on its shebang + exec
    bit is POSIX-only and not guaranteed even there."""
    executable = str(executable)
    if executable.endswith(".py"):
        return [sys.executable, executable]
    return [executable]


def run(executable: str | Path, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [*_command(executable), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ImgtoolError(
            f"imgtool {args[0] if args else ''} failed (exit {result.returncode}): {detail}"
        )
    return result
