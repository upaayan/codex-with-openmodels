import unittest

from sudhir_codex_gateway.cursor_catalog import CURSOR_MODEL_ROUTES
from sudhir_codex_gateway.cursor_catalog import cursor_model_info
from sudhir_codex_gateway.cursor_catalog import cursor_route


class CursorCatalogTests(unittest.TestCase):
    def test_exposes_only_the_approved_cursor_routes(self) -> None:
        self.assertEqual(
            [route.gateway_id for route in CURSOR_MODEL_ROUTES],
            [
                "cursor/composer-2.5-fast",
                "cursor/composer-2.5-slow",
                "cursor/composer-latest-fast",
                "cursor/composer-latest-slow",
                "cursor/grok-4.5-fast",
                "cursor/grok-4.5-slow",
                "cursor/grok-4.6-fast",
                "cursor/grok-4.6-slow",
                "cursor/grok-4.7-fast",
                "cursor/grok-4.7-slow",
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

    def test_grok_routes_select_the_requested_alias_and_speed(self) -> None:
        fast = cursor_route("cursor/grok-4.5-fast")
        slow = cursor_route("cursor/grok-4.6-slow")

        self.assertIsNotNone(fast)
        self.assertIsNotNone(slow)
        self.assertEqual(fast.cursor_alias, "grok-4.5")
        self.assertEqual(slow.cursor_alias, "grok-4.6")
        self.assertTrue(fast.fast)
        self.assertFalse(slow.fast)

    def test_model_metadata_is_picker_and_subagent_visible(self) -> None:
        route = cursor_route("cursor/composer-latest-slow")
        self.assertIsNotNone(route)

        info = cursor_model_info(route, "base", 200)

        self.assertEqual(info["slug"], "cursor/composer-latest-slow")
        self.assertEqual(info["visibility"], "list")
        self.assertEqual(info["multi_agent_version"], "v2")
        self.assertFalse(info["supports_parallel_tool_calls"])
        self.assertEqual(info["default_reasoning_level"], "high")
        self.assertEqual(info["context_window"], 200_000)
        self.assertEqual(info["max_context_window"], 200_000)
        self.assertEqual(
            [level["effort"] for level in info["supported_reasoning_levels"]],
            ["high"],
        )
        self.assertNotIn("comp_hash", info)

    def test_grok_routes_advertise_the_cursor_hosted_256k_window(self) -> None:
        for gateway_id in (
            "cursor/grok-4.5-fast",
            "cursor/grok-4.5-slow",
            "cursor/grok-4.6-fast",
            "cursor/grok-4.6-slow",
            "cursor/grok-4.7-fast",
            "cursor/grok-4.7-slow",
        ):
            with self.subTest(gateway_id=gateway_id):
                route = cursor_route(gateway_id)
                self.assertIsNotNone(route)
                info = cursor_model_info(route, "base", 200)
                self.assertEqual(info["context_window"], 256_000)
                self.assertEqual(info["max_context_window"], 256_000)


if __name__ == "__main__":
    unittest.main()
