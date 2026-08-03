"""Translate Codex Responses requests to a provider's OpenAI Responses API."""

import copy
import hashlib
import json
from typing import Any

from .adapter import ToolBinding
from .adapter import ToolBindings
from .catalog import OpenModel
from .errors import GatewayError
from .reasoning import reasoning_request_options

ENCRYPTED_ROUTE_PREFIX = "sudhir-codex-route-v1."


def responses_to_openai_request(
    request: object,
    model: OpenModel,
) -> tuple[dict[str, Any], ToolBindings]:
    """Build one non-streaming upstream Responses request."""

    if not isinstance(request, dict):
        raise GatewayError(
            400, "invalid_request", "Responses request must be an object"
        )
    input_items = request.get("input", [])
    if not isinstance(input_items, list):
        raise GatewayError(400, "invalid_input", "Responses input must be a list")

    bindings = ToolBindings(_available_tools(request, input_items))
    upstream_input = _translate_input(
        input_items,
        instructions=request.get("instructions"),
        bindings=bindings,
        model=model,
    )
    if not upstream_input:
        raise GatewayError(400, "empty_input", "Responses request contains no input")

    upstream: dict[str, Any] = {
        "model": model.upstream_id,
        "input": upstream_input,
        "stream": False,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    reasoning_options = reasoning_request_options(request, model)
    effort = reasoning_options.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        upstream["reasoning"] = {"effort": effort, "summary": "auto"}
    tools = [_responses_tool(binding, model) for binding in bindings.bindings]
    if tools:
        upstream["tools"] = tools
        tool_choice = request.get("tool_choice", "auto")
        if tool_choice in {"auto", "none", "required"}:
            upstream["tool_choice"] = tool_choice
        if (
            bool(request.get("parallel_tool_calls"))
            and model.compat.get("supportsParallelToolCalls", True) is not False
        ):
            upstream["parallel_tool_calls"] = True
    return upstream, bindings


def openai_response_to_sse(
    response: object,
    bindings: ToolBindings,
    model: OpenModel,
) -> bytes:
    """Convert one non-streaming Responses result to Responses SSE."""

    if not isinstance(response, dict):
        raise GatewayError(
            502, "invalid_responses_response", "Provider returned invalid JSON"
        )
    output = response.get("output")
    if not isinstance(output, list) or not output:
        raise GatewayError(
            502,
            "empty_responses_response",
            "Provider returned no Responses output",
        )
    response_id = response.get("id")
    if not isinstance(response_id, str) or not response_id:
        raise GatewayError(
            502,
            "invalid_responses_response",
            "Provider Responses result has no ID",
        )

    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": response_id}}
    ]
    translated_output = [
        translated
        for item in output
        if isinstance(item, dict)
        for translated in [_translate_output_item(item, bindings, model)]
        if translated is not None
    ]
    for item in translated_output:
        events.append({"type": "response.output_item.done", "item": item})
    if len(events) == 1:
        raise GatewayError(
            502,
            "empty_responses_response",
            "Provider returned no usable Responses output",
        )
    completed = {
        key: copy.deepcopy(value)
        for key, value in response.items()
        if key in {"id", "model", "status", "usage"}
    }
    completed["output"] = translated_output
    events.append({"type": "response.completed", "response": completed})
    return _encode_sse(events)


def _translate_input(
    input_items: list[object],
    *,
    instructions: object,
    bindings: ToolBindings,
    model: OpenModel,
) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions:
        translated.append({"role": "system", "content": instructions})

    for item in input_items:
        if not isinstance(item, dict):
            raise GatewayError(
                400, "invalid_input", "Responses input item is not an object"
            )
        kind = item.get("type")
        if kind == "message":
            translated.append(_translate_message(item, model))
        elif kind == "agent_message":
            text = "\n".join(
                part["text"]
                for part in item.get("content", [])
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
            if text:
                author = str(item.get("author", "agent"))
                translated.append(
                    {"role": "user", "content": f"[Message from {author}]\n{text}"}
                )
        elif kind == "reasoning":
            encrypted = _unwrap_encrypted_content(
                item.get("encrypted_content"),
                model,
            )
            if encrypted is not None:
                reasoning = _copy_fields(
                    item,
                    "type",
                    "id",
                    "status",
                    "summary",
                    "content",
                )
                reasoning["encrypted_content"] = encrypted
                translated.append(reasoning)
        elif kind == "compaction":
            encrypted = _unwrap_encrypted_content(
                item.get("encrypted_content"),
                model,
            )
            if encrypted is not None:
                compaction = _copy_fields(item, "type", "id")
                compaction["encrypted_content"] = encrypted
                translated.append(compaction)
        elif kind in {"function_call", "custom_tool_call", "tool_search_call"}:
            translated.append(_translate_tool_call(item, bindings))
        elif kind in {
            "function_call_output",
            "custom_tool_call_output",
            "mcp_tool_call_output",
            "tool_search_output",
        }:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise GatewayError(
                    400, "invalid_tool_output", "Tool output has no call_id"
                )
            if kind == "tool_search_output":
                output = json.dumps(
                    item.get("tools", []),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            else:
                output = _tool_output(item.get("output"))
            translated.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )
        elif kind in {
            "additional_tools",
            "compaction_trigger",
            "context_compaction",
        }:
            continue
        else:
            raise GatewayError(
                400,
                "unsupported_input_item",
                f"Responses input item type {kind!r} is unsupported",
            )
    return translated


def _available_tools(
    request: dict[str, Any],
    input_items: list[object],
) -> list[object]:
    tools = request.get("tools", [])
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        raise GatewayError(400, "invalid_tools", "Responses tools must be a list")
    available = list(tools)
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "tool_search_output":
            continue
        discovered = item.get("tools", [])
        if isinstance(discovered, list):
            available.extend(discovered)
    return available


def _translate_message(
    item: dict[str, Any],
    model: OpenModel,
) -> dict[str, Any]:
    role = item.get("role")
    if role not in {"developer", "system", "user", "assistant"}:
        role = "user"
    content = item.get("content", [])
    if isinstance(content, str):
        translated_content: str | list[dict[str, Any]] = content
    elif isinstance(content, list):
        translated_content = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in {"input_text", "output_text"} and isinstance(
                part.get("text"), str
            ):
                translated_content.append(
                    {
                        "type": (
                            "output_text" if role == "assistant" else "input_text"
                        ),
                        "text": part["text"],
                        **(
                            {"annotations": copy.deepcopy(part.get("annotations", []))}
                            if role == "assistant"
                            else {}
                        ),
                    }
                )
            elif kind == "input_image":
                if role == "assistant":
                    continue
                if "image" not in model.input_modalities:
                    raise GatewayError(
                        400,
                        "image_unsupported",
                        "Selected open model is not registered for image input",
                    )
                image_url = part.get("image_url")
                if not isinstance(image_url, str):
                    raise GatewayError(400, "invalid_image", "Image input has no URL")
                image = {"type": "input_image", "image_url": image_url}
                if part.get("detail") in {"auto", "low", "high"}:
                    image["detail"] = part["detail"]
                translated_content.append(image)
            elif kind == "input_audio":
                raise GatewayError(
                    400,
                    "audio_unsupported",
                    "OpenAI Responses audio translation is unsupported",
                )
    else:
        raise GatewayError(400, "invalid_content", "Message content must be a list")

    translated: dict[str, Any] = {
        "role": role,
        "content": translated_content,
    }
    if role == "assistant":
        translated["type"] = "message"
        translated.update(_copy_fields(item, "id", "status", "phase"))
    return translated


def _translate_tool_call(
    item: dict[str, Any],
    bindings: ToolBindings,
) -> dict[str, Any]:
    kind = item["type"]
    binding_kind = {
        "function_call": "function",
        "custom_tool_call": "custom",
        "tool_search_call": "tool_search",
    }[kind]
    namespace = item.get("namespace")
    if not isinstance(namespace, str):
        namespace = None
    name = item.get("name", "tool_search")
    if not isinstance(name, str):
        name = "tool_search"
    binding = bindings.for_original(namespace, name, binding_kind)
    if binding is None:
        raise GatewayError(
            400,
            "unknown_history_tool",
            f"History references unknown tool {name!r}",
        )
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise GatewayError(400, "invalid_tool_call", "Tool call has no call_id")
    if kind == "custom_tool_call":
        arguments = json.dumps(
            {"input": str(item.get("input", ""))},
            separators=(",", ":"),
        )
    else:
        arguments = item.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, separators=(",", ":"))
    translated = {
        "type": "function_call",
        "call_id": call_id,
        "name": binding.encoded_name,
        "arguments": arguments,
        "status": "completed",
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id.startswith("fc_") and len(item_id) <= 64:
        translated["id"] = item_id
    return translated


def _responses_tool(binding: ToolBinding, model: OpenModel) -> dict[str, Any]:
    definition = binding.as_chat_tool()["function"]
    parameters = copy.deepcopy(definition["parameters"])
    if (
        model.provider_id == "deepseek"
        and "type" not in parameters
        and isinstance(parameters.get("oneOf"), list)
    ):
        parameters["type"] = "object"
    return {
        "type": "function",
        "name": definition["name"],
        "description": definition["description"],
        "parameters": parameters,
        "strict": False,
    }


def _translate_output_item(
    item: dict[str, Any],
    bindings: ToolBindings,
    model: OpenModel,
) -> dict[str, Any] | None:
    if item.get("type") in {"reasoning", "message", "compaction"}:
        translated = copy.deepcopy(item)
        encrypted = translated.get("encrypted_content")
        if isinstance(encrypted, str) and encrypted:
            translated["encrypted_content"] = _wrap_encrypted_content(
                encrypted,
                model,
            )
        return translated
    if item.get("type") != "function_call":
        return None

    encoded_name = item.get("name")
    if not isinstance(encoded_name, str):
        raise GatewayError(502, "invalid_tool_call", "Provider tool call has no name")
    binding = bindings.for_encoded(encoded_name)
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise GatewayError(
            502, "invalid_tool_call", "Provider tool call has no call_id"
        )
    arguments = item.get("arguments", "{}")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, separators=(",", ":"))
    translated = _copy_fields(item, "id", "status")
    translated["call_id"] = call_id

    if binding.kind == "custom":
        raw_input = arguments
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, dict) and isinstance(decoded.get("input"), str):
                raw_input = decoded["input"]
        except json.JSONDecodeError:
            pass
        translated.update(
            {
                "type": "custom_tool_call",
                "name": binding.name,
                "input": raw_input,
            }
        )
    elif binding.kind == "tool_search":
        try:
            decoded_arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise GatewayError(
                502,
                "invalid_tool_call",
                "Provider returned invalid tool_search arguments",
            ) from exc
        translated.update(
            {
                "type": "tool_search_call",
                "execution": "client",
                "arguments": decoded_arguments,
            }
        )
    else:
        translated.update(
            {
                "type": "function_call",
                "name": binding.name,
                "arguments": arguments,
            }
        )
    if binding.namespace:
        translated["namespace"] = binding.namespace
    return translated


def _tool_output(output: object) -> str | list[dict[str, Any]]:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return copy.deepcopy(output)
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))


def _copy_fields(source: dict[str, Any], *names: str) -> dict[str, Any]:
    return {
        name: copy.deepcopy(source[name])
        for name in names
        if source.get(name) is not None
    }


def _wrap_encrypted_content(encrypted: str, model: OpenModel) -> str:
    return f"{ENCRYPTED_ROUTE_PREFIX}{_route_tag(model)}.{encrypted}"


def _unwrap_encrypted_content(
    encrypted: object,
    model: OpenModel,
) -> str | None:
    if not isinstance(encrypted, str) or not encrypted.startswith(
        ENCRYPTED_ROUTE_PREFIX
    ):
        return None
    tagged = encrypted.removeprefix(ENCRYPTED_ROUTE_PREFIX)
    tag, separator, payload = tagged.partition(".")
    if separator and tag == _route_tag(model) and payload:
        return payload
    return None


def _route_tag(model: OpenModel) -> str:
    identity = "\0".join(
        (
            model.provider_id,
            model.upstream_id,
            model.api,
            model.base_url,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _encode_sse(events: list[dict[str, Any]]) -> bytes:
    chunks = []
    for event in events:
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        chunks.append(f"event: {event['type']}\ndata: {data}\n\n")
    return "".join(chunks).encode("utf-8")
