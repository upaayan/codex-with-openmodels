"""Translate buffered Chat Completions payloads to Anthropic Messages."""

import hashlib
import json
import re
from typing import Any

from .catalog import OpenModel
from .errors import GatewayError

ANTHROPIC_VERSION = "2023-06-01"
DATA_IMAGE = re.compile(r"^data:([^;,]+);base64,(.+)$", re.DOTALL)
TOOL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def chat_request_to_anthropic(
    request: object,
    model: OpenModel,
) -> dict[str, Any]:
    """Convert the gateway's neutral Chat request to one Messages request."""

    if not isinstance(request, dict):
        raise GatewayError(
            500,
            "invalid_anthropic_request",
            "Translated Anthropic request must be an object",
        )
    messages = request.get("messages")
    if not isinstance(messages, list):
        raise GatewayError(
            500,
            "invalid_anthropic_request",
            "Translated Anthropic request has no messages list",
        )

    system_parts: list[str] = []
    translated: list[dict[str, Any]] = []
    tool_ids: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            raise GatewayError(
                400,
                "invalid_anthropic_message",
                "Chat history contains a non-object message",
            )
        role = message.get("role")
        if role == "system":
            text = _content_text(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "user":
            blocks = _user_blocks(message.get("content"))
            if blocks:
                _append_message(translated, "user", blocks)
            continue
        if role == "assistant":
            blocks = _assistant_blocks(message, tool_ids)
            if blocks:
                _append_message(translated, "assistant", blocks)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise GatewayError(
                    400,
                    "invalid_anthropic_tool_result",
                    "Tool result has no tool_call_id",
                )
            normalized_id = tool_ids.setdefault(call_id, _tool_id(call_id))
            block = {
                "type": "tool_result",
                "tool_use_id": normalized_id,
                "content": _content_text(message.get("content")),
            }
            _append_message(translated, "user", [block])
            continue
        raise GatewayError(
            400,
            "unsupported_anthropic_role",
            f"Chat role {role!r} cannot be sent to the Messages endpoint",
        )

    if not translated:
        raise GatewayError(
            400,
            "empty_anthropic_input",
            "Anthropic Messages request contains no conversation input",
        )

    output: dict[str, Any] = {
        "model": model.upstream_id,
        "messages": translated,
        "max_tokens": model.max_tokens or 8192,
        "stream": False,
    }
    if system_parts:
        output["system"] = "\n\n".join(system_parts)

    tools = _anthropic_tools(request.get("tools"))
    if tools:
        output["tools"] = tools
        choice = request.get("tool_choice")
        if choice == "required":
            output["tool_choice"] = {"type": "any"}
        elif choice in {"auto", "none"}:
            output["tool_choice"] = {"type": choice}

    thinking = request.get("thinking")
    if isinstance(thinking, dict):
        output["thinking"] = thinking
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int) and budget >= output["max_tokens"]:
            raise GatewayError(
                500,
                "invalid_anthropic_thinking_budget",
                "Thinking budget must be smaller than max_tokens",
            )
    return output


def anthropic_response_to_chat(response: object) -> dict[str, Any]:
    """Convert one non-streaming Messages result to the existing Chat shape."""

    if not isinstance(response, dict) or response.get("type") != "message":
        raise GatewayError(
            502,
            "invalid_anthropic_response",
            "Provider returned an invalid Anthropic message",
        )
    content = response.get("content")
    if not isinstance(content, list):
        raise GatewayError(
            502,
            "invalid_anthropic_response",
            "Provider returned no Anthropic content blocks",
        )

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block_type == "tool_use":
            name = block.get("name")
            if not isinstance(name, str) or not name:
                raise GatewayError(
                    502,
                    "invalid_anthropic_tool_call",
                    "Provider returned a tool call without a name",
                )
            tool_calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            block.get("input", {}),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = sum(
        _nonnegative_int(usage.get(field))
        for field in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    output_tokens = _nonnegative_int(usage.get("output_tokens"))
    return {
        "id": response.get("id"),
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _assistant_blocks(
    message: dict[str, Any],
    tool_ids: dict[str, str],
) -> list[dict[str, Any]]:
    blocks = _text_blocks(message.get("content"))
    calls = message.get("tool_calls")
    if calls is None and isinstance(message.get("function_call"), dict):
        calls = [
            {
                "id": "",
                "type": "function",
                "function": message["function_call"],
            }
        ]
    if calls is None:
        return blocks
    if not isinstance(calls, list):
        raise GatewayError(
            400,
            "invalid_anthropic_tool_call",
            "Assistant tool_calls must be a list",
        )
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise GatewayError(
                400,
                "invalid_anthropic_tool_call",
                "Assistant history contains an invalid tool call",
            )
        function = call["function"]
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise GatewayError(
                400,
                "invalid_anthropic_tool_call",
                "Assistant tool call has no name",
            )
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise GatewayError(
                    400,
                    "invalid_anthropic_tool_arguments",
                    f"Tool call {name!r} has invalid JSON arguments",
                ) from exc
        if not isinstance(arguments, dict):
            raise GatewayError(
                400,
                "invalid_anthropic_tool_arguments",
                f"Tool call {name!r} arguments must be an object",
            )
        original_id = str(call.get("id") or f"call_{len(tool_ids)}")
        normalized_id = tool_ids.setdefault(original_id, _tool_id(original_id))
        blocks.append(
            {
                "type": "tool_use",
                "id": normalized_id,
                "name": name,
                "input": arguments,
            }
        )
    return blocks


def _anthropic_tools(tools: object) -> list[dict[str, Any]]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise GatewayError(
            500,
            "invalid_anthropic_tools",
            "Translated tools must be a list",
        )
    translated: list[dict[str, Any]] = []
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or tool.get("type") != "function"
            or not isinstance(tool.get("function"), dict)
        ):
            raise GatewayError(
                500,
                "invalid_anthropic_tool",
                "Translated Anthropic tools must use Chat function format",
            )
        function = tool["function"]
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise GatewayError(
                500,
                "invalid_anthropic_tool",
                "Translated Anthropic tool has no name",
            )
        schema = function.get("parameters", {"type": "object", "properties": {}})
        if not isinstance(schema, dict):
            raise GatewayError(
                500,
                "invalid_anthropic_tool",
                f"Translated Anthropic tool {name!r} has an invalid schema",
            )
        translated.append(
            {
                "name": name,
                "description": str(function.get("description", "")),
                "input_schema": schema,
            }
        )
    return translated


def _user_blocks(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            if item:
                blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            blocks.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image_url":
            image = item.get("image_url")
            image_url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(image_url, str) or not image_url:
                raise GatewayError(
                    400,
                    "invalid_anthropic_image",
                    "Image input has no URL",
                )
            match = DATA_IMAGE.fullmatch(image_url)
            if match:
                source = {
                    "type": "base64",
                    "media_type": match.group(1),
                    "data": match.group(2),
                }
            elif image_url.startswith(("https://", "http://")):
                source = {"type": "url", "url": image_url}
            else:
                raise GatewayError(
                    400,
                    "invalid_anthropic_image",
                    "Anthropic image URL must use HTTP(S) or a base64 data URL",
                )
            blocks.append({"type": "image", "source": source})
    return blocks


def _text_blocks(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    return [
        {"type": "text", "text": item["text"]}
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
        and item["text"]
    ]


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in _text_blocks(content))


def _append_message(
    messages: list[dict[str, Any]],
    role: str,
    blocks: list[dict[str, Any]],
) -> None:
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": list(blocks)})


def _tool_id(value: str) -> str:
    if TOOL_ID.fullmatch(value):
        return value
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "call"
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{cleaned[:54]}_{digest}"[:64]


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
