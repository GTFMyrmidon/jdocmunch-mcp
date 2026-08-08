"""Integration tests for streamable-http transport in jdocmunch-mcp.

These exercise the Starlette app routing stack built by run_streamable_http_server.
"""

import asyncio
import json
import unittest

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route

_MCP_SESSION_ID_HEADER = "mcp-session-id"


def _build_app():
    """Return a Starlette app wired identically to run_streamable_http_server."""
    import uuid

    _sessions: dict = {}
    _session_tasks: dict = {}

    class _AlreadySent:
        async def __call__(self, scope, receive, send):
            pass

    _ALREADY_SENT = _AlreadySent()

    class _FakeTransport:
        def __init__(self, session_id: str):
            self.session_id = session_id
            self._terminated = False
            self.handle_count = 0

        async def handle_request(self, scope, receive, send):
            self.handle_count += 1
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"session": self.session_id}}).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                    [_MCP_SESSION_ID_HEADER.encode(), self.session_id.encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})

    async def handle_mcp(request: Request):
        session_id = request.headers.get(_MCP_SESSION_ID_HEADER)

        if session_id and session_id in _sessions:
            transport = _sessions[session_id]
            await transport.handle_request(request.scope, request.receive, request._send)
            if transport._terminated:
                _sessions.pop(session_id, None)
                task = _session_tasks.pop(session_id, None)
                if task and not task.done():
                    task.cancel()
            return _ALREADY_SENT

        new_id = uuid.uuid4().hex
        transport = _FakeTransport(new_id)
        _sessions[new_id] = transport

        await transport.handle_request(request.scope, request.receive, request._send)
        return _ALREADY_SENT

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
        ],
    )
    return app, _sessions


class TestStreamableHTTPIntegration(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.app, self.sessions = _build_app()
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_initial_post_creates_session(self):
        resp = await self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(_MCP_SESSION_ID_HEADER, resp.headers)
        sid = resp.headers[_MCP_SESSION_ID_HEADER]
        self.assertIn(sid, self.sessions)

    async def test_subsequent_post_reuses_session(self):
        r1 = await self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        sid = r1.headers[_MCP_SESSION_ID_HEADER]
        t = self.sessions[sid]

        r2 = await self.client.post(
            "/mcp",
            headers={_MCP_SESSION_ID_HEADER: sid},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(t.handle_count, 2)
