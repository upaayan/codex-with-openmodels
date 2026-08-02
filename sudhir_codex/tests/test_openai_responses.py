import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.errors import GatewayError
from sudhir_codex_gateway.openai_responses import openai_response_to_sse
from sudhir_codex_gateway.openai_responses import responses_to_openai_request


def response_events(payload: bytes) -> list[dict[str, object]]:
    events = []
    for line in payload.decode().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line.removeprefix("data: ")))
    return events


class OpenAIResponsesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_repo(self.root / "repo")
        self.pi = self.root / "pi"
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
                            }
                        ]
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

    def request(self) -> dict[str, object]:
        return {
            "model": self.model.gateway_id,
            "instructions": "You are a coding agent.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect the repo"}],
                },
                {
                    "type": "reasoning",
                    "id": "rs_previous",
                    "status": "completed",
                    "summary": [],
                    "encrypted_content": "opaque-previous-reasoning",
                },
                {
                    "type": "function_call",
                    "id": "fc_previous",
                    "call_id": "call-previous",
                    "name": "shell_command",
                    "arguments": '{"command":"pwd"}',
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-previous",
                    "output": "/tmp/project",
                },
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
                    "name": "sudhir_agents",
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
            "reasoning": {"effort": "high"},
            "stream": True,
        }

    def test_translates_codex_tools_and_preserves_responses_history(self) -> None:
        upstream, bindings = responses_to_openai_request(self.request(), self.model)
        names = {
            binding.qualified_name: binding.encoded_name
            for binding in bindings.bindings
        }

        self.assertEqual(
            [tool["name"] for tool in upstream.get("tools", [])],
            [
                names["shell_command"],
                names["apply_patch"],
                names["sudhir_agents.spawn_agent"],
            ],
        )
        self.assertTrue(
            all(tool["type"] == "function" for tool in upstream.get("tools", []))
        )
        self.assertTrue(
            all(tool["strict"] is False for tool in upstream.get("tools", []))
        )
        self.assertEqual(upstream["tool_choice"], "auto")
        self.assertEqual(upstream["parallel_tool_calls"], True)
        self.assertEqual(upstream["input"][0]["role"], "system")
        self.assertEqual(
            upstream["input"][2]["encrypted_content"],
            "opaque-previous-reasoning",
        )
        self.assertEqual(upstream["input"][3]["name"], names["shell_command"])
        self.assertEqual(upstream["input"][3]["id"], "fc_previous")
        self.assertEqual(
            upstream["input"][4],
            {
                "type": "function_call_output",
                "call_id": "call-previous",
                "output": "/tmp/project",
            },
        )

    def test_decodes_custom_and_namespaced_tool_calls_for_codex(self) -> None:
        _upstream, bindings = responses_to_openai_request(self.request(), self.model)
        names = {
            binding.qualified_name: binding.encoded_name
            for binding in bindings.bindings
        }
        response = {
            "id": "resp_xai",
            "status": "completed",
            "model": "grok-4.5",
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs_xai",
                    "status": "completed",
                    "summary": [],
                    "encrypted_content": "opaque-new-reasoning",
                },
                {
                    "type": "function_call",
                    "id": "fc_patch",
                    "call_id": "call-patch",
                    "name": names["apply_patch"],
                    "arguments": '{"input":"*** Begin Patch"}',
                    "status": "completed",
                },
                {
                    "type": "function_call",
                    "id": "fc_agent",
                    "call_id": "call-agent",
                    "name": names["sudhir_agents.spawn_agent"],
                    "arguments": '{"model":"pi-xai/grok-4.5","message":"review"}',
                    "status": "completed",
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        }

        events = response_events(openai_response_to_sse(response, bindings))
        items = [
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
        ]

        self.assertEqual(items[0]["encrypted_content"], "opaque-new-reasoning")
        self.assertEqual(items[1]["type"], "custom_tool_call")
        self.assertEqual(items[1]["id"], "fc_patch")
        self.assertEqual(items[1]["name"], "apply_patch")
        self.assertEqual(items[1]["input"], "*** Begin Patch")
        self.assertEqual(items[2]["type"], "function_call")
        self.assertEqual(items[2]["id"], "fc_agent")
        self.assertEqual(items[2]["namespace"], "sudhir_agents")
        self.assertEqual(items[2]["name"], "spawn_agent")
        self.assertEqual(
            events[-1]["response"]["output"],
            items,
        )

    def test_exposes_tools_returned_by_codex_tool_search(self) -> None:
        request = self.request()
        request["tools"] = [
            request["tools"][0],
            {
                "type": "tool_search",
                "execution": "client",
                "description": "Search deferred tools.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        ]
        request["input"].extend(
            [
                {
                    "type": "tool_search_call",
                    "id": "fc_search",
                    "call_id": "call-search",
                    "status": "completed",
                    "execution": "client",
                    "arguments": {"query": "spawn agent"},
                },
                {
                    "type": "tool_search_output",
                    "call_id": "call-search",
                    "status": "completed",
                    "execution": "client",
                    "tools": [self.request()["tools"][2]],
                },
            ]
        )

        upstream, bindings = responses_to_openai_request(request, self.model)

        names = {
            binding.qualified_name: binding.encoded_name
            for binding in bindings.bindings
        }
        self.assertEqual(
            set(names),
            {"shell_command", "tool_search", "sudhir_agents.spawn_agent"},
        )
        self.assertEqual(
            [tool["name"] for tool in upstream["tools"]],
            [
                names["shell_command"],
                names["tool_search"],
                names["sudhir_agents.spawn_agent"],
            ],
        )
        self.assertIsInstance(upstream["input"][-1]["output"], str)

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
                "id": "fc_history",
                "call_id": "call-history",
                "name": "exec_command",
                "arguments": '{"cmd":"pwd"}',
                "status": "completed",
            },
            {
                "type": "function_call_output",
                "call_id": "call-history",
                "output": "/tmp/project",
            },
        ]

        upstream, bindings = responses_to_openai_request(request, self.model)

        self.assertNotIn("tools", upstream)
        self.assertNotIn("tool_choice", upstream)
        history_call = next(
            item for item in upstream["input"] if item.get("type") == "function_call"
        )
        self.assertEqual(history_call["call_id"], "call-history")
        self.assertEqual(
            upstream["input"][-1],
            {
                "type": "function_call_output",
                "call_id": "call-history",
                "output": "/tmp/project",
            },
        )

        with self.assertRaisesRegex(GatewayError, "unknown translated tool"):
            openai_response_to_sse(
                {
                    "id": "resp_history_only",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_new",
                            "call_id": "call-new",
                            "name": history_call["name"],
                            "arguments": '{"cmd":"whoami"}',
                            "status": "completed",
                        }
                    ],
                },
                bindings,
            )

    def test_historical_discovered_tools_require_active_tool_search(self) -> None:
        request = self.request()
        request["tools"] = []
        request["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Search tools"}],
            },
            {
                "type": "tool_search_call",
                "id": "fc_search_history",
                "call_id": "call-search-history",
                "status": "completed",
                "execution": "client",
                "arguments": {"query": "spawn agent"},
            },
            {
                "type": "tool_search_output",
                "call_id": "call-search-history",
                "status": "completed",
                "execution": "client",
                "tools": [self.request()["tools"][2]],
            },
        ]

        upstream, _bindings = responses_to_openai_request(request, self.model)

        self.assertNotIn("tools", upstream)
        self.assertNotIn("tool_choice", upstream)


if __name__ == "__main__":
    unittest.main()
