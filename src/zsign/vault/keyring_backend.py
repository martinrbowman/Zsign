from __future__ import annotations

import base64

import keyring
import keyring.backend

from zsign.vault.errors import FailClosedError, KekNotFoundError

SERVICE_NAME = "zsign-vault"

# Only these backends are treated as a validated native OS credential store.
# Anything else (keyring's "fail" backend, in-memory/null backends, or
# third-party plaintext backends from keyrings.alt) must fail closed rather
# than silently storing the KEK somewhere weaker than the user expects.
ALLOWED_BACKENDS = {
    "keyring.backends.macOS.Keyring",
    "keyring.backends.Windows.WinVaultKeyring",
    "keyring.backends.SecretService.Keyring",
    "keyring.backends.kwallet.DBusKeyring",
}


def _backend_name(backend: keyring.backend.KeyringBackend) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__qualname__}"


def active_backend_name() -> str:
    return _backend_name(keyring.get_keyring())


def ensure_backend_available() -> None:
    """Fail closed unless an allowlisted native backend is active and
    usable. Raises FailClosedError otherwise -- never falls back to a
    weaker backend silently."""
    name = active_backend_name()
    if name not in ALLOWED_BACKENDS:
        raise FailClosedError(
            f"No validated native OS credential backend available "
            f"(active backend: {name}). Refusing to store the key-"
            f"encryption key insecurely; use passphrase protection instead."
        )
    try:
        # Probe with a throwaway round trip; some backends (e.g. a locked
        # Secret Service session) report as active but fail on use.
        probe_account = "__zsign_probe__"
        keyring.set_password(SERVICE_NAME, probe_account, "probe")
        keyring.delete_password(SERVICE_NAME, probe_account)
    except Exception as exc:  # noqa: BLE001 - any backend failure is fail-closed
        raise FailClosedError(
            f"Native OS credential backend ({name}) is unavailable or "
            f"locked: {exc}"
        ) from exc


def store_kek(account: str, kek: bytes) -> None:
    ensure_backend_available()
    keyring.set_password(SERVICE_NAME, account, base64.b64encode(kek).decode("ascii"))


def load_kek(account: str) -> bytes:
    ensure_backend_available()
    encoded = keyring.get_password(SERVICE_NAME, account)
    if encoded is None:
        raise KekNotFoundError(
            f"No key-encryption key found in OS keyring for account "
            f"{account!r}; vault cannot be unlocked on this machine/user."
        )
    return base64.b64decode(encoded)


def delete_kek(account: str) -> None:
    ensure_backend_available()
    try:
        keyring.delete_password(SERVICE_NAME, account)
    except keyring.errors.PasswordDeleteError:
        pass
