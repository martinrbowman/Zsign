import os

import pytest

from zsign.vault import storage


def test_atomic_write_then_read_roundtrip(tmp_path):
    target = tmp_path / "vault.json"
    storage.atomic_write(target, b"hello vault")
    assert storage.read_file(target) == b"hello vault"


def test_atomic_write_sets_restrictive_permissions(tmp_path):
    target = tmp_path / "vault.json"
    storage.atomic_write(target, b"data")
    if os.name != "nt":
        mode = target.stat().st_mode & 0o777
        assert mode == 0o600


def test_failed_write_leaves_original_file_untouched(tmp_path, monkeypatch):
    target = tmp_path / "vault.json"
    storage.atomic_write(target, b"original content")

    def boom(*args, **kwargs):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        storage.atomic_write(target, b"new content that should never land")

    assert storage.read_file(target) == b"original content"
    leftover_tmp_files = list(tmp_path.glob(".vault.json.*.tmp"))
    assert leftover_tmp_files == []


def test_read_rejects_permissive_file(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits only")
    target = tmp_path / "vault.json"
    storage.atomic_write(target, b"data")
    target.chmod(0o644)
    with pytest.raises(Exception):
        storage.read_file(target)
