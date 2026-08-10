import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx

from helpers import basic_pi_document
from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.app import BufferedResponse
from sudhir_codex_gateway.app import CHATGPT_CLIENT_VERSION
from sudhir_codex_gateway.app import GATEWAY_TOKEN_HEADER
from sudhir_codex_gateway.app import GatewayApp
from sudhir_codex_gateway.app import GatewaySettings
from sudhir_codex_gateway.app import StreamingResponse
from sudhir_codex_gateway.cursor_worker import CursorTurnResult
from sudhir_codex_gateway.errors import GatewayError
from sudhir_codex_gateway.pi_auth_worker import PiAuthResult


class GatewayAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_repo(self.root / "repo")
        self.state = self.root / "state"
        self.pi = self.root / "pi"
        write_json(self.pi / "models.json", basic_pi_document())
        write_json(
            self.pi / "auth.json",
            {"demo": {"type": "api_key", "key": "pi-secret"}},
        )
        self.settings = GatewaySettings(
            repo_root=self.repo,
            state_dir=self.state,
            pi_agent_dir=self.pi,
            gateway_token="gateway-secret",
            chatgpt_base_url="https://chatgpt.test/backend-api/codex",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_health_identifies_the_gateway_process(self) -> None:
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: self.fail("Health check used HTTP transport")
                )
            ),
        )
        try:
            response = app.health()
        finally:
            app.close()

        self.assertEqual(
            json.loads(response.body),
            {
                "ok": True,
                "service": "sudhir-codex-gateway",
                "instance_id": self.settings.instance_id,
                "pid": os.getpid(),
            },
        )

    def test_pi_request_receives_only_pi_credential(self) -> None:
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["account"] = request.headers.get("chatgpt-account-id")
            observed["gateway"] = request.headers.get(GATEWAY_TOKEN_HEADER.lower())
            observed["cookie"] = request.headers.get("cookie")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Pi replied",
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 2,
                        "total_tokens": 6,
                    },
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        app = GatewayApp(self.settings, http_client=client)
        try:
            response = app.responses(
                {
                    "Authorization": "Bearer chatgpt-secret",
                    "ChatGPT-Account-Id": "account-secret",
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                },
                json.dumps(
                    {
                        "model": "pi-demo/demo/model",
                        "instructions": "Be concise",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "hello-private"}
                                ],
                            }
                        ],
                        "stream": True,
                    }
                ).encode(),
            )
        finally:
            app.close()

        self.assertIsInstance(response, BufferedResponse)
        self.assertEqual(observed["url"], "https://pi.test/v1/chat/completions")
        self.assertEqual(observed["authorization"], "Bearer pi-secret")
        self.assertIsNone(observed["account"])
        self.assertIsNone(observed["gateway"])
        self.assertEqual(observed["body"]["model"], "demo/model")
        self.assertIn(b"Pi replied", response.body)

        audit = self.settings.route_audit_path.read_text(encoding="utf-8")
        self.assertNotIn("hello-private", audit)
        self.assertNotIn("pi-secret", audit)
        self.assertNotIn("chatgpt-secret", audit)
        self.assertNotIn("account-secret", audit)

    def test_pi_provider_error_preserves_status_body_and_request_id(self) -> None:
        cases = (
            (
                400,
                b'{"error":{"message":"414 tools exceeds maximum 350"}}\n',
                {
                    "X-Request-Id": "xai-request-400",
                    "Set-Cookie": "provider-secret=must-not-leak",
                },
                "application/json",
            ),
            (
                401,
                b'{"error":{"message":"provider credential expired"}}',
                {
                    "Content-Type": "application/problem+json",
                    "Request-Id": "provider-request-401",
                },
                "application/problem+json",
            ),
        )

        for status, body, headers, expected_content_type in cases:
            with self.subTest(status=status):
                def handler(_request: httpx.Request) -> httpx.Response:
                    return httpx.Response(status, headers=headers, content=body)

                app = GatewayApp(
                    self.settings,
                    http_client=httpx.Client(
                        transport=httpx.MockTransport(handler)
                    ),
                )
                try:
                    response = app.responses(
                        {GATEWAY_TOKEN_HEADER: "gateway-secret"},
                        json.dumps(
                            {
                                "model": "pi-demo/demo/model",
                                "input": [
                                    {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {"type": "input_text", "text": "hello"}
                                        ],
                                    }
                                ],
                            }
                        ).encode(),
                    )
                finally:
                    app.close()

                self.assertIsInstance(response, BufferedResponse)
                self.assertEqual(response.status, status)
                self.assertEqual(
                    response.headers["content-type"], expected_content_type
                )
                self.assertEqual(response.body, body)
                for name, value in headers.items():
                    if name.lower() in {"request-id", "x-request-id"}:
                        self.assertEqual(response.headers[name.lower()], value)
                self.assertNotIn("set-cookie", response.headers)

    def test_cursor_request_uses_the_native_worker_and_returns_responses_sse(
        self,
    ) -> None:
        class FakeCursorWorker:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []
                self.closed = False

            def turn(self, **kwargs: object) -> CursorTurnResult:
                self.calls.append(kwargs)
                return CursorTurnResult(
                    text="Composer replied",
                    input_tokens=11,
                    output_tokens=5,
                    tool_calls=2,
                )

            def close(self) -> None:
                self.closed = True

        worker = FakeCursorWorker()
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: self.fail("Cursor route used HTTP transport")
                )
            ),
            cursor_worker=worker,
        )
        try:
            response = app.responses(
                {
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                    "thread-id": "thread-cursor",
                },
                json.dumps(
                    {
                        "model": "cursor/composer-latest-slow",
                        "instructions": "Be concise.",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "<environment_context>\n"
                                            f"<cwd>{self.root}</cwd>\n"
                                            "</environment_context>"
                                        ),
                                    },
                                    {
                                        "type": "input_text",
                                        "text": "Inspect this repo.",
                                    },
                                ],
                            }
                        ],
                        "stream": True,
                    }
                ).encode(),
            )
        finally:
            app.close()

        self.assertIsInstance(response, BufferedResponse)
        self.assertIn(b"Composer replied", response.body)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(
            worker.calls[0]["model_id"],
            "cursor/composer-latest-slow",
        )
        self.assertEqual(worker.calls[0]["cwd"], self.root)
        self.assertEqual(worker.calls[0]["thread_id"], "thread-cursor")
        self.assertTrue(worker.closed)

        audit = self.settings.route_audit_path.read_text(encoding="utf-8")
        self.assertIn('"provider_id":"cursor"', audit)
        self.assertNotIn("Inspect this repo.", audit)

    def test_cursor_thread_reuses_last_verified_cwd(self) -> None:
        class FakeCursorWorker:
            def __init__(self) -> None:
                self.cwds: list[Path] = []

            def turn(self, **kwargs: object) -> CursorTurnResult:
                self.cwds.append(kwargs["cwd"])
                return CursorTurnResult("ok", 0, 0, 0)

            def close(self) -> None:
                return

        worker = FakeCursorWorker()
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: self.fail("Cursor route used HTTP transport")
                )
            ),
            cursor_worker=worker,
        )
        headers = {"thread-id": "thread-cursor"}
        try:
            app.responses(
                headers,
                json.dumps(
                    {
                        "model": "cursor/composer-2.5-fast",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": f"<environment_context><cwd>{self.root}</cwd></environment_context>",
                                    }
                                ],
                            }
                        ],
                    }
                ).encode(),
            )
            app.responses(
                headers,
                json.dumps(
                    {
                        "model": "cursor/composer-2.5-slow",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "Continue."}
                                ],
                            }
                        ],
                    }
                ).encode(),
            )
        finally:
            app.close()

        self.assertEqual(worker.cwds, [self.root, self.root])

    def test_anthropic_pi_request_uses_messages_transport(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["models"][0]["api"] = "anthropic-messages"
        write_json(self.pi / "models.json", document)
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["api_key"] = request.headers.get("x-api-key")
            observed["version"] = request.headers.get("anthropic-version")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "hidden",
                            "signature": "opaque",
                        },
                        {"type": "text", "text": "Messages replied"},
                    ],
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                    },
                },
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            response = app.responses(
                {GATEWAY_TOKEN_HEADER: "gateway-secret"},
                json.dumps(
                    {
                        "model": "pi-demo/demo/model",
                        "instructions": "Be concise",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "hello"}],
                            }
                        ],
                        "stream": True,
                    }
                ).encode(),
            )
        finally:
            app.close()

        self.assertIsInstance(response, BufferedResponse)
        self.assertEqual(observed["url"], "https://pi.test/v1/messages")
        self.assertIsNone(observed["authorization"])
        self.assertEqual(observed["api_key"], "pi-secret")
        self.assertEqual(observed["version"], "2023-06-01")
        self.assertEqual(observed["body"]["system"], "Be concise")
        self.assertEqual(observed["body"]["max_tokens"], 8192)
        self.assertNotIn("reasoning_effort", observed["body"])
        self.assertIn(b"Messages replied", response.body)

    def test_xai_grok45_uses_responses_transport(self) -> None:
        write_json(
            self.pi / "models.json",
            {
                "providers": {
                    "xai": {
                        "models": [
                            {
                                "id": "grok-4.5",
                                "reasoning": True,
                                "input": ["text", "image"],
                                "contextWindow": 500_000,
                                "maxTokens": 500_000,
                            }
                        ]
                    }
                }
            },
        )
        write_json(
            self.pi / "auth.json",
            {
                "xai": {
                    "type": "oauth",
                    "access": "xai-secret",
                    "refresh": "xai-refresh",
                    "expires": 9_999_999_999_999,
                }
            },
        )
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["body"] = json.loads(request.content)
            if request.url.path.endswith("/responses"):
                return httpx.Response(
                    200,
                    json={
                        "id": "resp_xai",
                        "status": "completed",
                        "model": "grok-4.5",
                        "output": [
                            {
                                "type": "reasoning",
                                "id": "rs_xai",
                                "status": "completed",
                                "summary": [],
                                "encrypted_content": "opaque-reasoning",
                            },
                            {
                                "type": "message",
                                "id": "msg_xai",
                                "status": "completed",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Grok replied",
                                        "annotations": [],
                                    }
                                ],
                            },
                        ],
                        "usage": {
                            "input_tokens": 4,
                            "output_tokens": 2,
                            "total_tokens": 6,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Wrong transport",
                            }
                        }
                    ]
                },
            )

        class XaiAuthWorker:
            def resolve(self, _model):
                return PiAuthResult(
                    provider_id="xai",
                    model_id="grok-4.5",
                    api="openai-responses",
                    api_key="xai-secret",
                    headers={},
                    base_url=None,
                )

            def close(self) -> None:
                return None

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            pi_auth_worker=XaiAuthWorker(),
        )
        try:
            response = app.responses(
                {GATEWAY_TOKEN_HEADER: "gateway-secret"},
                json.dumps(
                    {
                        "model": "pi-xai/grok-4.5",
                        "instructions": "Be concise",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "hello"}],
                            }
                        ],
                        "reasoning": {"effort": "high"},
                        "stream": True,
                    }
                ).encode(),
            )
        finally:
            app.close()

        self.assertIsInstance(response, BufferedResponse)
        self.assertEqual(observed["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(observed["authorization"], "Bearer xai-secret")
        self.assertEqual(observed["body"]["model"], "grok-4.5")
        self.assertEqual(observed["body"]["stream"], False)
        self.assertEqual(observed["body"]["store"], False)
        self.assertEqual(
            observed["body"]["include"],
            ["reasoning.encrypted_content"],
        )
        self.assertEqual(
            observed["body"]["reasoning"],
            {"effort": "high", "summary": "auto"},
        )
        self.assertNotIn("reasoning_effort", observed["body"])
        self.assertIn(b"opaque-reasoning", response.body)
        self.assertIn(b"Grok replied", response.body)

    def test_gpt_request_keeps_chatgpt_auth_and_strips_gateway_secret(self) -> None:
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "slug": "gpt-test",
                                "display_name": "GPT Test",
                                "visibility": "list",
                            }
                        ]
                    },
                )
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["account"] = request.headers.get("chatgpt-account-id")
            observed["gateway"] = request.headers.get(GATEWAY_TOKEN_HEADER.lower())
            observed["cookie"] = request.headers.get("cookie")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        app = GatewayApp(self.settings, http_client=client)
        response: StreamingResponse | None = None
        try:
            result = app.responses(
                {
                    "Authorization": "Bearer chatgpt-secret",
                    "ChatGPT-Account-Id": "account-secret",
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                    "Cookie": "must-not-forward",
                },
                json.dumps(
                    {
                        "model": "gpt-test",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "gpt-private"}
                                ],
                            }
                        ],
                        "stream": True,
                    }
                ).encode(),
            )
            self.assertIsInstance(result, StreamingResponse)
            response = result
            self.assertEqual(b"".join(response.response.iter_bytes())[:6], b"event:")
        finally:
            if response is not None:
                response.response.close()
            app.close()

        self.assertEqual(
            observed["url"],
            "https://chatgpt.test/backend-api/codex/responses",
        )
        self.assertEqual(observed["authorization"], "Bearer chatgpt-secret")
        self.assertEqual(observed["account"], "account-secret")
        self.assertIsNone(observed["gateway"])
        self.assertIsNone(observed["cookie"])
        self.assertEqual(observed["body"]["model"], "gpt-test")

        audit = self.settings.route_audit_path.read_text(encoding="utf-8")
        self.assertNotIn("gpt-private", audit)
        self.assertNotIn("chatgpt-secret", audit)
        self.assertNotIn("account-secret", audit)

    def test_gpt_request_omits_foreign_plaintext_reasoning(self) -> None:
        observed: dict[str, object] = {}
        openai_reasoning = {
            "type": "reasoning",
            "id": "rs_openai",
            "summary": [],
            "encrypted_content": "opaque-openai-reasoning",
        }
        assistant_message = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "DeepSeek replied"}],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "slug": "gpt-test",
                                "display_name": "GPT Test",
                                "visibility": "list",
                            }
                        ]
                    },
                )
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    b"event: response.completed\n"
                    b'data: {"type":"response.completed"}\n\n'
                ),
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        response: StreamingResponse | None = None
        try:
            result = app.responses(
                {"Authorization": "Bearer chatgpt-secret"},
                json.dumps(
                    {
                        "model": "gpt-test",
                        "input": [
                            openai_reasoning,
                            {
                                "type": "reasoning",
                                "id": "foreign-reasoning",
                                "summary": [],
                                "content": [
                                    {
                                        "type": "reasoning_text",
                                        "text": "DeepSeek private reasoning",
                                    }
                                ],
                                "encrypted_content": None,
                            },
                            assistant_message,
                        ],
                        "stream": True,
                    }
                ).encode(),
            )
            self.assertIsInstance(result, StreamingResponse)
            response = result
            self.assertEqual(b"".join(response.response.iter_bytes())[:6], b"event:")
        finally:
            if response is not None:
                response.response.close()
            app.close()

        self.assertEqual(
            observed["body"]["input"],  # type: ignore[index]
            [openai_reasoning, assistant_message],
        )

    def test_search_proxies_pi_model_through_chatgpt_auth(self) -> None:
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["account"] = request.headers.get("chatgpt-account-id")
            observed["gateway"] = request.headers.get(GATEWAY_TOKEN_HEADER.lower())
            observed["cookie"] = request.headers.get("cookie")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "X-Request-Id": "search-request-id",
                },
                json={"output": "Search result"},
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        request_body = json.dumps(
            {
                "id": "search-session",
                "model": "pi-deepseek/deepseek-v4-flash",
                "commands": {"search_query": [{"q": "private-search-query"}]},
            }
        ).encode()
        try:
            response = app.search(
                {
                    "Authorization": "Bearer chatgpt-secret",
                    "ChatGPT-Account-Id": "account-secret",
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                    "Cookie": "must-not-forward",
                },
                request_body,
            )
        finally:
            app.close()

        self.assertEqual(
            observed["url"],
            "https://chatgpt.test/backend-api/codex/alpha/search",
        )
        self.assertEqual(observed["authorization"], "Bearer chatgpt-secret")
        self.assertEqual(observed["account"], "account-secret")
        self.assertIsNone(observed["gateway"])
        self.assertIsNone(observed["cookie"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["x-request-id"], "search-request-id")

        audit = self.settings.route_audit_path.read_text(encoding="utf-8")
        self.assertIn('"provider_id":"openai-codex-search"', audit)
        self.assertNotIn("private-search-query", audit)

    def test_image_generation_proxies_through_chatgpt_auth(self) -> None:
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["gateway"] = request.headers.get(GATEWAY_TOKEN_HEADER.lower())
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "X-Request-Id": "image-request-id",
                },
                json={"data": [{"b64_json": "image-data"}]},
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        request_body = json.dumps(
            {"model": "gpt-image-2", "prompt": "private-image-prompt"}
        ).encode()
        try:
            response = app.generate_image(
                {
                    "Authorization": "Bearer chatgpt-secret",
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                },
                request_body,
            )
        finally:
            app.close()

        self.assertEqual(
            observed["url"],
            "https://chatgpt.test/backend-api/codex/images/generations",
        )
        self.assertEqual(observed["authorization"], "Bearer chatgpt-secret")
        self.assertIsNone(observed["gateway"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["x-request-id"], "image-request-id")

        audit = self.settings.route_audit_path.read_text(encoding="utf-8")
        self.assertIn('"provider_id":"openai-codex-image"', audit)
        self.assertNotIn("private-image-prompt", audit)

    def test_malformed_pi_catalog_cannot_block_gpt_requests(self) -> None:
        write_json(
            self.pi / "models.json",
            {
                "providers": {
                    "broken": {
                        "models": [{"id": "broken/model"}],
                    }
                }
            },
        )
        post_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_calls
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "slug": "gpt-test",
                                "display_name": "GPT Test",
                                "visibility": "list",
                            }
                        ]
                    },
                )
            post_calls += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    b"event: response.completed\n"
                    b'data: {"type":"response.completed"}\n\n'
                ),
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        response: StreamingResponse | None = None
        try:
            result = app.responses(
                {"Authorization": "Bearer chatgpt-secret"},
                json.dumps(
                    {
                        "model": "gpt-test",
                        "input": [],
                        "stream": True,
                    }
                ).encode(),
            )
            self.assertIsInstance(result, StreamingResponse)
            response = result
            self.assertEqual(b"".join(response.response.iter_bytes())[:6], b"event:")
        finally:
            if response is not None:
                response.response.close()
            app.close()

        self.assertEqual(post_calls, 1)

    def test_pi_catalog_refreshes_additions_and_removals(self) -> None:
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda _request: httpx.Response(500))
            ),
        )
        try:
            initial = app.catalog()

            document = basic_pi_document()
            document["providers"]["demo"]["models"].append(
                {
                    "id": "demo/new-model",
                    "name": "New Model",
                }
            )
            write_json(self.pi / "models.json", document)
            added = app.catalog()

            document["providers"]["demo"]["models"] = [
                {
                    "id": "demo/new-model",
                    "name": "New Model",
                }
            ]
            write_json(self.pi / "models.json", document)
            removed = app.catalog()
        finally:
            app.close()

        self.assertEqual(
            set(initial.by_gateway_id),
            {"pi-demo/demo/model"},
        )
        self.assertEqual(
            set(added.by_gateway_id),
            {"pi-demo/demo/model", "pi-demo/demo/new-model"},
        )
        self.assertEqual(
            set(removed.by_gateway_id),
            {"pi-demo/demo/new-model"},
        )

    def test_pi_catalog_refresh_retries_replacement_during_load(self) -> None:
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda _request: httpx.Response(500))
            ),
        )
        try:
            app.catalog()
            models_path = self.pi / "models.json"

            document_b = basic_pi_document()
            document_b["providers"]["demo"]["models"] = [
                {"id": "demo/model-b", "name": "Model B"}
            ]
            replacement_b = self.pi / "models-b.json"
            write_json(replacement_b, document_b)
            replacement_b.replace(models_path)

            document_c = basic_pi_document()
            document_c["providers"]["demo"]["models"] = [
                {"id": "demo/model-c", "name": "Model C"}
            ]
            original_load = app.loader.load
            load_count = 0

            def load_then_replace() -> object:
                nonlocal load_count
                load_count += 1
                loaded = original_load()
                if load_count == 1:
                    replacement_c = self.pi / "models-c.json"
                    write_json(replacement_c, document_c)
                    replacement_c.replace(models_path)
                return loaded

            app.loader.load = load_then_replace
            refreshed = app.catalog()
            unchanged = app.catalog()
        finally:
            app.close()

        self.assertEqual(load_count, 2)
        self.assertEqual(
            set(refreshed.by_gateway_id),
            {"pi-demo/demo/model-c"},
        )
        self.assertEqual(
            set(unchanged.by_gateway_id),
            {"pi-demo/demo/model-c"},
        )

    def test_cached_gpt_model_is_allowed_when_live_catalog_fails(self) -> None:
        write_json(
            self.settings.gpt_cache_path,
            {
                "models": [
                    {
                        "slug": "gpt-cached",
                        "display_name": "GPT Cached",
                        "visibility": "list",
                    }
                ]
            },
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(503)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    b"event: response.completed\n"
                    b'data: {"type":"response.completed"}\n\n'
                ),
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        response: StreamingResponse | None = None
        try:
            result = app.responses(
                {"Authorization": "Bearer chatgpt-secret"},
                json.dumps(
                    {
                        "model": "gpt-cached",
                        "input": [],
                        "stream": True,
                    }
                ).encode(),
            )
            self.assertIsInstance(result, StreamingResponse)
            response = result
        finally:
            if response is not None:
                response.response.close()
            app.close()

    def test_unknown_gpt_model_fails_before_responses_upstream(self) -> None:
        post_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_calls
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "slug": "gpt-known",
                                "display_name": "GPT Known",
                                "visibility": "list",
                            }
                        ]
                    },
                )
            post_calls += 1
            return httpx.Response(500)

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            with self.assertRaisesRegex(GatewayError, "Unknown GPT model ID"):
                app.responses(
                    {"Authorization": "Bearer chatgpt-secret"},
                    json.dumps(
                        {
                            "model": "gpt-unknown",
                            "input": [],
                            "stream": True,
                        }
                    ).encode(),
                )
        finally:
            app.close()

        self.assertEqual(post_calls, 0)

    def test_models_merge_live_gpt_and_pi_catalogs(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/backend-api/codex/models")
            self.assertEqual(
                request.url.params.get("client_version"),
                CHATGPT_CLIENT_VERSION,
            )
            self.assertEqual(request.url.params.get("feature"), "kept")
            self.assertEqual(
                request.headers.get("authorization"),
                "Bearer chatgpt-secret",
            )
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "gpt-test",
                            "display_name": "GPT Test",
                            "visibility": "list",
                            "priority": 1,
                        }
                    ]
                },
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            response = app.list_models(
                {
                    "Authorization": "Bearer chatgpt-secret",
                    GATEWAY_TOKEN_HEADER: "gateway-secret",
                },
                "client_version=0.0.0&feature=kept",
            )
        finally:
            app.close()

        document = json.loads(response.body)
        by_id = {model["slug"]: model for model in document["models"]}
        self.assertEqual(
            set(by_id),
            {
                "gpt-test",
                "pi-demo/demo/model",
                "cursor/composer-2.5-fast",
                "cursor/composer-2.5-slow",
                "cursor/composer-latest-fast",
                "cursor/composer-latest-slow",
            },
        )
        self.assertEqual(by_id["gpt-test"]["multi_agent_version"], "v2")
        self.assertEqual(
            by_id["pi-demo/demo/model"]["multi_agent_version"],
            "v2",
        )
        self.assertEqual(
            by_id["cursor/composer-latest-fast"]["multi_agent_version"],
            "v2",
        )
        self.assertEqual(response.headers["X-Sudhir-GPT-Catalog"], "live")
        self.assertIn("ETag", response.headers)

    def test_concurrent_live_catalog_writes_leave_valid_cache(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "gpt-test",
                            "display_name": "GPT Test",
                            "visibility": "list",
                        }
                    ]
                },
            )

        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                responses = list(
                    pool.map(
                        lambda _index: app.list_models(
                            {"Authorization": "Bearer chatgpt-secret"},
                            "",
                        ),
                        range(64),
                    )
                )
        finally:
            app.close()

        self.assertEqual([response.status for response in responses], [200] * 64)
        cached = json.loads(self.settings.gpt_cache_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [model["slug"] for model in cached["models"]],
            ["gpt-test"],
        )

    def test_gateway_token_comparison_is_exact(self) -> None:
        app = GatewayApp(
            self.settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda _request: httpx.Response(500))
            ),
        )
        try:
            self.assertTrue(app.authenticate("gateway-secret"))
            self.assertFalse(app.authenticate("Gateway-secret"))
            self.assertFalse(app.authenticate(None))
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
