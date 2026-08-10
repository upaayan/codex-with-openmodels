import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_app = legacy_module("test_app")
_server = legacy_module("test_server")
_state_launcher = legacy_module("test_state_launcher")


class WebSearchContracts(unittest.TestCase):
    def test_pi_standalone_web_search_is_configured_and_proxied(self) -> None:
        run_cases(
            self,
            (
                (
                    _state_launcher.StateAndLauncherTests,
                    "test_private_state_is_independent_and_mode_restricted",
                ),
                (
                    _state_launcher.StateAndLauncherTests,
                    "test_forced_config_pins_gateway_and_telemetry_off",
                ),
                (
                    _server.GatewayServerTests,
                    "test_authorized_standalone_search_route",
                ),
                (
                    _app.GatewayAppTests,
                    "test_search_proxies_pi_model_through_chatgpt_auth",
                ),
            ),
        )
