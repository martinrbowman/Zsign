from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

from zsign.vault.errors import VaultIntegrityError

_RESTRICTIVE_MODE = 0o600
_PERMISSIVE_BITS = stat.S_IRWXG | stat.S_IRWXO


def atomic_write(path: Path, data: bytes) -> None:
    """Write data to path atomically: temp file in the same directory,
    fsync, rename, best-effort directory fsync. A failure here must never
    leave a half-written vault file in place of a good one."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, _RESTRICTIVE_MODE)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_dir_best_effort(parent)


def _fsync_dir_best_effort(directory: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def read_file(path: Path) -> bytes:
    check_permissions(path)
    return path.read_bytes()


def check_permissions(path: Path) -> None:
    """Defense-in-depth: refuse a vault file readable/writable by group or
    other on POSIX. A loosened-permission vault file indicates a
    misconfigured shared directory, not something to silently trust."""
    if sys.platform == "win32":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & _PERMISSIVE_BITS:
        raise VaultIntegrityError(
            f"Vault file {path} has permissive mode {oct(mode)}; expected "
            f"owner-only access. Refusing to read it."
        )
