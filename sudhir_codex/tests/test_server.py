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
        self.search_calls: list[bytes] = []
        self.image_generation_calls: list[bytes] = []
        self.image_edit_calls: list[bytes] = []

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

    def search(
        self,
        _incoming_headers: dict[str, str],
        body: bytes,
    ) -> BufferedResponse:
        self.search_calls.append(body)
        return BufferedResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"output":"found"}',
        )

    def generate_image(
        self,
        _incoming_headers: dict[str, str],
        body: bytes,
    ) -> BufferedResponse:
        self.image_generation_calls.append(body)
        return BufferedResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"data":[{"b64_json":"image-data"}]}',
        )

    def edit_image(
        self,
        _incoming_headers: dict[str, str],
        body: bytes,
    ) -> BufferedResponse:
        self.image_edit_calls.append(body)
        return BufferedResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"data":[{"b64_json":"edited-image-data"}]}',
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


def post(
    port: int,
    path: str,
    body: bytes,
    token: str | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=3)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if token is not None:
        headers[GATEWAY_TOKEN_HEADER] = token
    try:
        connection.request("POST", path, body=body, headers=headers)
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

    def test_authorized_health_request_with_bearer_header(self) -> None:
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3)
        try:
            connection.request(
                "GET",
                "/healthz",
                headers={"Authorization": "Bearer local-secret"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"ok": True})
        finally:
            connection.close()

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

    def test_authorized_standalone_search_route(self) -> None:
        request_body = b'{"model":"pi-deepseek/deepseek-v4-flash"}'

        status, body = post(
            self.port,
            "/v1/alpha/search",
            request_body,
            "local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"output": "found"})
        self.assertEqual(self.app.search_calls, [request_body])

    def test_authorized_image_generation_route(self) -> None:
        request_body = b'{"model":"gpt-image-2","prompt":"private prompt"}'

        status, body = post(
            self.port,
            "/v1/images/generations",
            request_body,
            "local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"data": [{"b64_json": "image-data"}]})
        self.assertEqual(self.app.image_generation_calls, [request_body])

    def test_authorized_image_edit_route(self) -> None:
        request_body = b'{"model":"gpt-image-2","prompt":"edit prompt"}'

        status, body = post(
            self.port,
            "/v1/images/edits",
            request_body,
            "local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {"data": [{"b64_json": "edited-image-data"}]},
        )
        self.assertEqual(self.app.image_edit_calls, [request_body])

    def test_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "only to 127.0.0.1"):
            GatewayHTTPServer(("0.0.0.0", 0), self.app)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
