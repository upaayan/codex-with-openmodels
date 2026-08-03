"""Apply a private, hot-reloaded visibility policy to the merged model catalog."""

import fnmatch
import json
from pathlib import Path
from typing import Any

from .errors import GatewayError

VALID_VISIBILITIES = {"hide", "list"}


def apply_model_visibility(
    document: dict[str, list[dict[str, Any]]],
    policy_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Set picker visibility without removing any model route from the catalog."""

    policy = _load_policy(policy_path)
    if policy is None:
        return document

    default, show_patterns, hide_patterns = policy
    for model in document["models"]:
        slug = model.get("slug")
        if not isinstance(slug, str):
            continue
        visibility = default
        if _matches(slug, show_patterns):
            visibility = "list"
        if _matches(slug, hide_patterns):
            visibility = "hide"
        model["visibility"] = visibility
    return document


def _load_policy(path: Path) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    if not path.exists():
        return None
    if path.is_symlink():
        raise GatewayError(
            503,
            "model_visibility_symlink",
            f"Model visibility policy may not be a symlink: {path}",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GatewayError(
            503,
            "model_visibility_invalid",
            f"Model visibility policy could not be read as JSON: {path}",
        ) from exc
    if not isinstance(value, dict):
        raise GatewayError(
            503,
            "model_visibility_invalid",
            "Model visibility policy must contain a JSON object",
        )

    default = value.get("default")
    if default not in VALID_VISIBILITIES:
        raise GatewayError(
            503,
            "model_visibility_invalid",
            "Model visibility policy default must be 'list' or 'hide'",
        )
    return (
        default,
        _patterns(value, "show"),
        _patterns(value, "hide"),
    )


def _patterns(value: dict[str, Any], field: str) -> tuple[str, ...]:
    patterns = value.get(field, [])
    if not isinstance(patterns, list) or not all(
        isinstance(pattern, str) and pattern for pattern in patterns
    ):
        raise GatewayError(
            503,
            "model_visibility_invalid",
            f"Model visibility policy {field!r} must be a list of non-empty strings",
        )
    return tuple(patterns)


def _matches(slug: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(slug, pattern) for pattern in patterns)
