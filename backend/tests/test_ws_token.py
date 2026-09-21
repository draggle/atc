"""A hosted backend behind TOWER_TOKEN closes a WebSocket that does not carry it, before accept."""
from __future__ import annotations

import pytest


def test_ws_token_gate(monkeypatch):
    monkeypatch.setenv("TOWER_TOKEN", "s3cret")
    import app as APP
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(APP.app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws"):
                pass
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws?token=wrong"):
                pass
        with c.websocket_connect("/ws?token=s3cret") as ws:
            ws.close()


def test_ws_open_without_token(monkeypatch):
    monkeypatch.delenv("TOWER_TOKEN", raising=False)
    import app as APP
    from fastapi.testclient import TestClient

    with TestClient(APP.app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.close()
