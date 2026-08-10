import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_adapter = legacy_module("test_adapter")
_anthropic = legacy_module("test_anthropic")
_catalog = legacy_module("test_catalog")
_responses = legacy_module("test_openai_responses")
_app = legacy_module("test_app")


class ReasoningContracts(unittest.TestCase):
    def test_route_aware_reasoning_controls(self) -> None:
        run_cases(
            self,
            (
                (
                    _catalog.CatalogTests,
                    "test_known_routes_advertise_only_real_reasoning_controls",
                ),
                (_adapter.AdapterTests, "test_translates_messages_tools_and_reasoning"),
                (_adapter.AdapterTests, "test_direct_deepseek_translates_none_high_and_max"),
                (
                    _adapter.AdapterTests,
                    "test_open_code_qwen_translates_messages_reasoning_budgets",
                ),
            ),
        )

    def test_deepseek_and_opencode_atomic_reasoning_text_tool_replay(self) -> None:
        run_cases(
            self,
            (
                (
                    _responses.OpenAIResponsesTests,
                    "test_deepseek_adds_object_type_to_root_one_of_tool_schema",
                ),
                (
                    _adapter.AdapterTests,
                    "test_round_trips_function_custom_and_namespaced_calls",
                ),
                (
                    _adapter.AdapterTests,
                    "test_emits_full_reasoning_as_responses_reasoning_item",
                ),
                (
                    _adapter.AdapterTests,
                    "test_replays_reasoning_alias_for_generic_and_opencode_routes",
                ),
                (
                    _adapter.AdapterTests,
                    "test_replays_reasoning_item_as_reasoning_content_for_deepseek_routes",
                ),
                (
                    _adapter.AdapterTests,
                    "test_replays_reasoning_on_tool_call_message_once",
                ),
            ),
        )

    def test_reasoning_token_accounting_survives_translation(self) -> None:
        run_cases(
            self,
            (
                (
                    _adapter.AdapterTests,
                    "test_maps_reasoning_token_usage_to_responses_details",
                ),
            ),
        )

    def test_encrypted_reasoning_is_route_scoped(self) -> None:
        run_cases(
            self,
            (
                (
                    _responses.OpenAIResponsesTests,
                    "test_translates_codex_tools_and_preserves_responses_history",
                ),
                (
                    _responses.OpenAIResponsesTests,
                    "test_decodes_custom_and_namespaced_tool_calls_for_codex",
                ),
                (
                    _anthropic.AnthropicAdapterTests,
                    "test_round_trips_thinking_text_and_opaque_signature",
                ),
            ),
        )

    def test_gpt_omits_foreign_plaintext_reasoning_on_return_switch(self) -> None:
        run_cases(
            self,
            (
                (
                    _app.GatewayAppTests,
                    "test_gpt_request_omits_foreign_plaintext_reasoning",
                ),
            ),
        )
