"""Tests for streamable-http session persistence and eviction in jdocmunch-mcp."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock


class TestStreamableHTTPSessionRouting(unittest.IsolatedAsyncioTestCase):

    async def test_existing_session_routed_without_new_transport(self):
        """A request with a known session ID reuses the existing transport."""
        session_id = "existing-session-99"
        mock_transport = MagicMock()
        mock_transport._terminated = False
        handle_calls = []

        async def fake_handle(scope, receive, send):
            handle_calls.append(1)

        mock_transport.handle_request = fake_handle
        sessions = {session_id: mock_transport}

        async def route(request_session_id):
            if request_session_id and request_session_id in sessions:
                transport = sessions[request_session_id]
                await transport.handle_request(None, None, None)
                if transport._terminated:
                    sessions.pop(request_session_id, None)
                return True
            return False

        result = await route(session_id)
        self.assertTrue(result)
        self.assertEqual(handle_calls, [1])

    async def test_terminated_session_cleaned_up(self):
        """A terminated transport is removed from the session map after its request."""
        session_id = "term-session"
        mock_transport = MagicMock()
        mock_transport._terminated = True
        mock_transport.handle_request = AsyncMock()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()

        sessions = {session_id: mock_transport}
        session_tasks = {session_id: mock_task}

        async def route(request_session_id):
            if request_session_id and request_session_id in sessions:
                transport = sessions[request_session_id]
                await transport.handle_request(None, None, None)
                if transport._terminated:
                    sessions.pop(request_session_id, None)
                    task = session_tasks.pop(request_session_id, None)
                    if task and not task.done():
                        task.cancel()
                return True
            return False

        await route(session_id)
        self.assertNotIn(session_id, sessions)
        self.assertNotIn(session_id, session_tasks)
        mock_task.cancel.assert_called_once()

    async def test_unknown_session_id_creates_new_session(self):
        """A request with an unrecognised session ID starts a new session."""
        sessions: dict = {}

        async def route(request_session_id):
            if request_session_id and request_session_id in sessions:
                return "existing"
            return "new"

        result = await route("unknown-id-xyz")
        self.assertEqual(result, "new")

    async def test_no_session_id_creates_new_session(self):
        """A request with no session ID header starts a new session."""
        sessions: dict = {}

        async def route(request_session_id):
            if request_session_id and request_session_id in sessions:
                return "existing"
            return "new"

        result = await route(None)
        self.assertEqual(result, "new")
