"""The deliberately small Cursor model surface exposed by Sudhir-Codex."""

from dataclasses import dataclass
from typing import Any

CURSOR_MODEL_PREFIX = "cursor/"
CURSOR_CONTEXT_WINDOW = 128_000


@dataclass(frozen=True)
class CursorModelRoute:
    """One stable Sudhir-Codex route to a Cursor catalog alias and speed."""

    gateway_id: str
    cursor_alias: str
    fast: bool
    display_name: str
    description: str


CURSOR_MODEL_ROUTES = (
    CursorModelRoute(
        gateway_id="cursor/composer-2.5-fast",
        cursor_alias="composer-2.5",
        fast=True,
        display_name="Composer 2.5 Fast · Cursor",
        description="Pinned Composer 2.5 using Cursor's fast variant.",
    ),
    CursorModelRoute(
        gateway_id="cursor/composer-2.5-slow",
        cursor_alias="composer-2.5",
        fast=False,
        display_name="Composer 2.5 Slow · Cursor",
        description="Pinned Composer 2.5 using Cursor's slow variant.",
    ),
    CursorModelRoute(
        gateway_id="cursor/composer-latest-fast",
        cursor_alias="composer-latest",
        fast=True,
        display_name="Composer Latest Fast · Cursor",
        description=(
            "Cursor's moving Composer Latest alias using the fast variant."
        ),
    ),
    CursorModelRoute(
        gateway_id="cursor/composer-latest-slow",
        cursor_alias="composer-latest",
        fast=False,
        display_name="Composer Latest Slow · Cursor",
        description=(
            "Cursor's moving Composer Latest alias using the slow variant."
        ),
    ),
)

_ROUTES_BY_ID = {route.gateway_id: route for route in CURSOR_MODEL_ROUTES}


def cursor_route(model_id: str) -> CursorModelRoute | None:
    """Resolve only an explicitly approved Cursor gateway ID."""

    return _ROUTES_BY_ID.get(model_id)


def cursor_model_info(
    route: CursorModelRoute,
    base_instructions: str,
    priority: int,
) -> dict[str, Any]:
    """Return Codex ModelInfo metadata for a Cursor-native agent route."""

    return {
        "slug": route.gateway_id,
        "display_name": route.display_name,
        "description": route.description,
        # Cursor owns the native agent's reasoning behavior. Fast/slow is an
        # explicit route choice, so Codex should not advertise fake effort knobs.
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {
                "effort": "high",
                "description": "Cursor controls Composer's native reasoning.",
            }
        ],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": base_instructions,
        "model_messages": None,
        "include_skills_usage_instructions": True,
        "supports_reasoning_summary_parameter": False,
        "default_reasoning_summary": "none",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        # Composer executes its own native tool loop inside the worker.
        "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False,
        "context_window": CURSOR_CONTEXT_WINDOW,
        "max_context_window": CURSOR_CONTEXT_WINDOW,
        "auto_compact_token_limit": None,
        "effective_context_window_percent": 90,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "auto_review_model_override": None,
        "tool_mode": None,
        # This allows a Codex parent task to select Composer for a child agent.
        "multi_agent_version": "v2",
    }
