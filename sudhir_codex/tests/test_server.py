import concurrent.futures
import http.client
import json
import threading
import unittest

from sudhir_codex_gateway.app import BufferedResponse
from sudhir_codex_gateway.app import GATEWAY_TOKEN_HEADER
from sudhir_codex_gateway.server import GatewayHTTPServer
from sudhir_codex_gateway.server import LOOPBACK_HOST


class StubApp:
    def __init__(self) -> None:
        self.health_calls = 0
        self.model_calls = 0

    def authenticate(self, provided: str | None) -> bool:
        return provided == "local-secret"

    def health(self) -> BufferedResponse:
        self.health_calls += 1
        return BufferedResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"ok":true}',
        )

    def list_models(
        self,
        _incoming_headers: dict[str, str],
        _query_string: str,
    ) -> BufferedResponse:
        self.model_calls += 1
        return BufferedResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"models":[]}',
        )


def get(
    port: int,
    path: str,
    token: str | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=3)
    headers = {GATEWAY_TOKEN_HEADER: token} if token is not None else {}
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class GatewayServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = StubApp()
        self.server = GatewayHTTPServer((LOOPBACK_HOST, 0), self.app)  # type: ignore[arg-type]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_unauthorized_request_does_no_route_work(self) -> None:
        status, body = get(self.port, "/healthz")

        self.assertEqual(status, 401)
        self.assertEqual(self.app.health_calls, 0)
        self.assertEqual(self.app.model_calls, 0)
        self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_authorized_health_request(self) -> None:
        status, body = get(self.port, "/healthz", "local-secret")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertEqual(self.app.health_calls, 1)

    def test_threaded_server_handles_concurrent_requests(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _index: get(
                        self.port,
                        "/v1/models",
                        "local-secret",
                    ),
                    range(8),
                )
            )

        self.assertEqual([status for status, _body in results], [200] * 8)
        self.assertEqual(self.app.model_calls, 8)

    def test_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "only to 127.0.0.1"):
            GatewayHTTPServer(("0.0.0.0", 0), self.app)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
