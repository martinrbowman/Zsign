from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse, Response

from zsign.api.approval import ApprovalHandler, ApprovalRequest, deny_all
from zsign.api.auth import Token, TokenError, TokenStore
from zsign.api.key_locks import KeyLockRegistry
from zsign.gui.controller import ControllerError, VaultController
from zsign.signing import (
    ImgtoolError,
    ProfileError,
    SigningProfile,
    dump_image_info,
    embedded_security_counter,
    verify_image,
)
from zsign.vault.models import LifecycleState

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
APPROVAL_TIMEOUT_SECONDS = 120


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str, audit_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.audit_id = audit_id


def create_app(
    controller: VaultController,
    token_store: TokenStore,
    lock_registry: KeyLockRegistry | None = None,
    approval_handler: ApprovalHandler = deny_all,
) -> FastAPI:
    """Build the localhost signing API.

    Binding to 127.0.0.1 only, rejecting redirects, and avoiding broad
    CORS are the caller's (runner.py's) job -- this just builds routes
    and policy. `controller` here is zsign.gui.controller.VaultController,
    which has no PySide6 import despite living under the gui package, so
    importing it does not pull in Qt.
    """
    lock_registry = lock_registry or KeyLockRegistry()
    app = FastAPI(title="Zsign Signing Agent", docs_url=None, redoc_url=None)

    @app.exception_handler(ApiError)
    def _handle_api_error(_request, exc: ApiError) -> JSONResponse:
        body = {"error": exc.error, "message": exc.message}
        if exc.audit_id is not None:
            body["audit_id"] = exc.audit_id
        return JSONResponse(status_code=exc.status_code, content=body)

    def _audit(event_type: str, actor: str, details: dict, **kwargs) -> None:
        if controller.audit_log is not None:
            controller.audit_log.append(event_type, actor, details, **kwargs)

    def _actor(token: Token) -> str:
        return f"{token.scope}-token:{token.token_id}"

    def _require_token(authorization: str | None = Header(default=None)) -> Token:
        if not authorization or not authorization.startswith("Bearer "):
            _audit("auth.failure", "unauthenticated", {"reason": "missing bearer token"})
            raise ApiError(401, "unauthorized", "missing bearer token")
        try:
            return token_store.verify(authorization[len("Bearer "):])
        except TokenError as exc:
            _audit("auth.failure", "unauthenticated", {"reason": str(exc)})
            raise ApiError(401, "unauthorized", str(exc)) from exc

    def _require_admin(token: Token = Depends(_require_token)) -> Token:
        if token.scope != "admin":
            _audit("auth.failure", _actor(token), {"reason": "admin-scoped token required"})
            raise ApiError(403, "unauthorized", "admin-scoped token required")
        return token

    def _require_sign(token: Token = Depends(_require_token)) -> Token:
        if token.scope != "sign":
            _audit("auth.failure", _actor(token), {"reason": "sign-scoped token required"})
            raise ApiError(403, "unauthorized", "sign-scoped token required")
        return token

    def _visible_to(token: Token, project: str) -> bool:
        return token.scope == "admin" or token.project == project

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/keys")
    def list_keys(token: Token = Depends(_require_token)) -> list[dict]:
        try:
            records = controller.list_keys()
        except ControllerError as exc:
            raise ApiError(409, "vault_locked", str(exc)) from exc
        return [r.to_dict() for r in records if _visible_to(token, r.project)]

    @app.get("/v1/keys/{key_id}/public.pem")
    def get_public_pem(key_id: str, token: Token = Depends(_require_token)) -> Response:
        try:
            record = controller.get_key(key_id)
        except ControllerError as exc:
            raise ApiError(404, "unknown_key", str(exc)) from exc
        if not _visible_to(token, record.project):
            raise ApiError(404, "unknown_key", "no such key")
        return Response(content=record.public_key_pem, media_type="application/x-pem-file")

    @app.post("/v1/lock")
    def lock(token: Token = Depends(_require_admin)) -> dict:
        controller.lock(actor=_actor(token))
        return {"locked": True}

    @app.post("/v1/unlock")
    def unlock(body: dict | None = None, token: Token = Depends(_require_admin)) -> dict:
        passphrase = (body or {}).get("passphrase")
        try:
            controller.unlock(passphrase=passphrase, actor=_actor(token))
        except ControllerError as exc:
            raise ApiError(401, "vault_locked", str(exc)) from exc
        return {"locked": False}

    @app.post("/v1/sign")
    def sign(
        metadata: str = Form(...),
        image: UploadFile = File(...),
        token: Token = Depends(_require_sign),
    ) -> Response:
        request_id = str(uuid.uuid4())
        try:
            profile_data = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise ApiError(400, "invalid_profile", f"metadata is not valid JSON: {exc}") from exc
        try:
            profile = SigningProfile.from_dict(profile_data)
        except ProfileError as exc:
            raise ApiError(400, "invalid_profile", str(exc)) from exc

        if profile.project != token.project:
            _audit(
                "policy.violation", _actor(token),
                {"reason": "token project does not match profile project"},
                project=profile.project, key_id=profile.key_id, record_id=request_id,
            )
            raise ApiError(403, "unauthorized", "token project does not match profile project")

        try:
            record = controller.get_key(profile.key_id)
        except ControllerError as exc:
            _audit(
                "policy.violation", _actor(token), {"reason": str(exc)},
                project=profile.project, key_id=profile.key_id, record_id=request_id,
            )
            raise ApiError(404, "unknown_key", str(exc)) from exc
        if record.project != profile.project:
            _audit(
                "policy.violation", _actor(token),
                {"reason": "key does not belong to the requested project"},
                project=profile.project, key_id=profile.key_id, record_id=request_id,
            )
            raise ApiError(403, "policy_violation", "key does not belong to the requested project")

        image_bytes = image.file.read(MAX_UPLOAD_BYTES + 1)
        if len(image_bytes) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "payload_too_large", f"image exceeds {MAX_UPLOAD_BYTES} bytes")

        approval_request = ApprovalRequest(
            request_id=request_id,
            client_label=f"sign-token:{token.token_id}",
            project=profile.project,
            key_id=profile.key_id,
            key_fingerprint=record.public_fingerprint,
            version=profile.version,
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),
            image_size=len(image_bytes),
            profile=profile_data,
        )

        with lock_registry.lock_for(profile.key_id):
            if record.lifecycle_state != LifecycleState.ACTIVE:
                _audit(
                    "policy.violation", _actor(token),
                    {"reason": f"key is not active ({record.lifecycle_state.value})"},
                    project=profile.project, key_id=profile.key_id, record_id=request_id,
                )
                raise ApiError(
                    403, "policy_violation", f"key is not active ({record.lifecycle_state.value})",
                    audit_id=request_id,
                )
            if not approval_handler(approval_request):
                _audit(
                    "sign.denied", _actor(token), {"reason": "denied or expired"},
                    project=profile.project, key_id=profile.key_id, record_id=request_id,
                )
                raise ApiError(
                    403, "denied", "signing request denied or expired", audit_id=request_id,
                )

            with tempfile.TemporaryDirectory(prefix="zsign-api-sign-") as tmp_dir:
                input_path = Path(tmp_dir) / "unsigned.bin"
                output_path = Path(tmp_dir) / "signed.bin"
                input_path.write_bytes(image_bytes)
                try:
                    result = controller.sign_image(
                        key_id=profile.key_id,
                        project=profile.project,
                        version=profile.version,
                        header_size=profile.header_size,
                        slot_size=profile.slot_size,
                        align=profile.align,
                        security_counter=profile.security_counter,
                        pad_header=profile.pad_header,
                        pad=profile.pad,
                        confirm=profile.confirm,
                        input_path=input_path,
                        output_path=output_path,
                        actor=_actor(token),
                        audit_id=request_id,
                    )
                except ControllerError as exc:
                    raise ApiError(400, "signing_failed", str(exc), audit_id=request_id) from exc
                signed_bytes = output_path.read_bytes()

        return Response(
            content=signed_bytes,
            media_type="application/octet-stream",
            headers={
                "X-Input-SHA256": result.input_sha256,
                "X-Signed-SHA256": result.signed_sha256,
                "X-Audit-ID": result.audit_id,
            },
        )

    @app.post("/v1/verify")
    def verify(
        metadata: str = Form(...),
        image: UploadFile = File(...),
        token: Token = Depends(_require_sign),
    ) -> dict:
        try:
            profile_data = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise ApiError(400, "invalid_profile", f"metadata is not valid JSON: {exc}") from exc

        key_id = profile_data.get("key_id")
        if not key_id:
            raise ApiError(400, "invalid_profile", "metadata missing key_id")
        try:
            record = controller.get_key(key_id)
        except ControllerError as exc:
            raise ApiError(404, "unknown_key", str(exc)) from exc

        image_bytes = image.file.read(MAX_UPLOAD_BYTES + 1)
        if len(image_bytes) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "payload_too_large", f"image exceeds {MAX_UPLOAD_BYTES} bytes")

        with tempfile.TemporaryDirectory(prefix="zsign-api-verify-") as tmp_dir:
            image_path = Path(tmp_dir) / "image.bin"
            pubkey_path = Path(tmp_dir) / "public.pem"
            image_path.write_bytes(image_bytes)
            pubkey_path.write_bytes(record.public_key_pem)

            imgtool_executable = controller.imgtool_executable or "imgtool"
            try:
                verify_image(pubkey_path, image_path, imgtool_executable)
            except ImgtoolError as exc:
                _audit(
                    "verify.completed", _actor(token), {"valid": False, "reason": str(exc)},
                    project=record.project, key_id=key_id,
                )
                return {"valid": False, "reason": str(exc)}

            dump = dump_image_info(image_path, imgtool_executable)

        actual_version = dump["header"]["version"]
        actual_counter = embedded_security_counter(dump)

        expected_version = profile_data.get("version")
        if expected_version is not None and expected_version != actual_version:
            reason = f"version mismatch: expected {expected_version}, got {actual_version}"
            _audit(
                "verify.completed", _actor(token), {"valid": False, "reason": reason},
                project=record.project, key_id=key_id,
            )
            return {"valid": False, "reason": reason}
        expected_counter = profile_data.get("security_counter")
        if expected_counter is not None and int(expected_counter) != actual_counter:
            reason = (
                f"security_counter mismatch: expected {expected_counter}, got {actual_counter}"
            )
            _audit(
                "verify.completed", _actor(token), {"valid": False, "reason": reason},
                project=record.project, key_id=key_id,
            )
            return {"valid": False, "reason": reason}

        _audit(
            "verify.completed", _actor(token),
            {"valid": True, "version": actual_version, "security_counter": actual_counter},
            project=record.project, key_id=key_id,
        )
        return {
            "valid": True,
            "key_id": key_id,
            "version": actual_version,
            "security_counter": actual_counter,
        }

    @app.get("/v1/audit")
    def audit(
        since: str | None = None,
        until: str | None = None,
        key_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        _token: Token = Depends(_require_admin),
    ) -> dict:
        if controller.audit_log is None:
            return {"records": [], "next_cursor": None}
        records, next_cursor = controller.audit_log.query(
            since=since, until=until, key_id=key_id, limit=limit, cursor=cursor,
        )
        return {"records": [r.to_dict() for r in records], "next_cursor": next_cursor}

    return app
