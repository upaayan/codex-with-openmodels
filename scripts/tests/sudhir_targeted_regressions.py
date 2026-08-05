#!/usr/bin/env python3
"""Run only the owner-approved Sudhir-Codex backend regressions."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_TESTS = (
    "test_catalog.CatalogTests.test_synthesized_model_is_picker_and_agent_visible",
    "test_cursor_catalog.CursorCatalogTests.test_model_metadata_is_picker_and_subagent_visible",
    "test_adapter.AdapterTests.test_translates_messages_tools_and_reasoning",
    "test_adapter.AdapterTests.test_round_trips_function_custom_and_namespaced_calls",
    "test_adapter.AdapterTests.test_emits_full_reasoning_as_responses_reasoning_item",
    "test_adapter.AdapterTests.test_reasoning_aliases_emit_one_canonical_item_without_duplicates",
    "test_adapter.AdapterTests.test_replays_reasoning_alias_for_generic_and_opencode_routes",
    "test_adapter.AdapterTests.test_replays_reasoning_item_as_reasoning_content_for_deepseek_routes",
    "test_adapter.AdapterTests.test_replays_reasoning_on_tool_call_message_once",
    "test_adapter.AdapterTests.test_maps_reasoning_token_usage_to_responses_details",
    "test_adapter.AdapterTests.test_preserves_tool_history_for_next_chat_turn",
    "test_adapter.AdapterTests.test_history_only_exec_command_is_serializable_but_not_callable",
    "test_anthropic.AnthropicAdapterTests.test_translates_tools_images_and_tool_history",
    "test_anthropic.AnthropicAdapterTests.test_round_trips_thinking_text_and_opaque_signature",
    "test_openai_responses.OpenAIResponsesTests.test_translates_codex_tools_and_preserves_responses_history",
    "test_openai_responses.OpenAIResponsesTests.test_decodes_custom_and_namespaced_tool_calls_for_codex",
    "test_openai_responses.OpenAIResponsesTests.test_exposes_tools_returned_by_codex_tool_search",
    "test_openai_responses.OpenAIResponsesTests.test_deepseek_adds_object_type_to_root_one_of_tool_schema",
    "test_openai_responses.OpenAIResponsesTests.test_history_only_exec_command_is_serializable_but_not_callable",
    "test_openai_responses.OpenAIResponsesTests.test_historical_discovered_tools_require_active_tool_search",
    "test_credentials.CredentialTests.test_arbitrary_oauth_provider_delegates_to_pi_without_allowlist",
    "test_app.GatewayAppTests.test_xai_grok45_uses_responses_transport",
)
RUST_TESTS = (
    "pre_sampling_compact_uses_selected_model_on_switch_to_smaller_context_model",
    "model_switch_with_changed_comp_hash_does_not_compact_without_pressure",
    "model_switch_to_selected_model_never_requests_previous_custom_model",
    "pre_sampling_compact_after_resume_uses_selected_smaller_model",
    "resumed_legacy_comp_hash_does_not_trigger_or_route_compaction",
    "plaintext_pi_multi_agent_v2_spawn_sends_agent_message_to_child",
    "hosted_web_search_and_standalone_image_generation_follow_runtime_gates",
)


def run_gateway() -> int:
    source_root = REPOSITORY_ROOT / "sudhir_codex" / "src"
    tests_root = REPOSITORY_ROOT / "sudhir_codex" / "tests"
    sys.path[:0] = [str(source_root), str(tests_root)]
    suite = unittest.defaultTestLoader.loadTestsFromNames(GATEWAY_TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_rust() -> int:
    expression = "test(/(" + "|".join(RUST_TESTS) + ")$/)"
    environment = os.environ.copy()
    result = subprocess.run(
        [
            "just",
            "test",
            "--retries",
            "0",
            "-p",
            "codex-core",
            "-E",
            expression,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one explicit Sudhir-Codex regression lane; no broad suite is available."
    )
    parser.add_argument("lane", choices=("gateway", "rust"))
    arguments = parser.parse_args()
    return run_gateway() if arguments.lane == "gateway" else run_rust()


if __name__ == "__main__":
    raise SystemExit(main())
