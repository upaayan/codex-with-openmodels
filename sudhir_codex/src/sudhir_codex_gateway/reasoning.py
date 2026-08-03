"""Provider-specific reasoning capabilities for Sudhir-Codex open-model routes.

The shared provider-definition file describes endpoints and credentials, but its
generic ``reasoning`` and ``supportsReasoningEffort`` flags are not sufficient
to describe the different request shapes used by each hosted model.  This
module is the Sudhir-Codex source of truth for effective, user-selectable
reasoning levels and their Chat Completions payloads.
"""

import copy
from dataclasses import dataclass
from typing import Any

from .catalog import OpenModel

REASONING_ORDER = (
    "none",
    "minimal",
    "low",
    "medium",
    "adaptive",
    "high",
    "xhigh",
    "max",
    "ultra",
)


@dataclass(frozen=True)
class ReasoningLevel:
    """One effective level and the provider fields that select it."""

    effort: str
    request_options: dict[str, Any]
    description: str


@dataclass(frozen=True)
class ReasoningProfile:
    """The complete reasoning contract for one provider/model route."""

    levels: tuple[ReasoningLevel, ...]
    default: str

    @property
    def efforts(self) -> tuple[str, ...]:
        return tuple(level.effort for level in self.levels)

    def level(self, effort: str) -> ReasoningLevel:
        for level in self.levels:
            if level.effort == effort:
                return level
        raise KeyError(effort)


def _level(
    effort: str,
    request_options: dict[str, Any] | None = None,
    description: str | None = None,
) -> ReasoningLevel:
    if description is None:
        description = f"Use {effort} reasoning effort."
    return ReasoningLevel(effort, request_options or {}, description)


def _profile(*levels: ReasoningLevel, default: str = "high") -> ReasoningProfile:
    efforts = tuple(level.effort for level in levels)
    if default not in efforts:
        raise ValueError(f"default effort {default!r} is not in {efforts!r}")
    return ReasoningProfile(levels=tuple(levels), default=default)


NO_REASONING = _profile(
    _level(
        "none",
        description="This route does not use a reasoning mode.",
    ),
    default="none",
)
FIXED_REASONING = _profile(
    _level(
        "high",
        description="This route always reasons; the provider exposes no adjustable effort.",
    )
)

DEEPSEEK_V4 = _profile(
    _level("none", {"thinking": {"type": "disabled"}}, "Disable thinking."),
    _level(
        "low",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        },
        "Accepted compatibility level; DeepSeek maps low to high.",
    ),
    _level(
        "medium",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "medium",
        },
        "Accepted compatibility level; DeepSeek maps medium to high.",
    ),
    _level(
        "high",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    ),
    _level(
        "xhigh",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "xhigh",
        },
        "Accepted compatibility level; DeepSeek maps extra high to max.",
    ),
    _level(
        "max",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        },
    ),
)

MOONSHOT_K3 = _profile(
    _level("low", {"reasoning_effort": "low"}),
    _level("high", {"reasoning_effort": "high"}),
    _level("max", {"reasoning_effort": "max"}),
)

THINKING_TOGGLE = _profile(
    _level("none", {"thinking": {"type": "disabled"}}, "Disable thinking."),
    _level("high", {"thinking": {"type": "enabled"}}, "Enable thinking."),
)

ZAI_GLM52 = _profile(
    _level(
        "none",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "none",
        },
        "Accepted compatibility level; GLM-5.2 skips thinking.",
    ),
    _level(
        "minimal",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "minimal",
        },
        "Accepted compatibility level; GLM-5.2 skips thinking.",
    ),
    _level(
        "low",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
        },
        "Accepted compatibility level; GLM-5.2 maps low to high.",
    ),
    _level(
        "medium",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "medium",
        },
        "Accepted compatibility level; GLM-5.2 maps medium to high.",
    ),
    _level(
        "high",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    ),
    _level(
        "xhigh",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "xhigh",
        },
        "Accepted compatibility level; GLM-5.2 maps extra high to max.",
    ),
    _level(
        "max",
        {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        },
    ),
)

NVIDIA_DEEPSEEK_V4 = _profile(
    _level("none", {"reasoning_effort": "none"}, "Disable thinking."),
    _level("high", {"reasoning_effort": "high"}),
    _level("max", {"reasoning_effort": "max"}),
)

NVIDIA_GLM52 = _profile(
    _level(
        "high",
        description=(
            "The hosted NVIDIA route reasons, but its current API schema "
            "documents no per-request effort control."
        ),
    )
)

NVIDIA_MINIMAX_M3 = _profile(
    _level(
        "none",
        {"chat_template_kwargs": {"thinking_mode": "disabled"}},
        "Disable thinking.",
    ),
    _level(
        "adaptive",
        {"chat_template_kwargs": {"thinking_mode": "adaptive"}},
        "Let MiniMax choose whether and how much to think.",
    ),
    _level(
        "high",
        {"chat_template_kwargs": {"thinking_mode": "enabled"}},
        "Enable thinking.",
    ),
)

NVIDIA_ENABLE_THINKING = _profile(
    _level(
        "none",
        {"chat_template_kwargs": {"enable_thinking": False}},
        "Disable thinking.",
    ),
    _level(
        "high",
        {"chat_template_kwargs": {"enable_thinking": True}},
        "Enable thinking.",
    ),
)

CEREBRAS_GLM47 = _profile(
    _level(
        "none",
        {"reasoning_effort": "none"},
        "Disable reasoning.",
    ),
    _level(
        "high",
        description="Enable the model's fixed reasoning mode.",
    ),
)

CEREBRAS_GEMMA4 = _profile(
    _level("none", {"reasoning_effort": "none"}, "Disable reasoning."),
    _level(
        "low",
        {"reasoning_effort": "low"},
        "Enable reasoning; Cerebras currently treats low, medium, and high as equivalent.",
    ),
    _level(
        "medium",
        {"reasoning_effort": "medium"},
        "Enable reasoning; Cerebras currently treats low, medium, and high as equivalent.",
    ),
    _level(
        "high",
        {"reasoning_effort": "high"},
        "Enable reasoning; Cerebras currently treats low, medium, and high as equivalent.",
    ),
)

LOW_MEDIUM_HIGH = _profile(
    _level("low", {"reasoning_effort": "low"}),
    _level("medium", {"reasoning_effort": "medium"}),
    _level("high", {"reasoning_effort": "high"}),
)


def _openrouter_profile(
    *efforts: str,
    default: str = "high",
) -> ReasoningProfile:
    return _profile(
        *(_level(effort, {"reasoning": {"effort": effort}}) for effort in efforts),
        default=default,
    )


OPENROUTER_DEEPSEEK_V4 = _openrouter_profile("high", "xhigh")
OPENROUTER_GEMINI_36_FLASH = _openrouter_profile(
    "minimal",
    "low",
    "medium",
    "high",
)
OPENROUTER_KIMI_K3 = _openrouter_profile("low", "high", "max")
OPENROUTER_GPT56 = _openrouter_profile(
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
OPENROUTER_GROK43 = _openrouter_profile("none", "low", "medium", "high")
OPENROUTER_GROK45 = _openrouter_profile("low", "medium", "high")
OPENROUTER_GLM52 = _openrouter_profile("high", "xhigh")

OPENCODE_MINIMAX_M3 = _profile(
    _level("none", {"thinking": {"type": "disabled"}}, "Disable thinking."),
    _level(
        "high",
        {"thinking": {"type": "adaptive"}},
        "Enable MiniMax adaptive thinking.",
    ),
)

OPENCODE_QWEN = _profile(
    _level("none", {"thinking": {"type": "disabled"}}, "Disable thinking."),
    _level(
        "high",
        {"thinking": {"type": "enabled", "budget_tokens": 16_000}},
        "Enable thinking with the standard OpenCode high budget.",
    ),
    _level(
        "max",
        {"thinking": {"type": "enabled", "budget_tokens": 31_999}},
        "Enable thinking with the maximum OpenCode budget.",
    ),
)

OPENCODE_GLM52 = _profile(
    _level("high", {"reasoning_effort": "high"}),
    _level("max", {"reasoning_effort": "max"}),
)

OPENCODE_DEEPSEEK_V4 = _profile(
    _level("high", {"reasoning_effort": "high"}),
    _level("max", {"reasoning_effort": "max"}),
)

OPENCODE_HY3 = _profile(
    _level("none", {"reasoning_effort": "none"}, "Disable reasoning."),
    _level("low", {"reasoning_effort": "low"}),
    _level("high", {"reasoning_effort": "high"}),
)


ROUTE_PROFILES: dict[tuple[str, str], ReasoningProfile] = {
    # Direct DeepSeek API.
    ("deepseek", "deepseek-v4-pro"): DEEPSEEK_V4,
    ("deepseek", "deepseek-v4-flash"): DEEPSEEK_V4,
    # Direct Moonshot API.
    ("moonshot", "kimi-k3"): MOONSHOT_K3,
    ("moonshot", "kimi-k2.7-code"): FIXED_REASONING,
    ("moonshot", "kimi-k2.6"): THINKING_TOGGLE,
    ("moonshot", "kimi-k2.5"): THINKING_TOGGLE,
    # Z.AI Coding Plan.
    ("zai", "glm-5.2"): ZAI_GLM52,
    ("zai", "glm-5.1"): THINKING_TOGGLE,
    ("zai", "glm-5-turbo"): THINKING_TOGGLE,
    # NVIDIA NIM.
    ("nvidia", "deepseek-ai/deepseek-v4-flash"): NVIDIA_DEEPSEEK_V4,
    ("nvidia", "deepseek-ai/deepseek-v4-pro"): NVIDIA_DEEPSEEK_V4,
    ("nvidia", "z-ai/glm-5.2"): NVIDIA_GLM52,
    ("nvidia", "minimaxai/minimax-m2.7"): FIXED_REASONING,
    ("nvidia", "minimaxai/minimax-m3"): NVIDIA_MINIMAX_M3,
    ("nvidia", "google/diffusiongemma-26b-a4b-it"): NVIDIA_ENABLE_THINKING,
    ("nvidia", "google/gemma-4-31b-it"): NVIDIA_ENABLE_THINKING,
    # Cerebras Inference.
    ("cerebras", "zai-glm-4.7"): CEREBRAS_GLM47,
    ("cerebras", "gemma-4-31b"): CEREBRAS_GEMMA4,
    ("cerebras", "gpt-oss-120b"): LOW_MEDIUM_HIGH,
    # xAI.
    ("xai", "grok-4.3"): _profile(
        _level("none", {"reasoning_effort": "none"}, "Disable reasoning."),
        _level("low", {"reasoning_effort": "low"}),
        _level("medium", {"reasoning_effort": "medium"}),
        _level("high", {"reasoning_effort": "high"}),
    ),
    ("xai", "grok-4.5"): LOW_MEDIUM_HIGH,
    # OpenCode Go. These are capabilities of the Go route, which can differ
    # from the same underlying model on its first-party endpoint.
    ("opencode-go", "minimax-m3"): OPENCODE_MINIMAX_M3,
    ("opencode-go", "minimax-m2.7"): FIXED_REASONING,
    ("opencode-go", "minimax-m2.5"): FIXED_REASONING,
    ("opencode-go", "kimi-k2.7-code"): FIXED_REASONING,
    ("opencode-go", "kimi-k2.6"): FIXED_REASONING,
    ("opencode-go", "kimi-k2.5"): FIXED_REASONING,
    ("opencode-go", "glm-5.2"): OPENCODE_GLM52,
    ("opencode-go", "glm-5.1"): FIXED_REASONING,
    ("opencode-go", "glm-5"): FIXED_REASONING,
    ("opencode-go", "deepseek-v4-pro"): OPENCODE_DEEPSEEK_V4,
    ("opencode-go", "deepseek-v4-flash"): OPENCODE_DEEPSEEK_V4,
    ("opencode-go", "qwen3.7-max"): OPENCODE_QWEN,
    ("opencode-go", "qwen3.7-plus"): OPENCODE_QWEN,
    ("opencode-go", "qwen3.6-plus"): OPENCODE_QWEN,
    ("opencode-go", "qwen3.5-plus"): OPENCODE_QWEN,
    ("opencode-go", "mimo-v2-pro"): FIXED_REASONING,
    ("opencode-go", "mimo-v2-omni"): FIXED_REASONING,
    ("opencode-go", "mimo-v2.5-pro"): FIXED_REASONING,
    ("opencode-go", "mimo-v2.5"): FIXED_REASONING,
    ("opencode-go", "hy3-preview"): OPENCODE_HY3,
    # OpenRouter's normalized nested reasoning parameter. Models without an
    # advertised effort list intentionally fall back to fixed high reasoning.
    ("openrouter", "deepseek/deepseek-v4-flash"): OPENROUTER_DEEPSEEK_V4,
    ("openrouter", "deepseek/deepseek-v4-pro"): OPENROUTER_DEEPSEEK_V4,
    ("openrouter", "google/gemini-3.6-flash"): OPENROUTER_GEMINI_36_FLASH,
    ("openrouter", "moonshotai/kimi-k3"): OPENROUTER_KIMI_K3,
    ("openrouter", "openai/gpt-5.6-luna"): OPENROUTER_GPT56,
    ("openrouter", "openai/gpt-5.6-luna-pro"): OPENROUTER_GPT56,
    ("openrouter", "openai/gpt-5.6-sol"): OPENROUTER_GPT56,
    ("openrouter", "openai/gpt-5.6-sol-pro"): OPENROUTER_GPT56,
    ("openrouter", "openai/gpt-5.6-terra"): OPENROUTER_GPT56,
    ("openrouter", "openai/gpt-5.6-terra-pro"): OPENROUTER_GPT56,
    ("openrouter", "x-ai/grok-4.3"): OPENROUTER_GROK43,
    ("openrouter", "x-ai/grok-4.5"): OPENROUTER_GROK45,
    ("openrouter", "z-ai/glm-5.2"): OPENROUTER_GLM52,
}


def reasoning_profile(model: OpenModel) -> ReasoningProfile:
    """Return the exact route profile, with conservative metadata fallbacks."""

    if model.provider_id == "backup-llama":
        return FIXED_REASONING if model.upstream_id.endswith("-think") else NO_REASONING

    known = ROUTE_PROFILES.get((model.provider_id, model.upstream_id))
    if known is not None:
        return known

    if not model.reasoning:
        return NO_REASONING

    mapped = _mapped_metadata_profile(model)
    if mapped is not None:
        return mapped

    if model.compat.get("supportsReasoningEffort") is True:
        return LOW_MEDIUM_HIGH

    # Unknown reasoning routes remain usable, but do not receive a menu full of
    # controls that may be silently ignored by the provider.
    return FIXED_REASONING


def reasoning_request_options(
    request: dict[str, Any],
    model: OpenModel,
) -> dict[str, Any]:
    """Translate the requested Codex effort to provider-specific request fields."""

    profile = reasoning_profile(model)
    requested: str | None = None
    reasoning = request.get("reasoning")
    if isinstance(reasoning, dict):
        value = reasoning.get("effort")
        if isinstance(value, str) and value:
            requested = value
    selected = requested if requested in profile.efforts else profile.default
    return copy.deepcopy(profile.level(selected).request_options)


def _mapped_metadata_profile(model: OpenModel) -> ReasoningProfile | None:
    """Honor explicit maps for user-added routes that are not in the catalog."""

    mappings: dict[str, str] = {}
    thinking_map = model.raw.get("thinkingLevelMap")
    if isinstance(thinking_map, dict):
        for source, destination in thinking_map.items():
            if isinstance(source, str) and isinstance(destination, str) and destination:
                mappings[source] = destination
    effort_map = model.compat.get("reasoningEffortMap")
    if isinstance(effort_map, dict):
        for source, destination in effort_map.items():
            if isinstance(source, str) and isinstance(destination, str) and destination:
                mappings[source] = destination
    if not mappings:
        return None

    ordered = sorted(
        mappings,
        key=lambda effort: (
            REASONING_ORDER.index(effort)
            if effort in REASONING_ORDER
            else len(REASONING_ORDER),
            effort,
        ),
    )
    levels = tuple(
        _level(effort, {"reasoning_effort": mappings[effort]}) for effort in ordered
    )
    default = "high" if "high" in mappings else ordered[0]
    return _profile(*levels, default=default)
