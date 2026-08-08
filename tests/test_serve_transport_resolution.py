"""Tests for serve transport endpoint resolution and runtime_identity transport recording."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jdocmunch_mcp import runtime_identity
from jdocmunch_mcp.server import _resolve_serve_endpoint


class TestServeTransportResolution(unittest.TestCase):

    def test_default_endpoint_resolution(self):
        args = SimpleNamespace()
        transport, host, port = _resolve_serve_endpoint(args)
        self.assertEqual(transport, "stdio")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8902)

    def test_cli_flags_override_defaults(self):
        args = SimpleNamespace(transport="streamable-http", host="0.0.0.0", port=9000)
        transport, host, port = _resolve_serve_endpoint(args)
        self.assertEqual(transport, "streamable-http")
        self.assertEqual(host, "0.0.0.0")
        self.assertEqual(port, 9000)

    @patch.dict(os.environ, {"JDOCMUNCH_TRANSPORT": "streamable-http", "JDOCMUNCH_HOST": "127.0.0.2", "JDOCMUNCH_PORT": "8999"})
    def test_env_vars_override_defaults(self):
        args = SimpleNamespace()
        transport, host, port = _resolve_serve_endpoint(args)
        self.assertEqual(transport, "streamable-http")
        self.assertEqual(host, "127.0.0.2")
        self.assertEqual(port, 8999)

    def test_runtime_identity_set_transport(self):
        runtime_identity.set_transport("streamable-http")
        payload = runtime_identity.identity_payload()
        self.assertEqual(payload["transport"], "streamable-http")
        # Reset back to stdio for test isolation
        runtime_identity.set_transport("stdio")
