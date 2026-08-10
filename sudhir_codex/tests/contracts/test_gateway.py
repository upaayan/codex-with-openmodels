import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_server = legacy_module("test_server")


class GatewayContracts(unittest.TestCase):
    def test_authenticated_gateway_is_loopback_only(self) -> None:
        run_cases(
            self,
            (
                (_server.GatewayServerTests, "test_non_loopback_bind_is_rejected"),
                (
                    _server.GatewayServerTests,
                    "test_unauthorized_request_does_no_route_work",
                ),
                (_server.GatewayServerTests, "test_authorized_health_request"),
            ),
        )
