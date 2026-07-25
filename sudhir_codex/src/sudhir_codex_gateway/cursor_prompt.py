"""Turn a Codex Responses request into one Cursor-native agent prompt."""

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GatewayError

CWD_PATTERN = re.compile(r"<cwd>(.*?)</cwd>", re.DOTALL)
IGNORED_ITEMS = {
    "additional_tools",
    "compaction_trigger",
    "context_compaction",
    "reasoning",
}


@dataclass(frozen=True)
class CursorTurn:
    cwd: Path
    prompt: str


def build_cursor_turn(
    request: dict[str, Any],
    *,
    fallback_cwd: Path | None = None,
) -> CursorTurn:
    """Build a self-contained prompt and require a verified working directory."""

    input_items = request.get("input", [])
    if not isinstance(input_items, list):
        raise GatewayError(400, "invalid_input", "Responses input must be a list")

    transcript: list[str] = []
    instructions = request.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        transcript.append(f"SYSTEM INSTRUCTIONS:\n{instructions}")

    candidate_cwds: list[str] = []
    for item in input_items:
        if not isinstance(item, dict):
            raise GatewayError(
                400,
                "invalid_input",
                "Responses input item is not an object",
            )
        rendered, _searchable_text = _render_item(item)
        if rendered:
            transcript.append(rendered)
        for context_text in _environment_context_texts(item):
            for match in CWD_PATTERN.finditer(context_text):
                candidate_cwds.append(html.unescape(match.group(1).strip()))

    cwd = _verified_cwd(candidate_cwds, fallback_cwd)
    if not transcript:
        raise GatewayError(
            400,
            "empty_input",
            "Responses request contains no Cursor prompt input",
        )

    prompt = (
        "The following is the current Codex task transcript. Continue the task "
        "from its latest state. Use your native Cursor tools when useful.\n\n"
        + "\n\n".join(transcript)
    )
    return CursorTurn(cwd=cwd, prompt=prompt)


def _verified_cwd(candidates: list[str], fallback: Path | None) -> Path:
    values: list[Path] = []
    for candidate in reversed(candidates):
        if candidate:
            values.append(Path(candidate).expanduser())
    if fallback is not None:
        values.append(fallback)

    for value in values:
        if value.is_absolute() and value.is_dir():
            return value
    raise GatewayError(
        400,
        "cursor_cwd_missing",
        (
            "Cursor requires a verified working directory from the task's "
            "environment context"
        ),
    )


def _environment_context_texts(item: dict[str, Any]) -> tuple[str, ...]:
    if item.get("type") != "message" or item.get("role") != "user":
        return ()
    content = item.get("content")
    if not isinstance(content, list):
        return ()
    contexts: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "input_text":
            continue
        text = part.get("text")
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if stripped.startswith("<environment_context>") and stripped.endswith(
            "</environment_context>"
        ):
            contexts.append(stripped)
    return tuple(contexts)


def _render_item(item: dict[str, Any]) -> tuple[str, str]:
    kind = item.get("type")
    if kind == "message":
        role = str(item.get("role", "user")).upper()
        text = _content_text(item.get("content"))
        return (f"{role}:\n{text}" if text else "", text)
    if kind == "agent_message":
        author = str(item.get("author", "agent"))
        text = _content_text(item.get("content"))
        return (f"AGENT MESSAGE FROM {author}:\n{text}" if text else "", text)
    if kind in {"function_call", "custom_tool_call", "tool_search_call"}:
        name = str(item.get("name", "tool_search"))
        call_id = str(item.get("call_id", ""))
        arguments = item.get("arguments", item.get("input", ""))
        rendered = _json_text(arguments)
        return f"TOOL CALL {name} {call_id}:\n{rendered}", rendered
    if kind == "mcp_tool_call":
        name = str(item.get("name", "mcp_tool"))
        server = str(item.get("server_label", item.get("server", "")))
        arguments = _json_text(item.get("arguments", {}))
        return f"MCP TOOL CALL {server}/{name}:\n{arguments}", arguments
    if kind in {
        "function_call_output",
        "custom_tool_call_output",
        "mcp_tool_call_output",
        "tool_search_output",
    }:
        call_id = str(item.get("call_id", ""))
        output = item.get("output", item.get("tools", ""))
        rendered = _json_text(output)
        return f"TOOL RESULT {call_id}:\n{rendered}", rendered
    if kind == "compaction":
        text = _content_text(item.get("content", item.get("summary", "")))
        return (f"COMPACTED CONTEXT:\n{text}" if text else "", text)
    if kind in IGNORED_ITEMS:
        return "", ""
    # Preserve future text-bearing input types instead of silently losing context.
    text = _content_text(item.get("content", ""))
    return (f"{str(kind).upper()}:\n{text}" if text else "", text)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
            continue
        if part.get("type") == "input_image":
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                parts.append(f"[Image input: {image_url}]")
    return "\n".join(parts)


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)
