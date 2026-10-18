import time

import pytest

from zsign.api.auth import TokenError, TokenStore


def test_issue_and_verify_roundtrip():
    store = TokenStore()
    token = store.issue("sign", project="product-a")
    verified = store.verify(token)
    assert verified.scope == "sign"
    assert verified.project == "product-a"


def test_admin_token_has_no_project():
    store = TokenStore()
    token = store.issue("admin")
    verified = store.verify(token)
    assert verified.project is None


def test_sign_scope_requires_project():
    store = TokenStore()
    with pytest.raises(ValueError):
        store.issue("sign")


def test_verify_rejects_malformed_token():
    store = TokenStore()
    with pytest.raises(TokenError):
        store.verify("not-a-valid-token")


def test_verify_rejects_unknown_token():
    store = TokenStore()
    with pytest.raises(TokenError):
        store.verify("deadbeefdeadbeef.somesecret")


def test_verify_rejects_tampered_secret():
    store = TokenStore()
    token = store.issue("admin")
    token_id, _secret = token.split(".", 1)
    with pytest.raises(TokenError):
        store.verify(f"{token_id}.wrong-secret")


def test_verify_rejects_expired_token():
    store = TokenStore()
    token = store.issue("admin", ttl_seconds=-1)
    with pytest.raises(TokenError):
        store.verify(token)


def test_revoke_invalidates_token():
    store = TokenStore()
    token = store.issue("admin")
    token_id, _ = token.split(".", 1)
    store.revoke(token_id)
    with pytest.raises(TokenError):
        store.verify(token)


def test_revoke_unknown_token_id_is_a_no_op():
    store = TokenStore()
    store.revoke("nonexistent")  # no raise


def test_list_active_excludes_expired_and_revoked():
    store = TokenStore()
    live = store.issue("sign", project="p")
    expired = store.issue("admin", ttl_seconds=-1)
    revoked = store.issue("admin")
    revoked_id, _ = revoked.split(".", 1)
    store.revoke(revoked_id)

    active_ids = {t.token_id for t in store.list_active()}
    live_id, _ = live.split(".", 1)
    expired_id, _ = expired.split(".", 1)
    assert live_id in active_ids
    assert expired_id not in active_ids
    assert revoked_id not in active_ids


def test_list_active_does_not_expose_secret():
    store = TokenStore()
    store.issue("sign", project="p")
    for token in store.list_active():
        assert not hasattr(token, "secret_hash")
        assert not hasattr(token, "secret")
