import unittest

from contracts._legacy import legacy_module
from contracts._legacy import run_cases

_state_launcher = legacy_module("test_state_launcher")


class StateContracts(unittest.TestCase):
    def test_private_state_and_auth_are_isolated_regular_files(self) -> None:
        run_cases(
            self,
            (
                (
                    _state_launcher.StateAndLauncherTests,
                    "test_private_state_is_independent_and_mode_restricted",
                ),
                (
                    _state_launcher.StateAndLauncherTests,
                    "test_auth_import_copies_then_backs_up_without_linking",
                ),
                (
                    _state_launcher.StateAndLauncherTests,
                    "test_official_or_symlinked_state_is_rejected",
                ),
            ),
        )
