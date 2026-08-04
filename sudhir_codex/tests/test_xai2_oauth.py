import json
import tempfile
import unittest
from pathlib import Path

from sudhir_codex_gateway.catalog import OpenModel
from sudhir_codex_gateway.credentials import CredentialResolver


class Xai2OAuthTests(unittest.TestCase):
    def test_delegates_to_pi_without_xai_hardcoding(self) -> None:
        try:
            from sudhir_codex_gateway.pi_auth_worker import PiAuthResult
        except ImportError as exc:
            self.fail(f"generic Pi auth worker is missing: {exc}")

        model = OpenModel(
            gateway_id="pi-xai2/grok-4.5",
            provider_id="xai2",
            upstream_id="grok-4.5",
            display_name="Grok 4.5",
            base_url="https://configured.example/v1",
            api="openai-responses",
            api_key_expression=None,
            compat={},
            reasoning=True,
            input_modalities=("text",),
            context_window=128_000,
            max_tokens=32_000,
            raw={},
        )

        class FakePiAuthWorker:
            def __init__(self) -> None:
                self.models = []

            def resolve(self, selected_model):
                self.models.append(selected_model)
                return PiAuthResult(
                    provider_id="xai2",
                    model_id="grok-4.5",
                    api="openai-responses",
                    api_key="pi-derived-xai2-access",
                    headers={},
                    base_url="https://auth-selected.example/v1",
                )

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as temp:
            auth_path = Path(temp) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "xai2": {
                            "type": "oauth",
                            "access": "stored-only-for-pi",
                        }
                    }
                ),
                encoding="utf-8",
            )
            worker = FakePiAuthWorker()
            try:
                resolver = CredentialResolver(auth_path, oauth_worker=worker)
            except TypeError as exc:
                self.fail(f"CredentialResolver hardcodes provider OAuth: {exc}")

            resolved = resolver.resolve(model)

        self.assertEqual(worker.models, [model])
        self.assertEqual(
            resolved.headers,
            {"Authorization": "Bearer pi-derived-xai2-access"},
        )
        self.assertEqual(resolved.base_url, "https://auth-selected.example/v1")
