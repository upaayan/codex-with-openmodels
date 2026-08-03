import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_adapter = legacy_module("test_adapter")
_responses = legacy_module("test_openai_responses")


class HistoryToolContracts(unittest.TestCase):
    def test_history_exec_is_serializable_but_not_callable(self) -> None:
        run_cases(
            self,
            (
                (_adapter.AdapterTests, "test_preserves_tool_history_for_next_chat_turn"),
                (
                    _adapter.AdapterTests,
                    "test_history_only_exec_command_is_serializable_but_not_callable",
                ),
                (
                    _responses.OpenAIResponsesTests,
                    "test_translates_codex_tools_and_preserves_responses_history",
                ),
                (
                    _responses.OpenAIResponsesTests,
                    "test_history_only_exec_command_is_serializable_but_not_callable",
                ),
                (
                    _responses.OpenAIResponsesTests,
                    "test_historical_discovered_tools_require_active_tool_search",
                ),
            ),
        )
