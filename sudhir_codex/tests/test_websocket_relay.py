"""Loopback integration checks for the authenticated GPT-only WebSocket path."""

from contextlib import closing
import http.client
import json
import socket
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, RejectConnection, RejectData, Request, TextMessage

from helpers import basic_pi_document, make_repo, write_json
from sudhir_codex_gateway.app import GatewayApp, GatewaySettings
from sudhir_codex_gateway.catalog_version import CatalogVersion
from sudhir_codex_gateway.server import GatewayHTTPServer


class WebSocketRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.requests, self.headers = [], []
        self.upstream_closed = threading.Event()
        self.reject = None
        self.response_events = lambda payload: [{"type": "response.completed", "response": {"id": "resp-fixture"}}]
        owner = self

        class Peer(socketserver.BaseRequestHandler):
            def handle(self):
                ws = WSConnection(ConnectionType.SERVER)
                try:
                    while True:
                        data = self.request.recv(65536)
                        if not data:
                            return
                        ws.receive_data(data)
                        for event in ws.events():
                            if isinstance(event, Request):
                                owner.headers.append(dict(event.extra_headers))
                                if owner.reject:
                                    self.request.sendall(ws.send(RejectConnection(
                                        owner.reject,
                                        [(b"content-type", b"application/json"), (b"x-request-id", b"fixture-reject")],
                                        has_body=True,
                                    )))
                                    self.request.sendall(ws.send(RejectData(b'{"error":{"message":"fixture rejection"}}')))
                                    return
                                self.request.sendall(ws.send(AcceptConnection(extra_headers=[
                                    (b"x-codex-turn-state", b"fixture-state"), (b"openai-model", b"gpt-fixture"),
                                ])))
                            elif isinstance(event, TextMessage):
                                payload = json.loads(event.data)
                                owner.requests.append(payload)
                                for response in owner.response_events(payload):
                                    self.request.sendall(ws.send(TextMessage(data=json.dumps(response))))
                            elif isinstance(event, CloseConnection):
                                return
                finally:
                    owner.upstream_closed.set()

        class FixtureServer(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self.peer = FixtureServer(("127.0.0.1", 0), Peer)
        self.peer_thread = threading.Thread(target=self.peer.serve_forever, daemon=True)
        self.peer_thread.start()
        pi = root / "pi"
        write_json(pi / "models.json", basic_pi_document())
        settings = GatewaySettings(
            repo_root=make_repo(root / "repo"), state_dir=root / "state", pi_agent_dir=pi,
            gateway_token="gateway-secret",
            chatgpt_base_url=f"http://127.0.0.1:{self.peer.server_address[1]}/backend-api/codex",
        )
        with patch("sudhir_codex_gateway.app.resolve_catalog_version", return_value=CatalogVersion("0.163.0", "0.163.0", None, None)):
            self.app = GatewayApp(settings, http_client=httpx.Client(transport=httpx.MockTransport(
                lambda _request: self.fail("Fixture attempted unexpected HTTP/network access")
            )))
        self.app._gpt_model_ids = frozenset({"gpt-fixture"})
        self.gateway = GatewayHTTPServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.peer.shutdown()
        self.peer.server_close()
        self.thread.join(2)
        self.peer_thread.join(2)
        self.app.close()
        self.temp.cleanup()

    def upgrade_headers(self, model="gpt-fixture", *, authenticated=True):
        headers = {
            "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "MDEyMzQ1Njc4OWFiY2RlZg==", "x-sudhir-selected-model": model,
            "Authorization": "Bearer chatgpt-fixture", "ChatGPT-Account-Id": "fixture-account",
        }
        if authenticated:
            headers["X-Sudhir-Gateway-Token"] = "gateway-secret"
        return headers

    def http_upgrade(self, model="gpt-fixture", *, authenticated=True):
        with closing(http.client.HTTPConnection("127.0.0.1", self.gateway.server_address[1], timeout=3)) as client:
            client.request("GET", "/v1/responses", headers=self.upgrade_headers(model, authenticated=authenticated))
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_native_gpt_continuation_auth_reasoning_and_disconnect(self):
        sock = socket.create_connection(self.gateway.server_address, timeout=3)
        ws = WSConnection(ConnectionType.CLIENT)
        headers = self.upgrade_headers()
        extra = [(k.encode(), v.encode()) for k, v in headers.items() if k.lower() not in {
            "connection", "upgrade", "sec-websocket-version", "sec-websocket-key",
        }]
        try:
            sock.sendall(ws.send(Request(host="127.0.0.1", target="/v1/responses", extra_headers=extra)))
            accepted = None
            while accepted is None:
                ws.receive_data(sock.recv(65536))
                accepted = next((e for e in ws.events() if isinstance(e, AcceptConnection)), None)
            self.assertIn((b"x-codex-turn-state", b"fixture-state"), accepted.extra_headers)
            first = {"type": "response.create", "model": "gpt-fixture", "input": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "foreign"}]},
                {"type": "reasoning", "encrypted_content": "native-encrypted", "content": []},
                {"type": "message", "role": "user", "content": "visible"},
            ]}
            second = {"type": "response.create", "model": "gpt-fixture", "previous_response_id": "resp-fixture", "input": [], "generate": False}
            interrupt = {"type": "response.interrupt", "response_id": "resp-fixture", "mode": "discard_partial_items"}
            for payload in (first, second, interrupt):
                sock.sendall(ws.send(TextMessage(data=json.dumps(payload))))
                response = None
                while response is None:
                    ws.receive_data(sock.recv(65536))
                    response = next((e.data for e in ws.events() if isinstance(e, TextMessage)), None)
                self.assertEqual(json.loads(response)["type"], "response.completed")
        finally:
            sock.close()
        self.assertTrue(self.upstream_closed.wait(3), "Client disconnect left the owned upstream connection open")
        self.assertEqual(self.requests, [{**first, "input": first["input"][1:]}, second, interrupt])
        self.assertEqual(len(self.headers), 1, "Continuation opened another upstream connection")
        self.assertEqual(self.headers[0][b"authorization"], b"Bearer chatgpt-fixture")
        self.assertNotIn(b"x-sudhir-gateway-token", self.headers[0])
        self.assertNotIn(b"x-sudhir-selected-model", self.headers[0])
        audit = self.app.settings.route_audit_path.read_text()
        self.assertEqual(len(audit.splitlines()), 1)
        self.assertEqual(json.loads(audit)["status"], 200)
        self.assertNotIn("gateway-secret", audit)
        self.assertNotIn("foreign", audit)

    def test_auth_route_and_model_validation_happen_before_upstream_upgrade(self):
        for model, authenticated, expected in [
            ("gpt-fixture", False, 401), ("pi-demo/model", True, 426),
            ("cursor/composer-2.5", True, 426), ("gpt-unknown", True, 404),
        ]:
            with self.subTest(model=model, authenticated=authenticated):
                self.assertEqual(self.http_upgrade(model, authenticated=authenticated)[0], expected)
        self.assertEqual(self.headers, [])

    def test_upstream_handshake_errors_preserve_status_body_and_request_id(self):
        for status in (401, 426):
            self.reject = status
            with self.subTest(status=status):
                actual, headers, body = self.http_upgrade()
                self.assertEqual((actual, headers["x-request-id"], json.loads(body)), (
                    status, "fixture-reject", {"error": {"message": "fixture rejection"}},
                ))

    def test_websocket_opt_out_returns_http_fallback_before_route_work(self):
        with patch.dict("os.environ", {"SUDHIR_CODEX_WEBSOCKETS": "0"}):
            self.assertEqual(self.http_upgrade()[0], 426)
        self.assertEqual(self.headers, [])
