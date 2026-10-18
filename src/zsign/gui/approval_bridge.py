from __future__ import annotations

import concurrent.futures

from PySide6.QtCore import QObject, Signal

from zsign.api.approval import ApprovalHandler, ApprovalRequest

APPROVAL_TIMEOUT_SECONDS = 120.0


class GuiApprovalBridge(QObject):
    """Bridges the API's blocking ApprovalHandler contract onto Qt's
    cross-thread signal queue.

    The handler runs on the API thread (inside uvicorn's Starlette
    threadpool) and must block until approved/denied. It emits a signal
    carrying the request and a Future; Qt auto-queues delivery to
    whichever thread this QObject lives in (the GUI thread), where a
    connected slot shows a dialog and resolves the Future. If nothing
    resolves it before the timeout, the handler returns False -- per
    spec.md, "rejected and expired approvals must not sign."
    """

    approval_requested = Signal(object, object)  # ApprovalRequest, Future

    def make_handler(self, timeout: float = APPROVAL_TIMEOUT_SECONDS) -> ApprovalHandler:
        def handler(request: ApprovalRequest) -> bool:
            future: concurrent.futures.Future = concurrent.futures.Future()
            self.approval_requested.emit(request, future)
            try:
                return bool(future.result(timeout=timeout))
            except concurrent.futures.TimeoutError:
                return False

        return handler
