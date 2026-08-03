import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_adapter = legacy_module("test_adapter")


class AgentContracts(unittest.TestCase):
    def test_six_agent_calls_are_preserved(self) -> None:
        run_cases(
            self,
            ((_adapter.AdapterTests, "test_six_agent_calls_are_emitted_in_one_response"),),
        )

    def test_gateway_rejects_provider_hosted_tools_for_pi(self) -> None:
        run_cases(
            self,
            (
                (_adapter.AdapterTests, "test_rejects_provider_hosted_web_search_for_pi"),
                (_adapter.AdapterTests, "test_rejects_image_for_text_only_model"),
            ),
        )
