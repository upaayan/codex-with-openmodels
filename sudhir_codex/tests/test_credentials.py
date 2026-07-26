import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

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

    def xai_model(self):
        return self.model(
            {
                "providers": {
                    "xai": {
                        "models": [
                            {
                                "id": "grok-4.5",
                                "reasoning": True,
                            }
                        ]
                    }
                }
            }
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

    def test_current_xai_oauth_access_token_is_used(self) -> None:
        write_json(
            self.pi_dir / "auth.json",
            {
                "xai": {
                    "type": "oauth",
                    "access": "xai-access",
                    "refresh": "xai-refresh",
                    "expires": 9_999_999_999_999,
                }
            },
        )

        with patch("httpx.post") as post:
            try:
                headers = CredentialResolver(
                    self.pi_dir / "auth.json"
                ).authorization_headers(self.xai_model())
            except GatewayError as exc:
                self.fail(f"xAI OAuth access token was rejected: {exc}")

        self.assertEqual(headers, {"Authorization": "Bearer xai-access"})
        post.assert_not_called()

    def test_expired_xai_oauth_token_is_refreshed_and_saved_atomically(self) -> None:
        auth_path = self.pi_dir / "auth.json"
        write_json(
            auth_path,
            {
                "xai": {
                    "type": "oauth",
                    "access": "expired-access",
                    "refresh": "existing-refresh",
                    "expires": 1,
                },
                "other": {"type": "api_key", "key": "other-secret"},
            },
        )
        refresh_response = httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "expires_in": 7200,
            },
        )
        real_replace = os.replace

        with (
            patch(
                "time.time",
                return_value=2_000_000_000,
            ),
            patch(
                "httpx.post",
                return_value=refresh_response,
            ) as post,
            patch(
                "sudhir_codex_gateway.credentials.os.replace",
                wraps=real_replace,
            ) as replace,
        ):
            try:
                headers = CredentialResolver(auth_path).authorization_headers(
                    self.xai_model()
                )
            except GatewayError as exc:
                self.fail(f"xAI OAuth refresh was rejected: {exc}")

        self.assertEqual(headers, {"Authorization": "Bearer fresh-access"})
        self.assertEqual(
            json.loads(auth_path.read_text(encoding="utf-8")),
            {
                "xai": {
                    "type": "oauth",
                    "access": "fresh-access",
                    "refresh": "existing-refresh",
                    "expires": 2_000_006_900_000,
                },
                "other": {"type": "api_key", "key": "other-secret"},
            },
        )
        replace.assert_called_once()
        if os.name != "nt":
            self.assertEqual(auth_path.stat().st_mode & 0o777, 0o600)
        post.assert_called_once()
        self.assertEqual(
            post.call_args.kwargs["data"],
            {
                "grant_type": "refresh_token",
                "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
                "refresh_token": "existing-refresh",
            },
        )

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
                CredentialResolver(
                    self.pi_dir / "auth.json"
                ).authorization_headers(self.model(document))

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

    def test_non_api_key_auth_is_rejected(self) -> None:
        write_json(
            self.pi_dir / "auth.json",
            {"demo": {"type": "oauth", "access": "not-supported"}},
        )
        with self.assertRaisesRegex(GatewayError, "unsupported auth type"):
            CredentialResolver(self.pi_dir / "auth.json").authorization_headers(
                self.model()
            )


if __name__ == "__main__":
    unittest.main()
