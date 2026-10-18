from __future__ import annotations

import dataclasses
from typing import Callable


@dataclasses.dataclass(frozen=True)
class ApprovalRequest:
    """Everything an approver needs to see before a sign request proceeds.

    Per spec.md's GUI/Audit Requirements: approval must show the
    authenticated client, target project, key fingerprint, image digest,
    version, and relevant signing parameters.
    """

    request_id: str
    client_label: str
    project: str
    key_id: str
    key_fingerprint: str
    version: str
    image_sha256: str
    image_size: int
    profile: dict


ApprovalHandler = Callable[[ApprovalRequest], bool]


def deny_all(_request: ApprovalRequest) -> bool:
    """Safe default when no real approval handler is wired up: nothing
    signs until an operator (GUI) or test double explicitly approves."""
    return False
