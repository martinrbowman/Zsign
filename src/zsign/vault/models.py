from __future__ import annotations

import dataclasses
import enum


class Algorithm(enum.Enum):
    ECDSA_P256 = "ecdsa-p256"
    ED25519 = "ed25519"
    RSA_2048 = "rsa-2048"
    RSA_3072 = "rsa-3072"


class LifecycleState(enum.Enum):
    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


@dataclasses.dataclass(frozen=True)
class KeyRecord:
    """Public metadata for one vault key. Never carries private material."""

    key_id: str
    label: str
    algorithm: Algorithm
    project: str
    public_fingerprint: str
    created_at: str
    lifecycle_state: LifecycleState
    allowed_uses: tuple[str, ...]
    public_key_pem: bytes

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "algorithm": self.algorithm.value,
            "project": self.project,
            "public_fingerprint": self.public_fingerprint,
            "created_at": self.created_at,
            "lifecycle_state": self.lifecycle_state.value,
            "allowed_uses": list(self.allowed_uses),
            "public_key_pem": self.public_key_pem.decode("ascii"),
        }

    @staticmethod
    def from_dict(data: dict) -> "KeyRecord":
        return KeyRecord(
            key_id=data["key_id"],
            label=data["label"],
            algorithm=Algorithm(data["algorithm"]),
            project=data["project"],
            public_fingerprint=data["public_fingerprint"],
            created_at=data["created_at"],
            lifecycle_state=LifecycleState(data["lifecycle_state"]),
            allowed_uses=tuple(data["allowed_uses"]),
            public_key_pem=data["public_key_pem"].encode("ascii"),
        )

    def with_lifecycle_state(self, state: LifecycleState) -> "KeyRecord":
        return dataclasses.replace(self, lifecycle_state=state)
