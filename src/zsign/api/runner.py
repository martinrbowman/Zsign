from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI


class ApiServer:
    """Runs the FastAPI app in a background thread, bound to 127.0.0.1
    only. Per spec.md's architecture: API handling runs outside the GUI
    event loop; this class is that boundary."""

    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 8765):
        if host != "127.0.0.1":
            raise ValueError("ApiServer only binds to 127.0.0.1")
        self._config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None
        self.host = host
        self.port = port

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self.is_running:
            return
        self._server.should_exit = False
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self.is_running:
            return
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
