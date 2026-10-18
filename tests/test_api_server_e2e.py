"""End-to-end test: real uvicorn socket + real GuiApprovalBridge, driven
through an actual (offscreen) Qt event loop -- the one thing the
TestClient-based tests in test_api.py can't cover, since TestClient
doesn't run a real server thread or a real Qt event loop.
"""
import json
import os
import shutil
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("httpx")

import httpx  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from zsign.api import ApiServer, KeyLockRegistry, TokenStore, create_app  # noqa: E402
from zsign.gui.approval_bridge import GuiApprovalBridge  # noqa: E402
from zsign.gui.controller import VaultController  # noqa: E402
from zsign.gui.main_window import ApprovalRequestDialog  # noqa: E402

PASSPHRASE = "correct horse battery staple"

pytestmark = pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_real_server_with_real_approval_bridge(qapp, tmp_path):
    controller = VaultController()
    controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    record = controller.generate_key("release", "product-a")

    token_store = TokenStore()
    sign_token = token_store.issue("sign", project="product-a")

    bridge = GuiApprovalBridge()
    # Auto-approve: as soon as a dialog would be shown, accept it instead
    # of blocking on real user input. Connecting to the signal and
    # answering via the Future directly (bypassing the dialog) proves
    # the signal crosses from the API thread to the Qt thread correctly.
    def on_request(request, future):
        if not future.done():
            future.set_result(True)

    bridge.approval_requested.connect(on_request)

    app = create_app(
        controller, token_store, KeyLockRegistry(), approval_handler=bridge.make_handler(),
    )
    server = ApiServer(app, port=18765)
    server.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                httpx.get(f"{server.base_url}/v1/health", timeout=0.5)
                break
            except httpx.TransportError:
                time.sleep(0.05)

        result_box = {}

        def do_request():
            profile = {
                "project": "product-a", "key_id": record.key_id, "version": "1.0.0+0",
                "header_size": 32, "slot_size": 65536, "align": 1, "pad_header": True,
            }
            response = httpx.post(
                f"{server.base_url}/v1/sign",
                data={"metadata": json.dumps(profile)},
                files={"image": ("app.bin", os.urandom(512))},
                headers={"Authorization": f"Bearer {sign_token}"},
                timeout=10,
            )
            result_box["response"] = response

        request_thread = threading.Thread(target=do_request)
        request_thread.start()

        # Pump the Qt event loop so the queued cross-thread signal
        # actually gets delivered to `on_request` while the request
        # thread blocks waiting on the Future.
        deadline = time.time() + 10
        while request_thread.is_alive() and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        request_thread.join(timeout=1)

        assert "response" in result_box
        response = result_box["response"]
        assert response.status_code == 200, response.text
        assert len(response.content) > 0
        assert "X-Signed-SHA256" in response.headers
    finally:
        server.stop()


def test_approval_request_dialog_shows_fields(qapp):
    from zsign.api.approval import ApprovalRequest

    request = ApprovalRequest(
        request_id="r1", client_label="sign-token:abc", project="product-a",
        key_id="k1", key_fingerprint="f" * 64, version="1.0.0+0",
        image_sha256="s" * 64, image_size=1024,
        profile={"header_size": 32, "slot_size": 65536, "security_counter": None},
    )
    dialog = ApprovalRequestDialog(request, timeout=60)
    # Just confirm it constructs with the right data wired in, without
    # actually waiting out the timer.
    assert dialog.windowTitle() == "Approve Signing Request"
    dialog._timeout_timer.stop()
    dialog.deleteLater()
