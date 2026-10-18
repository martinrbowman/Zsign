import json
import os
import shutil

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from zsign.api import ApprovalRequest, KeyLockRegistry, TokenStore, create_app  # noqa: E402
from zsign.gui.controller import VaultController  # noqa: E402

PASSPHRASE = "correct horse battery staple"

pytestmark = pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")


def _build(tmp_path, approval_handler=None, lock_registry=None):
    controller = VaultController()
    controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    record = controller.generate_key("release", "product-a")
    token_store = TokenStore()
    sign_token = token_store.issue("sign", project="product-a")
    admin_token = token_store.issue("admin")
    handler = approval_handler or (lambda req: True)
    app = create_app(
        controller, token_store, lock_registry or KeyLockRegistry(), approval_handler=handler,
    )
    client = TestClient(app)
    return client, controller, record, sign_token, admin_token, token_store


def _profile(record, **overrides):
    data = {
        "project": "product-a",
        "key_id": record.key_id,
        "version": "1.0.0+0",
        "header_size": 32,
        "slot_size": 65536,
        "align": 1,
        "pad_header": True,
    }
    data.update(overrides)
    return data


def test_health_requires_no_auth(tmp_path):
    client, *_ = _build(tmp_path)
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sign_requires_bearer_token(tmp_path):
    client, controller, record, *_ = _build(tmp_path)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
    )
    assert response.status_code == 401


def test_sign_success_end_to_end(tmp_path):
    client, controller, record, sign_token, _admin, _store = _build(tmp_path)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    assert "X-Signed-SHA256" in response.headers
    assert "X-Audit-ID" in response.headers
    assert len(response.content) > 0


def test_sign_denied_by_approval_handler(tmp_path):
    client, controller, record, sign_token, *_ = _build(tmp_path, approval_handler=lambda req: False)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "denied"


def test_approval_handler_receives_expected_fields(tmp_path):
    captured = []
    client, controller, record, sign_token, *_ = _build(
        tmp_path, approval_handler=lambda req: (captured.append(req), True)[1]
    )
    client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", b"x" * 100)},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert len(captured) == 1
    request: ApprovalRequest = captured[0]
    assert request.project == "product-a"
    assert request.key_id == record.key_id
    assert request.key_fingerprint == record.public_fingerprint
    assert request.image_size == 100


def test_sign_rejects_project_token_mismatch(tmp_path):
    client, controller, record, _sign_token, _admin, store = _build(tmp_path)
    other_project_token = store.issue("sign", project="other-project")
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {other_project_token}"},
    )
    assert response.status_code == 403


def test_sign_rejects_admin_token(tmp_path):
    client, controller, record, _sign_token, admin_token, _store = _build(tmp_path)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403


def test_sign_rejects_revoked_key(tmp_path):
    client, controller, record, sign_token, *_ = _build(tmp_path)
    controller.revoke_key(record.key_id)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "policy_violation"


def test_sign_rejects_oversized_upload(tmp_path):
    from zsign.api import server as server_module

    client, controller, record, sign_token, *_ = _build(tmp_path)
    original_limit = server_module.MAX_UPLOAD_BYTES
    server_module.MAX_UPLOAD_BYTES = 1024
    try:
        response = client.post(
            "/v1/sign",
            data={"metadata": json.dumps(_profile(record))},
            files={"image": ("app.bin", os.urandom(2048))},
            headers={"Authorization": f"Bearer {sign_token}"},
        )
        assert response.status_code == 413
    finally:
        server_module.MAX_UPLOAD_BYTES = original_limit


def test_verify_round_trip(tmp_path):
    client, controller, record, sign_token, *_ = _build(tmp_path)
    sign_response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record, version="2.0.0+1", security_counter=5))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert sign_response.status_code == 200

    verify_response = client.post(
        "/v1/verify",
        data={"metadata": json.dumps({
            "project": "product-a", "key_id": record.key_id,
            "version": "2.0.0+1", "security_counter": 5,
        })},
        files={"image": ("app.signed.bin", sign_response.content)},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert verify_response.status_code == 200
    body = verify_response.json()
    assert body["valid"] is True
    assert body["version"] == "2.0.0+1"
    assert body["security_counter"] == 5


def test_verify_detects_tamper(tmp_path):
    client, controller, record, sign_token, *_ = _build(tmp_path)
    sign_response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    tampered = bytearray(sign_response.content)
    tampered[40] ^= 0xFF

    verify_response = client.post(
        "/v1/verify",
        data={"metadata": json.dumps({"project": "product-a", "key_id": record.key_id})},
        files={"image": ("app.bin", bytes(tampered))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["valid"] is False


def test_list_keys_filters_by_project(tmp_path):
    client, controller, record, sign_token, admin_token, store = _build(tmp_path)
    controller.generate_key("other", "other-project")

    response = client.get("/v1/keys", headers={"Authorization": f"Bearer {sign_token}"})
    assert response.status_code == 200
    projects = {k["project"] for k in response.json()}
    assert projects == {"product-a"}

    admin_response = client.get("/v1/keys", headers={"Authorization": f"Bearer {admin_token}"})
    admin_projects = {k["project"] for k in admin_response.json()}
    assert admin_projects == {"product-a", "other-project"}


def test_get_public_pem(tmp_path):
    client, controller, record, sign_token, *_ = _build(tmp_path)
    response = client.get(
        f"/v1/keys/{record.key_id}/public.pem",
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert response.status_code == 200
    assert response.content == record.public_key_pem


def test_lock_and_unlock_require_admin_token(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)

    denied = client.post("/v1/lock", headers={"Authorization": f"Bearer {sign_token}"})
    assert denied.status_code == 403

    locked = client.post("/v1/lock", headers={"Authorization": f"Bearer {admin_token}"})
    assert locked.status_code == 200
    assert locked.json() == {"locked": True}
    assert not controller.status.is_unlocked

    unlocked = client.post(
        "/v1/unlock",
        json={"passphrase": PASSPHRASE},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unlocked.status_code == 200
    assert unlocked.json() == {"locked": False}
    assert controller.status.is_unlocked


def test_unlock_wrong_passphrase(tmp_path):
    client, controller, record, _sign_token, admin_token, _store = _build(tmp_path)
    client.post("/v1/lock", headers={"Authorization": f"Bearer {admin_token}"})
    response = client.post(
        "/v1/unlock",
        json={"passphrase": "wrong"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 401


def test_concurrent_sign_requests_for_same_key_are_serialized(tmp_path):
    """Two concurrent /v1/sign requests for the same key must not
    interleave: the key lock registry should force them to run one at a
    time. We verify this by having the approval handler record enter/exit
    order and asserting no overlap."""
    import threading
    import time

    events = []
    lock = threading.Lock()

    def handler(_req):
        with lock:
            events.append(("start", threading.current_thread().name))
        time.sleep(0.05)
        with lock:
            events.append(("end", threading.current_thread().name))
        return True

    client, controller, record, sign_token, *_ = _build(tmp_path, approval_handler=handler)

    def do_sign():
        client.post(
            "/v1/sign",
            data={"metadata": json.dumps(_profile(record))},
            files={"image": ("app.bin", os.urandom(512))},
            headers={"Authorization": f"Bearer {sign_token}"},
        )

    threads = [threading.Thread(target=do_sign) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Serialized means each "start" is immediately followed by its own
    # "end" before the next "start" appears.
    assert len(events) == 6
    for i in range(0, 6, 2):
        assert events[i][0] == "start"
        assert events[i + 1] == ("end", events[i][1])


def test_audit_endpoint_requires_admin_token(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    denied = client.get("/v1/audit", headers={"Authorization": f"Bearer {sign_token}"})
    assert denied.status_code == 403

    allowed = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_token}"})
    assert allowed.status_code == 200
    assert "records" in allowed.json()


def test_successful_sign_audit_id_matches_header(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    audit_id = response.headers["X-Audit-ID"]

    audit_response = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_token}"})
    records = audit_response.json()["records"]
    matching = [r for r in records if r["record_id"] == audit_id]
    assert len(matching) == 1
    assert matching[0]["event_type"] == "sign.completed"
    assert matching[0]["key_id"] == record.key_id


def test_denied_sign_audit_id_matches_error_body(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(
        tmp_path, approval_handler=lambda req: False,
    )
    response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    audit_id = response.json()["audit_id"]

    audit_response = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_token}"})
    records = audit_response.json()["records"]
    matching = [r for r in records if r["record_id"] == audit_id]
    assert len(matching) == 1
    assert matching[0]["event_type"] == "sign.denied"


def test_auth_failure_is_audited(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    client.get("/v1/keys", headers={"Authorization": "Bearer garbage"})
    audit_response = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_token}"})
    event_types = [r["event_type"] for r in audit_response.json()["records"]]
    assert "auth.failure" in event_types


def test_sign_rollback_rejected_over_api(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    first = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record, security_counter=5))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record, security_counter=3))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    assert second.status_code == 400
    assert "security_counter" in second.json()["message"]


def test_verify_outcome_is_audited(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    sign_response = client.post(
        "/v1/sign",
        data={"metadata": json.dumps(_profile(record))},
        files={"image": ("app.bin", os.urandom(512))},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    client.post(
        "/v1/verify",
        data={"metadata": json.dumps({"project": "product-a", "key_id": record.key_id})},
        files={"image": ("app.signed.bin", sign_response.content)},
        headers={"Authorization": f"Bearer {sign_token}"},
    )
    audit_response = client.get("/v1/audit", headers={"Authorization": f"Bearer {admin_token}"})
    event_types = [r["event_type"] for r in audit_response.json()["records"]]
    assert "verify.completed" in event_types


def test_audit_query_filters_by_key_id(tmp_path):
    client, controller, record, sign_token, admin_token, _store = _build(tmp_path)
    other_record = controller.generate_key("other", "other-project")
    response = client.get(
        f"/v1/audit?key_id={other_record.key_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    records = response.json()["records"]
    assert all(r["key_id"] == other_record.key_id for r in records)
    assert any(r["event_type"] == "key.generate" for r in records)
