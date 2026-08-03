"""Load shared open-model definitions and synthesize Codex `/models` metadata."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .cursor_catalog import CURSOR_MODEL_ROUTES
from .cursor_catalog import cursor_model_info
from .errors import GatewayError

LOGGER = logging.getLogger(__name__)
OPENAI_CODEX_PROVIDER = "openai-codex"
MODEL_ID_PREFIX = "pi-"
SUPPORTED_OPEN_MODEL_APIS = {
    "anthropic-messages",
    "openai-completions",
    "openai-responses",
}

# Some built-in providers have known defaults even when models.json omits them.
# Keep this deliberately small: an unknown provider must be explicit.
KNOWN_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "xai": {
        "baseUrl": "https://api.x.ai/v1",
        "api": "openai-completions",
    },
}
KNOWN_MODEL_DEFAULTS: dict[tuple[str, str], dict[str, str]] = {
    ("xai", "grok-4.5"): {
        "api": "openai-responses",
    },
}


@dataclass(frozen=True)
class OpenModel:
    """Resolved routing and metadata for one non-GPT model."""

    gateway_id: str
    provider_id: str
    upstream_id: str
    display_name: str
    base_url: str
    api: str
    api_key_expression: str | None
    compat: dict[str, Any]
    reasoning: bool
    input_modalities: tuple[str, ...]
    context_window: int
    max_tokens: int | None
    raw: dict[str, Any]

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def request_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if self.api == "anthropic-messages":
            if base_url.endswith("/v1"):
                return f"{base_url}/messages"
            return f"{base_url}/v1/messages"
        if self.api == "openai-responses":
            return f"{base_url}/responses"
        return f"{base_url}/chat/completions"


@dataclass(frozen=True)
class Catalog:
    """An immutable exact-ID routing catalog."""

    models: tuple[OpenModel, ...]
    by_gateway_id: dict[str, OpenModel]

    def count_by_provider(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for model in self.models:
            counts[model.provider_id] = counts.get(model.provider_id, 0) + 1
        return counts


class CatalogLoader:
    """Parse the shared model-definition file without modifying it."""

    def __init__(self, models_path: Path, base_instructions_path: Path) -> None:
        self.models_path = models_path
        self.base_instructions_path = base_instructions_path

    def load(self) -> Catalog:
        try:
            document = json.loads(self.models_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise GatewayError(
                503,
                "pi_models_missing",
                f"Shared model-definition file is missing at {self.models_path}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise GatewayError(
                503,
                "pi_models_invalid",
                "Shared model-definition file could not be read as JSON",
            ) from exc

        providers = document.get("providers")
        if not isinstance(providers, dict):
            raise GatewayError(
                503,
                "pi_models_invalid",
                "Shared model-definition file must contain a providers object",
            )

        resolved: list[OpenModel] = []
        seen: set[str] = set()
        for provider_id, provider in providers.items():
            if provider_id == OPENAI_CODEX_PROVIDER:
                continue
            if not isinstance(provider_id, str) or not isinstance(provider, dict):
                LOGGER.warning(
                    "Skipping malformed provider %r: pi_provider_invalid",
                    provider_id,
                )
                continue
            models = provider.get("models", [])
            if not isinstance(models, list):
                LOGGER.warning(
                    "Skipping malformed provider %r: pi_provider_invalid",
                    provider_id,
                )
                continue
            for model_index, model in enumerate(models):
                try:
                    resolved_model = self._resolve_model(provider_id, provider, model)
                except GatewayError as exc:
                    LOGGER.warning(
                        "Skipping malformed model %d for provider %r: %s",
                        model_index,
                        provider_id,
                        exc.code,
                    )
                    continue
                if resolved_model.gateway_id in seen:
                    LOGGER.warning(
                        "Skipping duplicate model %d for provider %r: duplicate_model_id",
                        model_index,
                        provider_id,
                    )
                    continue
                seen.add(resolved_model.gateway_id)
                resolved.append(resolved_model)

        resolved.sort(
            key=lambda model: (model.provider_id, model.display_name, model.upstream_id)
        )
        return Catalog(
            models=tuple(resolved),
            by_gateway_id={model.gateway_id: model for model in resolved},
        )

    def base_instructions(self) -> str:
        try:
            return self.base_instructions_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GatewayError(
                503,
                "base_instructions_missing",
                "Bundled Codex base instructions could not be read",
            ) from exc

    def _resolve_model(
        self,
        provider_id: str,
        provider: dict[str, Any],
        model: object,
    ) -> OpenModel:
        if not isinstance(model, dict):
            raise GatewayError(
                503,
                "pi_model_invalid",
                f"Provider {provider_id!r} contains a non-object model",
            )
        upstream_id = model.get("id")
        if not isinstance(upstream_id, str) or not upstream_id.strip():
            raise GatewayError(
                503,
                "pi_model_invalid",
                f"Provider {provider_id!r} contains a model without an ID",
            )
        upstream_id = upstream_id.strip()
        gateway_id = f"{MODEL_ID_PREFIX}{provider_id}/{upstream_id}"

        defaults = KNOWN_PROVIDER_DEFAULTS.get(provider_id, {})
        model_defaults = KNOWN_MODEL_DEFAULTS.get((provider_id, upstream_id), {})
        base_url = model.get(
            "baseUrl",
            provider.get(
                "baseUrl",
                model_defaults.get("baseUrl", defaults.get("baseUrl")),
            ),
        )
        if not isinstance(base_url, str) or not base_url.strip():
            raise GatewayError(
                503,
                "pi_provider_endpoint_missing",
                f"Provider {provider_id!r} has no resolvable base URL",
            )
        base_url = base_url.strip().rstrip("/")
        self._validate_base_url(provider_id, base_url)

        api = model.get("api", provider.get("api"))
        if api is None:
            api = model_defaults.get("api", defaults.get("api"))
        if api is None and base_url:
            api = "openai-completions"
        if api not in SUPPORTED_OPEN_MODEL_APIS:
            raise GatewayError(
                503,
                "pi_provider_api_unsupported",
                f"Provider {provider_id!r} uses unsupported API {api!r}",
            )

        provider_compat = provider.get("compat", {})
        model_compat = model.get("compat", {})
        if not isinstance(provider_compat, dict) or not isinstance(model_compat, dict):
            raise GatewayError(
                503,
                "pi_model_compat_invalid",
                f"Open-model route {gateway_id!r} has invalid compatibility metadata",
            )
        compat = {**provider_compat, **model_compat}

        display_name = model.get("name", upstream_id)
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = upstream_id
        modalities = model.get("input", ["text"])
        if not isinstance(modalities, list) or not all(
            isinstance(item, str) for item in modalities
        ):
            modalities = ["text"]
        context_window = model.get("contextWindow", 128_000)
        if not isinstance(context_window, int) or context_window <= 0:
            context_window = 128_000
        max_tokens = model.get("maxTokens")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            max_tokens = None
        api_key_expression = model.get("apiKey", provider.get("apiKey"))
        if not isinstance(api_key_expression, str) or not api_key_expression:
            api_key_expression = None

        return OpenModel(
            gateway_id=gateway_id,
            provider_id=provider_id,
            upstream_id=upstream_id,
            display_name=display_name.strip(),
            base_url=base_url,
            api=api,
            api_key_expression=api_key_expression,
            compat=compat,
            reasoning=bool(model.get("reasoning", False)),
            input_modalities=tuple(dict.fromkeys(modalities)) or ("text",),
            context_window=context_window,
            max_tokens=max_tokens,
            raw=dict(model),
        )

    @staticmethod
    def _validate_base_url(provider_id: str, base_url: str) -> None:
        parsed = urlparse(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError(
                503,
                "pi_provider_endpoint_invalid",
                f"Provider {provider_id!r} has an unsafe base URL",
            )
        hostname = (parsed.hostname or "").lower()
        is_loopback = hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme == "https" and hostname:
            return
        if parsed.scheme == "http" and is_loopback:
            return
        raise GatewayError(
            503,
            "pi_provider_endpoint_invalid",
            f"Provider {provider_id!r} must use HTTPS or loopback HTTP",
        )


def synthesize_model_info(
    model: OpenModel,
    base_instructions: str,
    priority: int,
) -> dict[str, Any]:
    """Return a complete Codex ModelInfo object for one open-model route."""

    # Import lazily to avoid a module cycle: reasoning profiles need OpenModel.
    from .reasoning import reasoning_profile

    profile = reasoning_profile(model)
    reasoning_levels = [
        {
            "effort": level.effort,
            "description": level.description,
        }
        for level in profile.levels
    ]
    modalities = [
        modality for modality in model.input_modalities if modality in {"text", "image"}
    ]
    if "text" not in modalities:
        modalities.insert(0, "text")

    return {
        "slug": model.gateway_id,
        "display_name": f"{model.display_name} · {model.provider_id}",
        "description": (
            f"Open model {model.upstream_id} through the {model.provider_id} provider."
        ),
        "default_reasoning_level": profile.default,
        "supported_reasoning_levels": reasoning_levels,
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
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": False,
        "context_window": model.context_window,
        "max_context_window": model.context_window,
        "auto_compact_token_limit": None,
        "effective_context_window_percent": 90,
        "experimental_supported_tools": [],
        "input_modalities": modalities,
        "supports_search_tool": model.api == "openai-responses",
        "use_responses_lite": False,
        "auto_review_model_override": None,
        "tool_mode": None,
        "multi_agent_version": "v2",
    }


def normalize_gpt_models(models: object) -> list[dict[str, Any]]:
    """Validate GPT model objects and align their subagent backend."""

    if not isinstance(models, list):
        raise GatewayError(
            502,
            "gpt_catalog_invalid",
            "ChatGPT returned an invalid model catalog",
        )
    normalized: list[dict[str, Any]] = []
    for value in models:
        if not isinstance(value, dict):
            continue
        slug = value.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        item = dict(value)
        if item.get("visibility") == "list":
            item["multi_agent_version"] = "v2"
        normalized.append(item)
    if not normalized:
        raise GatewayError(
            502,
            "gpt_catalog_empty",
            "ChatGPT returned no usable models",
        )
    return normalized


def merged_catalog_document(
    gpt_models: list[dict[str, Any]],
    catalog: Catalog,
    base_instructions: str,
) -> dict[str, list[dict[str, Any]]]:
    gpt_slugs = {
        item.get("slug") for item in gpt_models if isinstance(item.get("slug"), str)
    }
    open_models: list[dict[str, Any]] = []
    next_priority = (
        max(
            (
                item.get("priority", 0)
                for item in gpt_models
                if isinstance(item.get("priority"), int)
            ),
            default=0,
        )
        + 100
    )
    occupied_slugs = set(gpt_slugs)
    for offset, model in enumerate(catalog.models):
        if model.gateway_id in gpt_slugs:
            raise GatewayError(
                503,
                "catalog_collision",
                f"Generated open-model ID collides with GPT model {model.gateway_id!r}",
            )
        open_models.append(
            synthesize_model_info(model, base_instructions, next_priority + offset)
        )
        occupied_slugs.add(model.gateway_id)

    cursor_models: list[dict[str, Any]] = []
    cursor_priority = next_priority + len(open_models)
    for offset, route in enumerate(CURSOR_MODEL_ROUTES):
        if route.gateway_id in occupied_slugs:
            raise GatewayError(
                503,
                "catalog_collision",
                f"Cursor model ID collides with another model {route.gateway_id!r}",
            )
        cursor_models.append(
            cursor_model_info(
                route,
                base_instructions,
                cursor_priority + offset,
            )
        )
        occupied_slugs.add(route.gateway_id)
    return {"models": [*gpt_models, *open_models, *cursor_models]}


def catalog_etag(document: dict[str, object]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return f'"{hashlib.sha256(encoded).hexdigest()}"'
