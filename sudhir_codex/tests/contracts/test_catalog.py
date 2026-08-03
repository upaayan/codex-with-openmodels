import tempfile
import unittest
from pathlib import Path

from contracts._legacy import legacy_module
from contracts._legacy import run_cases
from helpers import basic_pi_document
from helpers import make_repo
from helpers import write_json
from sudhir_codex_gateway.catalog import CatalogLoader
from sudhir_codex_gateway.catalog import merged_catalog_document

_app = legacy_module("test_app")
_catalog = legacy_module("test_catalog")
_cursor_catalog = legacy_module("test_cursor_catalog")


class CatalogContracts(unittest.TestCase):
    def test_merged_catalog_has_unique_gpt_pi_and_cursor_ids(self) -> None:
        run_cases(
            self,
            (
                (_catalog.CatalogTests, "test_loads_non_codex_models_with_collision_free_ids"),
                (_catalog.CatalogTests, "test_merged_catalog_keeps_gpt_and_adds_pi"),
                (_app.GatewayAppTests, "test_models_merge_live_gpt_and_pi_catalogs"),
                (
                    _cursor_catalog.CursorCatalogTests,
                    "test_pinned_and_moving_aliases_remain_distinct",
                ),
            ),
        )

    def test_visibility_policy_controls_picker_and_agent_metadata(self) -> None:
        run_cases(
            self,
            (
                (
                    _catalog.CatalogTests,
                    "test_synthesized_model_is_picker_and_agent_visible",
                ),
                (
                    _cursor_catalog.CursorCatalogTests,
                    "test_model_metadata_is_picker_and_subagent_visible",
                ),
                (
                    _catalog.CatalogTests,
                    "test_visibility_policy_hides_by_default_and_supports_globs",
                ),
            ),
        )

    def test_pi_and_cursor_models_have_no_synthetic_comp_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = make_repo(root / "repo")
            pi_dir = root / "pi"
            models_path = pi_dir / "models.json"
            write_json(models_path, basic_pi_document())
            instructions = repo / "codex-rs" / "models-manager" / "prompt.md"
            loader = CatalogLoader(models_path, instructions)
            document = merged_catalog_document(
                [
                    {
                        "slug": "gpt-test",
                        "visibility": "list",
                        "priority": 1,
                    }
                ],
                loader.load(),
                loader.base_instructions(),
            )
            synthesized = [
                model for model in document["models"] if model["slug"] != "gpt-test"
            ]
            self.assertTrue(synthesized, "fixture must include synthesized Pi/Cursor models")
            self.assertTrue(
                all("comp_hash" not in model for model in synthesized),
                "synthesized provider models must not fabricate compaction identity",
            )
