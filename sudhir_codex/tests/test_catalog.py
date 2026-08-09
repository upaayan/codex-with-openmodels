import tempfile
import unittest
from pathlib import Path

from helpers import basic_pi_document
from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.catalog import catalog_etag
from sudhir_codex_gateway.catalog import merged_catalog_document
from sudhir_codex_gateway.catalog import normalize_gpt_models
from sudhir_codex_gateway.catalog import synthesize_model_info
from sudhir_codex_gateway.errors import GatewayError
from sudhir_codex_gateway.visibility import apply_model_visibility


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_repo(self.root / "repo")
        self.pi_dir = self.root / "pi"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def loader(self) -> CatalogLoader:
        return CatalogLoader(
            self.pi_dir / "models.json",
            self.repo / "codex-rs" / "models-manager" / "prompt.md",
        )

    def test_loads_non_codex_models_with_collision_free_ids(self) -> None:
        write_json(self.pi_dir / "models.json", basic_pi_document())

        catalog = self.loader().load()

        self.assertEqual(len(catalog.models), 1)
        model = catalog.models[0]
        self.assertEqual(model.gateway_id, "pi-demo/demo/model")
        self.assertEqual(model.upstream_id, "demo/model")
        self.assertEqual(model.api, "openai-completions")
        self.assertNotIn("gpt-private", catalog.by_gateway_id)

    def test_inherits_provider_compat_and_uses_known_xai_default(self) -> None:
        document = {
            "providers": {
                "xai": {
                    "compat": {"supportsDeveloperRole": False},
                    "models": [
                        {
                            "id": "grok-test",
                            "compat": {"supportsReasoningEffort": True},
                        }
                    ],
                }
            }
        }
        write_json(self.pi_dir / "models.json", document)

        model = self.loader().load().models[0]

        self.assertEqual(model.base_url, "https://api.x.ai/v1")
        self.assertEqual(model.api, "openai-completions")
        self.assertFalse(model.compat["supportsDeveloperRole"])
        self.assertTrue(model.compat["supportsReasoningEffort"])

    def test_model_can_override_provider_with_anthropic_messages(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["models"][0]["api"] = "anthropic-messages"
        document["providers"]["demo"]["models"][0]["baseUrl"] = "https://pi.test"
        write_json(self.pi_dir / "models.json", document)

        model = self.loader().load().models[0]

        self.assertEqual(model.api, "anthropic-messages")
        self.assertEqual(model.request_url, "https://pi.test/v1/messages")

    def test_xai_grok45_enables_deferred_tool_search(self) -> None:
        write_json(
            self.pi_dir / "models.json",
            {
                "providers": {
                    "xai": {
                        "models": [
                            {
                                "id": "grok-4.5",
                                "reasoning": True,
                                "input": ["text", "image"],
                            }
                        ]
                    }
                }
            },
        )
        model = self.loader().load().models[0]

        info = synthesize_model_info(model, "base", 100)

        self.assertTrue(info["supports_search_tool"])

    def test_chat_completion_models_enable_gateway_tool_search(self) -> None:
        write_json(
            self.pi_dir / "models.json",
            {
                "providers": {
                    "xai": {
                        "models": [
                            {
                                "id": "grok-4.3",
                                "reasoning": True,
                                "input": ["text", "image"],
                            }
                        ]
                    }
                }
            },
        )
        model = self.loader().load().models[0]

        info = synthesize_model_info(model, "base", 100)

        self.assertEqual(model.api, "openai-completions")
        self.assertTrue(info["supports_search_tool"])

    def test_duplicate_generated_ids_keep_the_first_healthy_model(self) -> None:
        document = basic_pi_document()
        document["providers"]["demo"]["models"].append(
            {"id": "demo/model", "name": "Duplicate"}
        )
        write_json(self.pi_dir / "models.json", document)

        with self.assertLogs("sudhir_codex_gateway.catalog", level="WARNING"):
            catalog = self.loader().load()

        self.assertEqual(
            [model.gateway_id for model in catalog.models],
            ["pi-demo/demo/model"],
        )

    def test_malformed_provider_and_model_do_not_hide_healthy_models(self) -> None:
        document = basic_pi_document()
        document["providers"]["broken-provider"] = {
            "models": [{"id": "broken/model"}]
        }
        document["providers"]["demo"]["models"].append({"name": "Missing ID"})
        write_json(self.pi_dir / "models.json", document)

        with self.assertLogs(
            "sudhir_codex_gateway.catalog",
            level="WARNING",
        ) as logs:
            catalog = self.loader().load()

        self.assertEqual(
            [model.gateway_id for model in catalog.models],
            ["pi-demo/demo/model"],
        )
        self.assertIn("pi_provider_endpoint_missing", "\n".join(logs.output))
        self.assertIn("pi_model_invalid", "\n".join(logs.output))

    def test_skips_remote_plain_http_endpoint(self) -> None:
        write_json(
            self.pi_dir / "models.json",
            basic_pi_document("http://pi.test/v1"),
        )

        with self.assertLogs(
            "sudhir_codex_gateway.catalog",
            level="WARNING",
        ) as logs:
            catalog = self.loader().load()

        self.assertEqual(catalog.models, ())
        self.assertIn("pi_provider_endpoint_invalid", "\n".join(logs.output))

    def test_synthesized_model_is_picker_and_agent_visible(self) -> None:
        write_json(self.pi_dir / "models.json", basic_pi_document())
        model = self.loader().load().models[0]

        info = synthesize_model_info(model, "base", 100)

        self.assertEqual(info["visibility"], "list")
        self.assertEqual(info["multi_agent_version"], "v2")
        self.assertEqual(info["apply_patch_tool_type"], "freeform")
        self.assertTrue(info["supports_parallel_tool_calls"])
        self.assertEqual(info["input_modalities"], ["text", "image"])
        self.assertNotIn("comp_hash", info)

    def test_known_routes_advertise_only_real_reasoning_controls(self) -> None:
        document = {
            "providers": {
                "moonshot": {
                    "baseUrl": "https://api.moonshot.ai/v1",
                    "compat": {"supportsReasoningEffort": False},
                    "models": [
                        {"id": "kimi-k3", "reasoning": True},
                        {"id": "kimi-k2.7-code", "reasoning": True},
                    ],
                },
                "nvidia": {
                    "baseUrl": "https://integrate.api.nvidia.com/v1",
                    "compat": {"supportsReasoningEffort": False},
                    "models": [
                        {"id": "z-ai/glm-5.2", "reasoning": True},
                    ],
                },
                "backup-llama": {
                    "baseUrl": "http://127.0.0.1:18081/v1",
                    "compat": {"supportsReasoningEffort": False},
                    "models": [
                        {"id": "gemma4-31b-qat-q4xl", "reasoning": False},
                        {"id": "gemma4-31b-qat-q4xl-think", "reasoning": True},
                    ],
                },
                "opencode-go": {
                    "baseUrl": "https://opencode.test/zen/go/v1",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "minimax-m3",
                            "api": "anthropic-messages",
                            "reasoning": True,
                        },
                        {
                            "id": "qwen3.7-plus",
                            "api": "anthropic-messages",
                            "reasoning": True,
                        },
                    ],
                },
            }
        }
        write_json(self.pi_dir / "models.json", document)
        models = {
            model.upstream_id: synthesize_model_info(model, "base", 100)
            for model in self.loader().load().models
        }

        def efforts(model_id: str) -> list[str]:
            return [
                item["effort"]
                for item in models[model_id]["supported_reasoning_levels"]
            ]

        self.assertEqual(efforts("kimi-k3"), ["low", "high", "max"])
        self.assertEqual(models["kimi-k3"]["default_reasoning_level"], "high")
        self.assertEqual(efforts("kimi-k2.7-code"), ["high"])
        self.assertEqual(efforts("z-ai/glm-5.2"), ["high"])
        self.assertEqual(efforts("gemma4-31b-qat-q4xl"), ["none"])
        self.assertEqual(efforts("gemma4-31b-qat-q4xl-think"), ["high"])
        self.assertEqual(efforts("minimax-m3"), ["none", "high"])
        self.assertEqual(efforts("qwen3.7-plus"), ["none", "high", "max"])

    def test_direct_zai_glm52_advertises_every_documented_effort(self) -> None:
        document = {
            "providers": {
                "zai": {
                    "baseUrl": "https://api.z.ai/api/coding/paas/v4",
                    "models": [{"id": "glm-5.2", "reasoning": True}],
                }
            }
        }
        write_json(self.pi_dir / "models.json", document)
        model = self.loader().load().models[0]

        info = synthesize_model_info(model, "base", 100)

        self.assertEqual(
            [item["effort"] for item in info["supported_reasoning_levels"]],
            ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(info["default_reasoning_level"], "high")

    def test_explicit_effort_map_defaults_to_high_when_available(self) -> None:
        write_json(self.pi_dir / "models.json", basic_pi_document())
        model = self.loader().load().models[0]

        info = synthesize_model_info(model, "base", 100)

        self.assertEqual(
            [item["effort"] for item in info["supported_reasoning_levels"]],
            ["low", "medium", "high"],
        )
        self.assertEqual(info["default_reasoning_level"], "high")

    def test_merged_catalog_keeps_gpt_and_adds_pi(self) -> None:
        write_json(self.pi_dir / "models.json", basic_pi_document())
        catalog = self.loader().load()
        gpt = normalize_gpt_models(
            [
                {
                    "slug": "gpt-test",
                    "display_name": "GPT Test",
                    "visibility": "list",
                    "priority": 1,
                }
            ]
        )

        document = merged_catalog_document(gpt, catalog, "base")

        self.assertEqual(
            [model["slug"] for model in document["models"]],
            [
                "gpt-test",
                "pi-demo/demo/model",
                "cursor/composer-2.5-fast",
                "cursor/composer-2.5-slow",
                "cursor/composer-latest-fast",
                "cursor/composer-latest-slow",
            ],
        )
        self.assertEqual(document["models"][0]["multi_agent_version"], "v2")
        self.assertEqual(catalog_etag(document), catalog_etag(document))

    def test_visibility_policy_hides_by_default_and_supports_globs(self) -> None:
        policy_path = self.root / "model-visibility.json"
        write_json(
            policy_path,
            {
                "default": "hide",
                "show": ["gpt-5.6-*", "pi-deepseek/*"],
            },
        )
        document = {
            "models": [
                {"slug": "gpt-5.6-sol", "visibility": "list"},
                {"slug": "gpt-5.4", "visibility": "list"},
                {"slug": "pi-deepseek/deepseek-v4-pro", "visibility": "list"},
                {"slug": "pi-cerebras/gpt-oss-120b", "visibility": "list"},
            ]
        }

        result = apply_model_visibility(document, policy_path)

        self.assertEqual(
            {model["slug"]: model["visibility"] for model in result["models"]},
            {
                "gpt-5.6-sol": "list",
                "gpt-5.4": "hide",
                "pi-deepseek/deepseek-v4-pro": "list",
                "pi-cerebras/gpt-oss-120b": "hide",
            },
        )

    def test_visibility_policy_rejects_invalid_json(self) -> None:
        policy_path = self.root / "model-visibility.json"
        policy_path.write_text("{", encoding="utf-8")

        with self.assertRaisesRegex(GatewayError, "could not be read as JSON"):
            apply_model_visibility({"models": []}, policy_path)


if __name__ == "__main__":
    unittest.main()
