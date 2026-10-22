import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import basic_pi_document
from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.credentials import CredentialResolver
from sudhir_codex_gateway.errors import GatewayError


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_repo(self.root / "repo")
        self.pi_dir = self.root / "pi"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def model(self, document: dict[str, object] | None = None):
        write_json(self.pi_dir / "models.json", document or basic_pi_document())
        return (
            CatalogLoader(
                self.pi_dir / "models.json",
                self.repo / "codex-rs" / "models-manager" / "prompt.md",
            )
            .load()
            .models[0]
        )

    def test_auth_file_api_key_is_used(self) -> None:
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "api_key", "key": "pi-secret"}},
        )

        headers = CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
            self.model()
        )

        self.assertEqual(headers, {"Authorization": "Bearer pi-secret"})

    def test_arbitrary_oauth_provider_delegates_to_pi_without_allowlist(
        self,
    ) -> None:
        try:
            from sudhir_codex_gateway.pi_auth_worker import PiAuthResult
        except ImportError as exc:
            self.fail(f"generic Pi auth worker is missing: {exc}")

        model = self.model(
            {
                "providers": {
                    "future-oauth": {
                        "baseUrl": "https://configured.example/v1",
                        "api": "openai-responses",
                        "models": [{"id": "future-model", "reasoning": True}],
                    }
                }
            }
        )
        write_json(
            self.pi_dir / "auth.json",
            {
                "future-oauth": {
                    "type": "oauth",
                    "access": "stored-only-for-pi",
                }
            },
        )

        class FakePiAuthWorker:
            def __init__(self) -> None:
                self.models = []

            def resolve(self, selected_model):
                self.models.append(selected_model)
                return PiAuthResult(
                    provider_id="future-oauth",
                    model_id="future-model",
                    api="openai-responses",
                    api_key="pi-derived-access",
                    headers={"X-Provider-Auth": "extension-value"},
                    base_url="https://auth-selected.example/v1",
                )

            def close(self) -> None:
                return None

        worker = FakePiAuthWorker()
        try:
            resolver = CredentialResolver(
                self.pi_dir / "auth.json",
                oauth_worker=worker,
            )
        except TypeError as exc:
            self.fail(f"CredentialResolver cannot delegate OAuth generically: {exc}")

        resolved = resolver.resolve(model)

        self.assertEqual(worker.models, [model])
        self.assertEqual(
            resolved.headers,
            {
                "Authorization": "Bearer pi-derived-access",
                "X-Provider-Auth": "extension-value",
            },
        )
        self.assertEqual(resolved.base_url, "https://auth-selected.example/v1")

    def test_anthropic_route_uses_messages_headers(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["models"][0]["api"] = "anthropic-messages"
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "api_key", "key": "pi-secret"}},
        )

        headers = CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
            self.model(document)
        )

        self.assertEqual(
            headers,
            {
                "x-api-key": "pi-secret",
                "anthropic-version": "2023-06-01",
            },
        )

    def test_environment_expression_precedes_auth_file(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["apiKey"] = "$DEMO_TEST_KEY"
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "api_key", "key": "auth-secret"}},
        )
        with patch.dict(os.environ, {"DEMO_TEST_KEY": "env-secret"}):
            headers = CredentialResolver(
                self.pi_dir / "auth.json"
            ).authorization_headers(self.model(document))
        self.assertEqual(headers["Authorization"], "Bearer env-secret")

    def test_empty_environment_expression_falls_back_to_auth_file(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["apiKey"] = "$DEMO_TEST_KEY"
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "api_key", "key": "auth-secret"}},
        )
        with patch.dict(os.environ, {"DEMO_TEST_KEY": ""}):
            headers = CredentialResolver(
                self.pi_dir / "auth.json"
            ).authorization_headers(self.model(document))
        self.assertEqual(headers["Authorization"], "Bearer auth-secret")

    def test_command_expression_is_resolved_without_logging_output(self) -> None:
        document = basic_pi_document()
        command = (
            "!Write-Output command-secret"
            if os.name == "nt"
            else "!printf command-secret"
        )
        document["providers"]["demo"]["apiKey"] = command

        headers = CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
            self.model(document)
        )

        self.assertEqual(headers["Authorization"], "Bearer command-secret")

    @unittest.skipUnless(os.name == "nt", "native Windows PowerShell test")
    def test_windows_command_expression_runs_real_success_and_failure(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["apiKey"] = "!Write-Output native-secret"
        headers = CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
            self.model(document)
        )
        self.assertEqual(headers["Authorization"], "Bearer native-secret")

        document["providers"]["demo"]["apiKey"] = "!exit 9"
        with self.assertRaisesRegex(GatewayError, "failed for Pi provider"):
            CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
                self.model(document)
            )

    def test_windows_command_expression_uses_noninteractive_powershell(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["apiKey"] = "!Write-Output command-secret"
        with (
            patch(
                "sudhir_codex_gateway.credentials.is_windows",
                return_value=True,
            ),
            patch("sudhir_codex_gateway.credentials.subprocess.run") as run_command,
        ):
            run_command.return_value.returncode = 0
            run_command.return_value.stdout = "command-secret\r\n"

            headers = CredentialResolver(
                self.pi_dir / "auth.json"
            ).authorization_headers(self.model(document))

        self.assertEqual(headers["Authorization"], "Bearer command-secret")
        self.assertEqual(
            run_command.call_args.args[0],
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Write-Output command-secret",
            ],
        )
        self.assertEqual(run_command.call_args.kwargs["timeout"], 5.0)

    def test_windows_command_expression_maps_nonzero_status(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["apiKey"] = "!exit 9"
        with (
            patch(
                "sudhir_codex_gateway.credentials.is_windows",
                return_value=True,
            ),
            patch("sudhir_codex_gateway.credentials.subprocess.run") as run_command,
        ):
            run_command.return_value.returncode = 9
            run_command.return_value.stdout = ""

            with self.assertRaisesRegex(GatewayError, "failed for Pi provider"):
                CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
                    self.model(document)
                )

    def test_loopback_endpoint_may_omit_auth(self) -> None:
        model = self.model(basic_pi_document("http://127.0.0.1:18081/v1"))
        headers = CredentialResolver(
            self.pi_dir / "missing-auth.json"
        ).authorization_headers(model)
        self.assertEqual(headers, {})

    def test_remote_endpoint_without_auth_fails_before_network(self) -> None:
        with self.assertRaisesRegex(GatewayError, "No credential"):
            CredentialResolver(self.pi_dir / "missing-auth.json").authorization_headers(
                self.model()
            )

    def test_unknown_auth_type_is_rejected(self) -> None:
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "unknown", "access": "not-supported"}},
        )
        with self.assertRaisesRegex(GatewayError, "unsupported auth type"):
            CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
                self.model()
            )


if __name__ == "__main__":
    unittest.main()
