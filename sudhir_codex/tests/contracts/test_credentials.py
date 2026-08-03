import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_app = legacy_module("test_app")


class CredentialContracts(unittest.TestCase):
    def test_route_credentials_are_isolated(self) -> None:
        run_cases(
            self,
            (
                (_app.GatewayAppTests, "test_pi_request_receives_only_pi_credential"),
                (
                    _app.GatewayAppTests,
                    "test_gpt_request_keeps_chatgpt_auth_and_strips_gateway_secret",
                ),
                (
                    _app.GatewayAppTests,
                    "test_anthropic_pi_request_uses_messages_transport",
                ),
            ),
        )
