class VaultError(Exception):
    """Base class for all vault errors."""


class FailClosedError(VaultError):
    """Raised when a required secure backend (OS keyring) is unavailable.

    The vault must never silently fall back to a weaker protection mode.
    """


class VaultLockedError(VaultError):
    """Raised when an operation requires an unlocked vault."""


class VaultIntegrityError(VaultError):
    """Raised when vault ciphertext fails authentication (tampered or
    corrupted file, or wrong passphrase/KEK)."""


class KekNotFoundError(VaultError):
    """Raised when a vault is in keyring mode but its KEK entry is missing
    from the OS keyring (deleted externally, wrong machine/user, etc.)."""
