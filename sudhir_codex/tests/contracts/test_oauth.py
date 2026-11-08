import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_app = legacy_module("test_app")
_credentials = legacy_module("test_credentials")


class OAuthContracts(unittest.TestCase):
    def test_arbitrary_pi_oauth_provider_needs_no_gateway_allowlist(self) -> None:
        run_cases(
            self,
            (
                (
                    _credentials.CredentialTests,
                    "test_arbitrary_oauth_provider_delegates_to_pi_without_allowlist",
                ),
                (
                    _credentials.CredentialTests,
                    "test_oauth_header_deletions_survive_worker_and_merge",
                ),
                (
                    _credentials.CredentialTests,
                    "test_oauth_worker_rejects_invalid_auth_header_values",
                ),
            ),
        )

    def test_xai_route_uses_responses_without_hardcoding(self) -> None:
        run_cases(
            self,
            (
                (_app.GatewayAppTests, "test_xai_grok45_uses_responses_transport"),
                (_app.GatewayAppTests, "test_xai_grok43_uses_responses_transport"),
            ),
        )
