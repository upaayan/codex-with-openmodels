import base64
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

    def test_native_google_signature_carrier_survives_tool_loop_translation(
        self,
    ) -> None:
        document = {
            "providers": {
                "google": {
                    "api": "google-generative-ai",
                    "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
                    "models": [{"id": "gemini-3.7-flash", "reasoning": True}],
                }
            }
        }
        write_json(self.pi / "models.json", document)
        model = (
            CatalogLoader(
                self.pi / "models.json",
                self.repo / "codex-rs" / "models-manager" / "prompt.md",
            )
            .load()
            .models[0]
        )
        envelope = {
            "v": 1,
            "api": model.api,
            "provider": model.provider_id,
            "model": model.upstream_id,
            "parts": [],
        }
        carrier = "sudhir-google-signature-v1." + base64.urlsafe_b64encode(
            json.dumps(envelope, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        first_request = self.request()
        first_request["model"] = model.gateway_id
        _chat, bindings = responses_to_chat_request(first_request, model)
        sse = chat_response_to_sse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_signature": carrier,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": '{"command":"pwd"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            bindings,
        )
        output = [
            event["item"]
            for event in response_events(sse)
            if event.get("type") == "response.output_item.done"
        ]
        self.assertEqual(output[0]["encrypted_content"], carrier)
        self.assertEqual(output[0]["content"], [])

        follow_up = self.request()
        follow_up["model"] = model.gateway_id
        follow_up["input"] = [
            *output,
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "ok",
            },
        ]
        chat, _bindings = responses_to_chat_request(follow_up, model)

        self.assertEqual(chat["messages"][1]["reasoning_signature"], carrier)

        switched, _bindings = responses_to_chat_request(follow_up, self.model)
        self.assertNotIn("reasoning_signature", switched["messages"][1])

    def test_chat_loads_only_eight_latest_discovered_tools(self) -> None:
        request = self.request()
        request["tools"] = [
            request["tools"][0],
            {
                "type": "tool_search",
                "description": "Search deferred tools",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "namespace",
                "name": "mixed",
                "description": "Mixed direct and deferred tools",
                "tools": [
                    {
                        "type": "function",
                        "name": "direct_child",
                        "description": "Required directly",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    {
                        "type": "function",
                        "name": "deferred_child",
                        "description": "Load on demand",
                        "defer_loading": True,
                        "parameters": {"type": "object", "properties": {}},
                    },
                ],
            },
            *[
                {
                    "type": "function",
                    "name": f"deferred_{index}",
                    "description": "Deferred inventory",
                    "defer_loading": True,
                    "parameters": {"type": "object", "properties": {}},
                }
                for index in range(20)
            ],
        ]
        discovered = {
            "type": "namespace",
            "name": "discovered",
            "description": "On-demand namespace",
            "tools": [
                {
                    "type": "function",
                    "name": f"leaf_{index}",
                    "description": "On demand",
                    "parameters": {"type": "object", "properties": {}},
                }
                for index in range(12)
            ],
        }
        request["input"].extend(
            [
                {
                    "type": "tool_search_call",
                    "id": "fc-search-old",
                    "call_id": "call-search-old",
                    "status": "completed",
                    "execution": "client",
                    "arguments": {"query": "old tools"},
                },
                {
                    "type": "tool_search_output",
                    "call_id": "call-search-old",
                    "tools": [
                        {
                            "type": "function",
                            "name": "old_discovery",
                            "description": "Historical result",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                },
                {
                    "type": "tool_search_call",
                    "id": "fc-search-latest",
                    "call_id": "call-search-latest",
                    "status": "completed",
                    "execution": "client",
                    "arguments": {"query": "latest tools"},
                },
                {
                    "type": "tool_search_output",
                    "call_id": "call-search-latest",
                    "tools": [discovered],
                },
            ]
        )

        chat, bindings = responses_to_chat_request(request, self.model)

        self.assertEqual(
            [binding.qualified_name for binding in bindings.bindings],
            [
                "shell_command",
                "tool_search",
                "mixed.direct_child",
                *[f"discovered.leaf_{index}" for index in range(8)],
            ],
        )
        self.assertEqual(len(chat["tools"]), 11)
        search_messages = [
            message
            for message in chat["messages"]
            if message.get("role") == "tool"
            and str(message.get("tool_call_id", "")).startswith("call-search")
        ]
        self.assertEqual(json.loads(search_messages[0]["content"]), [])
        visible = json.loads(search_messages[1]["content"])
        self.assertEqual(
            [child["name"] for child in visible[0]["tools"]],
            [f"leaf_{index}" for index in range(8)],
        )

    def test_chat_style_message_items_without_type_are_accepted(self) -> None:
        # pi-ai (dsh) emits chat-style items as {role, content} with no
        # Responses "type", and may carry content as a plain string (e.g. the
        # system prompt). Ensure _translate_input treats those as messages
        # instead of raising unsupported_input_item / invalid_content.
        request = self.request()
        request["instructions"] = None
        request["input"] = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        chat, _bindings = responses_to_chat_request(request, self.model)

        messages = chat["messages"]
        system_messages = [m for m in messages if m["role"] == "system"]
        self.assertEqual(system_messages[0]["content"], "You are helpful.")
        user_messages = [m for m in messages if m["role"] == "user"]
        self.assertEqual(user_messages[0]["content"], "hi")
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistant_messages[0]["content"], "hello")

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

    def test_emits_full_reasoning_as_responses_reasoning_item(self) -> None:
        _request, bindings = responses_to_chat_request(self.request(), self.model)
        reasoning = "Inspect the repository, then verify the smallest safe change."

        events = response_events(
            chat_response_to_sse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "reasoning_content": reasoning,
                                "content": "I will inspect the repository.",
                            }
                        }
                    ]
                },
                bindings,
            )
        )
        items = [
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
        ]

        self.assertEqual([item["type"] for item in items], ["reasoning", "message"])
        reasoning_item = dict(items[0])
        reasoning_id = reasoning_item.pop("id")
        self.assertIsInstance(reasoning_id, str)
        self.assertTrue(reasoning_id.startswith("rs_sudhir_"))
        self.assertEqual(
            reasoning_item,
            {
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": reasoning}],
                "encrypted_content": None,
            },
        )

    def test_reasoning_aliases_emit_one_canonical_item_without_duplicates(self) -> None:
        _request, bindings = responses_to_chat_request(self.request(), self.model)
        cases = (
            ({"reasoning": "reasoning alias"}, "reasoning alias"),
            ({"reasoning_text": "reasoning-text alias"}, "reasoning-text alias"),
            (
                {
                    "reasoning_content": "",
                    "reasoning": "first nonempty reasoning",
                    "reasoning_text": "later reasoning text",
                },
                "first nonempty reasoning",
            ),
            (
                {
                    "reasoning_content": "preferred reasoning",
                    "reasoning": "duplicate reasoning",
                    "reasoning_text": "duplicate reasoning text",
                },
                "preferred reasoning",
            ),
        )

        for aliases, expected in cases:
            with self.subTest(aliases=aliases):
                events = response_events(
                    chat_response_to_sse(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "Done.",
                                        **aliases,
                                    }
                                }
                            ]
                        },
                        bindings,
                    )
                )
                reasoning_items = [
                    event["item"]
                    for event in events
                    if event.get("item", {}).get("type") == "reasoning"
                ]

                self.assertEqual(len(reasoning_items), 1)
                self.assertEqual(
                    reasoning_items[0]["content"],
                    [{"type": "reasoning_text", "text": expected}],
                )

    def test_replays_reasoning_alias_for_generic_and_opencode_routes(self) -> None:
        cases = (
            ("demo", "demo/model", "reasoning", "reasoning", None),
            ("demo", "demo/model", "reasoning_text", "reasoning_text", None),
            (
                "opencode-go",
                "deepseek-v4-flash",
                "reasoning",
                "reasoning_content",
                None,
            ),
        )

        for provider_id, model_id, source_field, replay_field, compat in cases:
            with self.subTest(
                provider_id=provider_id,
                source_field=source_field,
            ):
                model = self.load_model(provider_id, model_id, compat=compat)
                request = self.request()
                _chat, bindings = responses_to_chat_request(request, model)
                shell_name = next(
                    binding.encoded_name
                    for binding in bindings.bindings
                    if binding.qualified_name == "shell_command"
                )
                reasoning = f"reasoning from {source_field}"
                events = response_events(
                    chat_response_to_sse(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "Done.",
                                        source_field: reasoning,
                                        "tool_calls": [
                                            {
                                                "id": "call-1",
                                                "type": "function",
                                                "function": {
                                                    "name": shell_name,
                                                    "arguments": '{"command":"pwd"}',
                                                },
                                            }
                                        ],
                                    }
                                }
                            ]
                        },
                        bindings,
                    )
                )
                output_items = [
                    event["item"]
                    for event in events
                    if event["type"] == "response.output_item.done"
                ]
                reasoning_item = next(
                    item for item in output_items if item["type"] == "reasoning"
                )
                self.assertIsNone(reasoning_item["encrypted_content"])
                request["input"] = [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Work"}],
                    },
                    *output_items,
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "/tmp/project",
                    },
                ]

                replayed, _bindings = responses_to_chat_request(request, model)
                assistants = [
                    message
                    for message in replayed["messages"]
                    if message["role"] == "assistant"
                ]
                self.assertEqual(len(assistants), 1)
                assistant = assistants[0]

                self.assertEqual(assistant["content"], "Done.")
                self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
                self.assertEqual(assistant[replay_field], reasoning)
                if replay_field != source_field:
                    self.assertNotIn(source_field, assistant)

    def test_replays_reasoning_item_as_reasoning_content_for_deepseek_routes(
        self,
    ) -> None:
        reasoning = "The table exists, so inspect its schema before continuing."
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Create the table"}],
            },
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": reasoning}],
                "encrypted_content": None,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "I will inspect the table schema.",
                    }
                ],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue"}],
            },
        ]
        routes = (
            (
                "deepseek",
                {
                    "requiresReasoningContentOnAssistantMessages": True,
                    "thinkingFormat": "deepseek",
                },
            ),
            ("opencode-go", None),
        )

        for provider_id, compat in routes:
            with self.subTest(provider_id=provider_id):
                model = self.load_model(
                    provider_id,
                    "deepseek-v4-flash",
                    compat=compat,
                )
                request = self.request()
                request["input"] = input_items

                chat, _bindings = responses_to_chat_request(request, model)
                assistant_messages = [
                    message
                    for message in chat["messages"]
                    if message["role"] == "assistant"
                ]

                self.assertEqual(len(assistant_messages), 1)
                self.assertEqual(
                    assistant_messages[0]["reasoning_content"],
                    reasoning,
                )

    def test_replays_reasoning_on_tool_call_message_once(self) -> None:
        model = self.load_model(
            "deepseek",
            "deepseek-v4-flash",
            compat={
                "requiresReasoningContentOnAssistantMessages": True,
                "thinkingFormat": "deepseek",
            },
        )
        request = self.request()
        reasoning = "The table exists, so inspect its schema before continuing."
        _chat, bindings = responses_to_chat_request(request, model)
        shell_name = next(
            binding.encoded_name
            for binding in bindings.bindings
            if binding.qualified_name == "shell_command"
        )
        events = response_events(
            chat_response_to_sse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "reasoning_content": reasoning,
                                "content": "I will inspect the table schema.",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": shell_name,
                                            "arguments": (
                                                '{"command":"aws dynamodb '
                                                'describe-table"}'
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                bindings,
            )
        )
        output_items = [
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
        ]
        self.assertEqual(
            [item["type"] for item in output_items],
            ["reasoning", "message", "function_call"],
        )
        request["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Create the table"}],
            },
            *output_items,
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"TableStatus":"ACTIVE"}',
            },
        ]

        chat, _bindings = responses_to_chat_request(request, model)
        assistant_messages = [
            message
            for message in chat["messages"]
            if message["role"] == "assistant"
        ]
        self.assertEqual(len(assistant_messages), 1)
        tool_call_message = next(
            message for message in assistant_messages if message.get("tool_calls")
        )

        self.assertEqual(
            tool_call_message["content"],
            "I will inspect the table schema.",
        )
        self.assertEqual(tool_call_message["reasoning_content"], reasoning)
        self.assertEqual(
            sum(
                message.get("reasoning_content") == reasoning
                for message in assistant_messages
            ),
            1,
        )

    def test_maps_reasoning_token_usage_to_responses_details(self) -> None:
        _request, bindings = responses_to_chat_request(self.request(), self.model)

        events = response_events(
            chat_response_to_sse(
                {
                    "choices": [{"message": {"content": "Done."}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 17,
                        "completion_tokens_details": {"reasoning_tokens": 13},
                        "total_tokens": 27,
                    },
                },
                bindings,
            )
        )
        usage = events[-1]["response"]["usage"]

        self.assertEqual(usage["output_tokens"], 17)
        self.assertEqual(
            usage["output_tokens_details"],
            {"reasoning_tokens": 13},
        )

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

    def test_history_only_exec_command_is_serializable_but_not_callable(
        self,
    ) -> None:
        request = self.request()
        request["tools"] = []
        request["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Run pwd"}],
            },
            {
                "type": "function_call",
                "call_id": "call-history",
                "name": "exec_command",
                "arguments": '{"cmd":"pwd"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-history",
                "output": "/tmp/project",
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue"}],
            },
        ]

        chat, bindings = responses_to_chat_request(request, self.model)

        self.assertNotIn("tools", chat)
        self.assertNotIn("tool_choice", chat)
        self.assertEqual(
            [message["role"] for message in chat["messages"]],
            ["system", "user", "assistant", "tool", "user"],
        )
        history_call = chat["messages"][2]["tool_calls"][0]
        self.assertEqual(history_call["id"], "call-history")
        self.assertEqual(chat["messages"][3]["tool_call_id"], "call-history")

        with self.assertRaisesRegex(GatewayError, "unknown translated tool"):
            chat_response_to_sse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-new",
                                        "type": "function",
                                        "function": {
                                            "name": history_call["function"]["name"],
                                            "arguments": '{"cmd":"whoami"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                bindings,
            )

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
