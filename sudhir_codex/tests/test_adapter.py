import json
import tempfile
import unittest
from pathlib import Path

from helpers import basic_pi_document
from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.adapter import chat_response_to_sse
from sudhir_codex_gateway.adapter import responses_to_chat_request
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.errors import GatewayError


def response_events(payload: bytes) -> list[dict[str, object]]:
    events = []
    for line in payload.decode().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line.removeprefix("data: ")))
    return events


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_repo(self.root / "repo")
        self.pi = self.root / "pi"
        write_json(self.pi / "models.json", basic_pi_document())
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

    def request(self) -> dict[str, object]:
        return {
            "model": self.model.gateway_id,
            "instructions": "You are a coding agent.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect the repo"}],
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "description": "Run a shell command",
                    "strict": False,
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch",
                    "format": {
                        "type": "grammar",
                        "syntax": "lark",
                        "definition": "start: /.+/",
                    },
                },
                {
                    "type": "namespace",
                    "name": "multi_agent_v1",
                    "description": "Agent tools",
                    "tools": [
                        {
                            "type": "function",
                            "name": "spawn_agent",
                            "description": "Spawn an agent",
                            "strict": False,
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "model": {"type": "string"},
                                    "message": {"type": "string"},
                                },
                            },
                        }
                    ],
                },
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "reasoning": {"effort": "medium"},
            "stream": True,
        }

    def load_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        reasoning: bool = True,
        compat: dict[str, object] | None = None,
    ):
        document = {
            "providers": {
                provider_id: {
                    "baseUrl": (
                        "http://127.0.0.1:18081/v1"
                        if provider_id == "backup-llama"
                        else "https://provider.test/v1"
                    ),
                    "compat": compat or {},
                    "models": [{"id": model_id, "reasoning": reasoning}],
                }
            }
        }
        write_json(self.pi / "models.json", document)
        return (
            CatalogLoader(
                self.pi / "models.json",
                self.repo / "codex-rs" / "models-manager" / "prompt.md",
            )
            .load()
            .models[0]
        )

    def test_translates_messages_tools_and_reasoning(self) -> None:
        request, bindings = responses_to_chat_request(self.request(), self.model)

        self.assertEqual(request["model"], "demo/model")
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertEqual(request["messages"][1]["content"], "Inspect the repo")
        self.assertEqual(len(request["tools"]), 3)
        self.assertTrue(request["parallel_tool_calls"])
        self.assertEqual(request["reasoning_effort"], "high")
        qualified = {binding.qualified_name for binding in bindings.bindings}
        self.assertEqual(
            qualified,
            {"shell_command", "apply_patch", "multi_agent_v1.spawn_agent"},
        )

    def test_direct_deepseek_translates_none_high_and_max(self) -> None:
        model = self.load_model(
            "deepseek",
            "deepseek-v4-pro",
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()

        request["reasoning"] = {"effort": "none"}
        none_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(none_request["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", none_request)

        request["reasoning"] = {"effort": "high"}
        high_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(high_request["thinking"], {"type": "enabled"})
        self.assertEqual(high_request["reasoning_effort"], "high")

        request["reasoning"] = {"effort": "max"}
        max_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(max_request["thinking"], {"type": "enabled"})
        self.assertEqual(max_request["reasoning_effort"], "max")

    def test_nvidia_glm52_does_not_invent_undocumented_hosted_controls(self) -> None:
        model = self.load_model(
            "nvidia",
            "z-ai/glm-5.2",
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()

        request["reasoning"] = {"effort": "none"}
        none_request, _ = responses_to_chat_request(request, model)
        self.assertNotIn("reasoning_effort", none_request)
        self.assertNotIn("thinking", none_request)
        self.assertNotIn("chat_template_kwargs", none_request)

        request["reasoning"] = {"effort": "max"}
        max_request, _ = responses_to_chat_request(request, model)
        self.assertNotIn("reasoning_effort", max_request)
        self.assertNotIn("thinking", max_request)
        self.assertNotIn("chat_template_kwargs", max_request)

    def test_direct_zai_glm52_sends_exact_documented_effort(self) -> None:
        model = self.load_model(
            "zai",
            "glm-5.2",
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()
        request["reasoning"] = {"effort": "minimal"}

        chat, _ = responses_to_chat_request(request, model)

        self.assertEqual(chat["thinking"], {"type": "enabled"})
        self.assertEqual(chat["reasoning_effort"], "minimal")

    def test_openrouter_uses_nested_reasoning_effort(self) -> None:
        model = self.load_model(
            "openrouter",
            "openai/gpt-5.6-sol",
            compat={"thinkingFormat": "openrouter"},
        )
        request = self.request()
        request["reasoning"] = {"effort": "max"}

        chat, _ = responses_to_chat_request(request, model)

        self.assertEqual(chat["reasoning"], {"effort": "max"})
        self.assertNotIn("reasoning_effort", chat)

    def test_cerebras_glm47_maps_high_to_enabled_default(self) -> None:
        model = self.load_model(
            "cerebras",
            "zai-glm-4.7",
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()

        request["reasoning"] = {"effort": "none"}
        none_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(none_request["reasoning_effort"], "none")

        request["reasoning"] = {"effort": "high"}
        high_request, _ = responses_to_chat_request(request, model)
        self.assertNotIn("reasoning_effort", high_request)

    def test_open_code_qwen_translates_messages_reasoning_budgets(self) -> None:
        model = self.load_model(
            "opencode-go",
            "qwen3.7-plus",
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()

        request["reasoning"] = {"effort": "none"}
        none_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(none_request["thinking"], {"type": "disabled"})

        request["reasoning"] = {"effort": "high"}
        high_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(
            high_request["thinking"],
            {"type": "enabled", "budget_tokens": 16_000},
        )

        request["reasoning"] = {"effort": "max"}
        max_request, _ = responses_to_chat_request(request, model)
        self.assertEqual(
            max_request["thinking"],
            {"type": "enabled", "budget_tokens": 31_999},
        )

    def test_incompatible_configured_effort_falls_back_to_model_default(self) -> None:
        model = self.load_model(
            "backup-llama",
            "gemma4-31b-qat-q4xl",
            reasoning=False,
            compat={"supportsReasoningEffort": False},
        )
        request = self.request()
        request["reasoning"] = {"effort": "high"}

        chat, _ = responses_to_chat_request(request, model)

        self.assertNotIn("reasoning_effort", chat)
        self.assertNotIn("thinking", chat)
        self.assertNotIn("chat_template_kwargs", chat)

    def test_removes_responses_encryption_markers_from_chat_tool_schemas(self) -> None:
        source = self.request()
        parameters = source["tools"][0]["parameters"]
        parameters["properties"]["command"]["encrypted"] = True
        parameters["allOf"] = [
            {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "encrypted": True,
                    }
                },
            }
        ]

        chat, _bindings = responses_to_chat_request(source, self.model)
        chat_schema = chat["tools"][0]["function"]["parameters"]

        self.assertNotIn("encrypted", chat_schema["properties"]["command"])
        self.assertNotIn(
            "encrypted",
            chat_schema["allOf"][0]["properties"]["message"],
        )
        self.assertTrue(parameters["properties"]["command"]["encrypted"])
        self.assertTrue(parameters["allOf"][0]["properties"]["message"]["encrypted"])

    def test_round_trips_function_custom_and_namespaced_calls(self) -> None:
        _request, bindings = responses_to_chat_request(self.request(), self.model)
        names = {
            binding.qualified_name: binding.encoded_name
            for binding in bindings.bindings
        }
        chat_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I will use the tools.",
                        "tool_calls": [
                            {
                                "id": "call-shell",
                                "type": "function",
                                "function": {
                                    "name": names["shell_command"],
                                    "arguments": '{"command":"pwd"}',
                                },
                            },
                            {
                                "id": "call-patch",
                                "type": "function",
                                "function": {
                                    "name": names["apply_patch"],
                                    "arguments": '{"input":"*** Begin Patch"}',
                                },
                            },
                            {
                                "id": "call-agent",
                                "type": "function",
                                "function": {
                                    "name": names["multi_agent_v1.spawn_agent"],
                                    "arguments": '{"model":"pi-demo/demo/model","message":"review"}',
                                },
                            },
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        events = response_events(chat_response_to_sse(chat_response, bindings))
        items = [
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
        ]

        self.assertEqual(items[0]["type"], "message")
        self.assertEqual(items[1]["type"], "function_call")
        self.assertEqual(items[1]["name"], "shell_command")
        self.assertEqual(items[2]["type"], "custom_tool_call")
        self.assertEqual(items[2]["input"], "*** Begin Patch")
        self.assertEqual(items[3]["namespace"], "multi_agent_v1")
        self.assertEqual(items[3]["name"], "spawn_agent")
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(events[-1]["response"]["usage"]["total_tokens"], 15)

    def test_preserves_tool_history_for_next_chat_turn(self) -> None:
        request = self.request()
        request["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Run pwd"}],
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "shell_command",
                "arguments": '{"command":"pwd"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "/tmp/project",
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue"}],
            },
        ]

        chat, _bindings = responses_to_chat_request(request, self.model)

        roles = [message["role"] for message in chat["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(
            chat["messages"][2]["tool_calls"][0]["id"],
            "call-1",
        )
        self.assertEqual(chat["messages"][3]["tool_call_id"], "call-1")

    def test_six_agent_calls_are_emitted_in_one_response(self) -> None:
        _request, bindings = responses_to_chat_request(self.request(), self.model)
        agent_name = next(
            binding.encoded_name
            for binding in bindings.bindings
            if binding.qualified_name == "multi_agent_v1.spawn_agent"
        )
        calls = [
            {
                "id": f"agent-{index}",
                "type": "function",
                "function": {
                    "name": agent_name,
                    "arguments": json.dumps(
                        {
                            "model": self.model.gateway_id,
                            "message": f"task {index}",
                        }
                    ),
                },
            }
            for index in range(6)
        ]
        events = response_events(
            chat_response_to_sse(
                {"choices": [{"message": {"content": None, "tool_calls": calls}}]},
                bindings,
            )
        )
        agent_items = [
            event["item"]
            for event in events
            if event.get("item", {}).get("namespace") == "multi_agent_v1"
        ]
        self.assertEqual(len(agent_items), 6)
        self.assertTrue(all(item["name"] == "spawn_agent" for item in agent_items))

    def test_rejects_provider_hosted_web_search_for_pi(self) -> None:
        request = self.request()
        request["tools"] = [{"type": "web_search"}]
        with self.assertRaisesRegex(GatewayError, "web_search"):
            responses_to_chat_request(request, self.model)

    def test_rejects_image_for_text_only_model(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["models"][0]["input"] = ["text"]
        write_json(self.pi / "models.json", document)
        model = (
            CatalogLoader(
                self.pi / "models.json",
                self.repo / "codex-rs" / "models-manager" / "prompt.md",
            )
            .load()
            .models[0]
        )
        request = self.request()
        request["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64,AA=="}
                ],
            }
        ]
        with self.assertRaisesRegex(GatewayError, "not registered for image"):
            responses_to_chat_request(request, model)


if __name__ == "__main__":
    unittest.main()
