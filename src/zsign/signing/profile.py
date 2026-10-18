from __future__ import annotations

import dataclasses

_REQUIRED_FIELDS = ("project", "key_id", "version", "header_size", "slot_size")
_ALLOWED_ALIGN = (1, 2, 4, 8, 16, 32)


class ProfileError(ValueError):
    """Raised when a signing profile is missing fields or has invalid values."""


@dataclasses.dataclass(frozen=True)
class SigningProfile:
    """One reviewed signing profile, matching spec.md's signing-profile.json.

    A client-provided profile is a request, not authorization: callers in
    the API layer must check `project`/`key_id` against server-side policy
    before using a profile from an untrusted source. This class only
    validates shape, not authorization.
    """

    project: str
    key_id: str
    version: str
    header_size: int
    slot_size: int
    align: int = 1
    security_counter: int | None = None
    pad_header: bool = False
    pad: bool = False
    confirm: bool = False

    def __post_init__(self):
        if self.header_size <= 0:
            raise ProfileError(f"header_size must be positive, got {self.header_size}")
        if self.slot_size <= 0:
            raise ProfileError(f"slot_size must be positive, got {self.slot_size}")
        if self.align not in _ALLOWED_ALIGN:
            raise ProfileError(f"align must be one of {_ALLOWED_ALIGN}, got {self.align}")
        if self.security_counter is not None and self.security_counter < 0:
            raise ProfileError(
                f"security_counter must be non-negative, got {self.security_counter}"
            )

    @staticmethod
    def from_dict(data: dict) -> "SigningProfile":
        missing = [field for field in _REQUIRED_FIELDS if field not in data]
        if missing:
            raise ProfileError(f"signing profile missing required fields: {missing}")
        return SigningProfile(
            project=data["project"],
            key_id=data["key_id"],
            version=data["version"],
            header_size=int(data["header_size"]),
            slot_size=int(data["slot_size"]),
            align=int(data.get("align", 1)),
            security_counter=(
                None if data.get("security_counter") is None else int(data["security_counter"])
            ),
            pad_header=bool(data.get("pad_header", False)),
            pad=bool(data.get("pad", False)),
            confirm=bool(data.get("confirm", False)),
        )

    def imgtool_common_args(self) -> list[str]:
        """Args shared between the vector-export and fix-sig imgtool
        invocations. Both passes must receive identical values for every
        flag that affects the image header/protected-TLV bytes, or the
        digest signed in pass one won't match what pass two re-derives."""
        args = [
            "-v", self.version,
            "-H", str(self.header_size),
            "-S", str(self.slot_size),
            "--align", str(self.align),
        ]
        if self.security_counter is not None:
            args += ["-s", str(self.security_counter)]
        if self.pad_header:
            args.append("--pad-header")
        if self.pad:
            args.append("--pad")
        if self.confirm:
            args.append("--confirm")
        return args
