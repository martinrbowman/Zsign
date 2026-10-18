import pytest

from zsign.vault import keyring_backend
from zsign.vault.errors import FailClosedError, KekNotFoundError


def test_disallowed_backend_fails_closed_without_touching_keyring(monkeypatch):
    monkeypatch.setattr(
        keyring_backend, "active_backend_name", lambda: "keyring.backends.fail.Keyring"
    )

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("should fail closed before touching the real backend")

    monkeypatch.setattr(keyring_backend.keyring, "set_password", must_not_be_called)
    with pytest.raises(FailClosedError):
        keyring_backend.ensure_backend_available()


def test_allowed_backend_but_locked_session_fails_closed(monkeypatch):
    monkeypatch.setattr(
        keyring_backend, "active_backend_name", lambda: "keyring.backends.macOS.Keyring"
    )

    def locked(*args, **kwargs):
        raise RuntimeError("session is locked")

    monkeypatch.setattr(keyring_backend.keyring, "set_password", locked)
    with pytest.raises(FailClosedError):
        keyring_backend.ensure_backend_available()


def test_allowed_backend_store_and_load_roundtrip(monkeypatch):
    monkeypatch.setattr(
        keyring_backend, "active_backend_name", lambda: "keyring.backends.macOS.Keyring"
    )
    fake_store: dict[tuple[str, str], str] = {}

    def fake_set_password(service, account, value):
        fake_store[(service, account)] = value

    def fake_get_password(service, account):
        return fake_store.get((service, account))

    def fake_delete_password(service, account):
        fake_store.pop((service, account), None)

    monkeypatch.setattr(keyring_backend.keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring_backend.keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring_backend.keyring, "delete_password", fake_delete_password)

    kek = b"\x01" * 32
    keyring_backend.store_kek("account-1", kek)
    assert keyring_backend.load_kek("account-1") == kek

    keyring_backend.delete_kek("account-1")
    with pytest.raises(KekNotFoundError):
        keyring_backend.load_kek("account-1")


def test_missing_kek_raises_not_found(monkeypatch):
    monkeypatch.setattr(
        keyring_backend, "active_backend_name", lambda: "keyring.backends.macOS.Keyring"
    )
    monkeypatch.setattr(keyring_backend.keyring, "set_password", lambda *a, **k: None)
    monkeypatch.setattr(keyring_backend.keyring, "delete_password", lambda *a, **k: None)
    monkeypatch.setattr(keyring_backend.keyring, "get_password", lambda *a, **k: None)
    with pytest.raises(KekNotFoundError):
        keyring_backend.load_kek("nonexistent-account")
