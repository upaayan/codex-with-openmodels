import unittest

from sudhir_codex_gateway.cursor_catalog import CURSOR_MODEL_ROUTES
from sudhir_codex_gateway.cursor_catalog import cursor_model_info
from sudhir_codex_gateway.cursor_catalog import cursor_route


class CursorCatalogTests(unittest.TestCase):
    def test_exposes_only_the_four_approved_composer_routes(self) -> None:
        self.assertEqual(
            [route.gateway_id for route in CURSOR_MODEL_ROUTES],
            [
                "cursor/composer-2.5-fast",
                "cursor/composer-2.5-slow",
                "cursor/composer-latest-fast",
                "cursor/composer-latest-slow",
            ],
        )

    def test_pinned_and_moving_aliases_remain_distinct(self) -> None:
        pinned = cursor_route("cursor/composer-2.5-fast")
        latest = cursor_route("cursor/composer-latest-fast")

        self.assertIsNotNone(pinned)
        self.assertIsNotNone(latest)
        self.assertEqual(pinned.cursor_alias, "composer-2.5")
        self.assertEqual(latest.cursor_alias, "composer-latest")
        self.assertTrue(pinned.fast)
        self.assertTrue(latest.fast)

    def test_model_metadata_is_picker_and_subagent_visible(self) -> None:
        route = cursor_route("cursor/composer-latest-slow")
        self.assertIsNotNone(route)

        info = cursor_model_info(route, "base", 200)

        self.assertEqual(info["slug"], "cursor/composer-latest-slow")
        self.assertEqual(info["visibility"], "list")
        self.assertEqual(info["multi_agent_version"], "v2")
        self.assertFalse(info["supports_parallel_tool_calls"])
        self.assertEqual(info["default_reasoning_level"], "high")
        self.assertEqual(
            [level["effort"] for level in info["supported_reasoning_levels"]],
            ["high"],
        )
        self.assertNotIn("comp_hash", info)


if __name__ == "__main__":
    unittest.main()
