import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.anthropic import anthropic_response_to_chat
from sudhir_codex_gateway.anthropic import chat_request_to_anthropic
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.errors import GatewayError


class AnthropicAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = make_repo(root / "repo")
        self.pi = root / "pi"
        write_json(
            self.pi / "models.json",
            {
                "providers": {
                    "opencode-go": {
                        "baseUrl": "https://opencode.test/zen/go/v1",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "qwen3.7-plus",
                                "api": "anthropic-messages",
                                "reasoning": True,
                                "maxTokens": 65_536,
                            }
                        ],
                    }
                }
            },
        )
        self.model = (
            CatalogLoader(
                self.pi / "models.json",
                self.repo / "codex-rs" / "models-manager" / "prompt.md",
            )
            .load()
            .models[0]
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_translates_tools_images_and_tool_history(self) -> None:
        chat = {
            "model": "qwen3.7-plus",
            "messages": [
                {"role": "system", "content": "Use tools."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Inspect this"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,AA==",
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call with spaces",
                            "type": "function",
                            "function": {
                                "name": "shell_command",
                                "arguments": '{"command":"pwd"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call with spaces",
                    "content": "/tmp/project",
                },
                {"role": "user", "content": "Continue"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "description": "Run a command",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                        },
                    },
                }
            ],
            "tool_choice": "required",
            "thinking": {"type": "enabled", "budget_tokens": 16_000},
        }

        translated = chat_request_to_anthropic(chat, self.model)

        self.assertEqual(translated["system"], "Use tools.")
        self.assertEqual(translated["max_tokens"], 65_536)
        self.assertEqual(translated["tool_choice"], {"type": "any"})
        self.assertEqual(
            translated["tools"][0]["input_schema"]["type"],
            "object",
        )
        self.assertEqual(
            [message["role"] for message in translated["messages"]],
            ["user", "assistant", "user"],
        )
        image = translated["messages"][0]["content"][1]
        self.assertEqual(image["source"]["type"], "base64")
        call = translated["messages"][1]["content"][0]
        result = translated["messages"][2]["content"][0]
        self.assertRegex(call["id"], r"^[A-Za-z0-9_-]{1,64}$")
        self.assertEqual(result["tool_use_id"], call["id"])
        self.assertEqual(
            translated["messages"][2]["content"][1],
            {"type": "text", "text": "Continue"},
        )

    def test_converts_text_tools_and_cache_usage_to_chat(self) -> None:
        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden", "signature": "opaque"},
                {"type": "text", "text": "Running it."},
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": "shell_command",
                    "input": {"command": "pwd"},
                },
            ],
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 7,
                "cache_read_input_tokens": 11,
                "output_tokens": 13,
            },
        }

        chat = anthropic_response_to_chat(response)

        message = chat["choices"][0]["message"]
        self.assertEqual(message["content"], "Running it.")
        self.assertEqual(message["tool_calls"][0]["id"], "toolu_test")
        self.assertEqual(
            json.loads(message["tool_calls"][0]["function"]["arguments"]),
            {"command": "pwd"},
        )
        self.assertEqual(chat["usage"]["prompt_tokens"], 23)
        self.assertEqual(chat["usage"]["total_tokens"], 36)

    def test_rejects_thinking_budget_that_consumes_all_output(self) -> None:
        with self.assertRaisesRegex(GatewayError, "smaller than max_tokens"):
            chat_request_to_anthropic(
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": 65_536,
                    },
                },
                self.model,
            )


if __name__ == "__main__":
    unittest.main()
