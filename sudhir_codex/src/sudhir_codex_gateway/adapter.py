"""Translate Codex Responses requests to OpenAI-compatible chat completions."""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .catalog import OpenModel
from .errors import GatewayError
from .reasoning import reasoning_request_options

FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
IGNORED_INPUT_TYPES = {
    "reasoning",
    "compaction",
    "context_compaction",
    "compaction_trigger",
}


@dataclass(frozen=True)
class ToolBinding:
    encoded_name: str
    kind: str
    name: str
    namespace: str | None
    description: str
    parameters: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name

    def as_chat_tool(self) -> dict[str, Any]:
        description = self.description
        if self.qualified_name != self.encoded_name:
            description = f"Codex tool `{self.qualified_name}`. {description}".strip()
        return {
            "type": "function",
            "function": {
                "name": self.encoded_name,
                "description": description,
                "parameters": self.parameters,
            },
        }


class ToolBindings:
    """A reversible mapping between Responses and Chat tool names."""

    def __init__(self, tools: object) -> None:
        self.bindings: list[ToolBinding] = []
        self.by_encoded: dict[str, ToolBinding] = {}
        self.by_original: dict[tuple[str | None, str, str], ToolBinding] = {}
        self._parse(tools)

    def chat_tools(self) -> list[dict[str, Any]]:
        return [binding.as_chat_tool() for binding in self.bindings]

    def for_encoded(self, name: str) -> ToolBinding:
        try:
            return self.by_encoded[name]
        except KeyError as exc:
            raise GatewayError(
                502,
                "unknown_upstream_tool",
                f"Model called an unknown translated tool {name!r}",
            ) from exc

    def for_original(
        self,
        namespace: str | None,
        name: str,
        kind: str = "function",
    ) -> ToolBinding | None:
        return self.by_original.get((namespace, name, kind))

    def _parse(self, tools: object) -> None:
        if tools is None:
            return
        if not isinstance(tools, list):
            raise GatewayError(400, "invalid_tools", "Responses tools must be a list")
        for tool in tools:
            if not isinstance(tool, dict):
                raise GatewayError(
                    400, "invalid_tool", "Responses tool must be an object"
                )
            kind = tool.get("type")
            if kind == "function":
                self._add_function(tool, namespace=None, binding_kind="function")
            elif kind == "custom":
                self._add_custom(tool, namespace=None)
            elif kind == "namespace":
                namespace = _required_string(tool, "name", "namespace tool")
                children = tool.get("tools")
                if not isinstance(children, list):
                    raise GatewayError(
                        400,
                        "invalid_tool",
                        f"Namespace {namespace!r} must contain a tools list",
                    )
                for child in children:
                    if not isinstance(child, dict) or child.get("type") != "function":
                        raise GatewayError(
                            400,
                            "unsupported_tool",
                            f"Namespace {namespace!r} contains a non-function tool",
                        )
                    self._add_function(
                        child, namespace=namespace, binding_kind="function"
                    )
            elif kind == "tool_search":
                parameters = _schema(tool.get("parameters"))
                name = "tool_search"
                self._add(
                    kind="tool_search",
                    name=name,
                    namespace=None,
                    description=str(
                        tool.get("description", "Search available Codex tools.")
                    ),
                    parameters=parameters,
                )
            elif kind == "web_search":
                raise GatewayError(
                    400,
                    "unsupported_tool",
                    "Provider-hosted web_search is unavailable for translated open models",
                )
            else:
                raise GatewayError(
                    400,
                    "unsupported_tool",
                    f"Responses tool type {kind!r} is unsupported by the chat adapter",
                )

    def _add_function(
        self,
        tool: dict[str, Any],
        namespace: str | None,
        binding_kind: str,
    ) -> None:
        self._add(
            kind=binding_kind,
            name=_required_string(tool, "name", "function tool"),
            namespace=namespace,
            description=str(tool.get("description", "")),
            parameters=_schema(tool.get("parameters")),
        )

    def _add_custom(self, tool: dict[str, Any], namespace: str | None) -> None:
        self._add(
            kind="custom",
            name=_required_string(tool, "name", "custom tool"),
            namespace=namespace,
            description=str(tool.get("description", "")),
            parameters={
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Raw input for the Codex custom tool.",
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        )

    def _add(
        self,
        *,
        kind: str,
        name: str,
        namespace: str | None,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        original = (namespace, name, kind)
        if original in self.by_original:
            return
        qualified = f"{namespace}__{name}" if namespace else name
        encoded = _encoded_tool_name(qualified, len(self.bindings))
        while encoded in self.by_encoded:
            encoded = _encoded_tool_name(
                f"{qualified}_{len(self.bindings)}", len(self.bindings)
            )
        binding = ToolBinding(
            encoded_name=encoded,
            kind=kind,
            name=name,
            namespace=namespace,
            description=description,
            parameters=parameters,
        )
        self.bindings.append(binding)
        self.by_encoded[encoded] = binding
        self.by_original[original] = binding


def responses_to_chat_request(
    request: object,
    model: OpenModel,
) -> tuple[dict[str, Any], ToolBindings]:
    """Convert one Codex Responses body to a buffered chat request."""

    if not isinstance(request, dict):
        raise GatewayError(
            400, "invalid_request", "Responses request must be an object"
        )
    bindings = ToolBindings(request.get("tools"))
    messages = _translate_input(
        request.get("input", []),
        instructions=request.get("instructions"),
        bindings=bindings,
        model=model,
    )
    if not messages:
        raise GatewayError(
            400, "empty_input", "Responses request contains no chat input"
        )

    chat_request: dict[str, Any] = {
        "model": model.upstream_id,
        "messages": messages,
        "stream": False,
    }
    chat_tools = bindings.chat_tools()
    if chat_tools:
        chat_request["tools"] = chat_tools
        tool_choice = request.get("tool_choice", "auto")
        if tool_choice in {"auto", "none", "required"}:
            chat_request["tool_choice"] = tool_choice
        if (
            bool(request.get("parallel_tool_calls"))
            and model.compat.get("supportsParallelToolCalls", True) is not False
        ):
            chat_request["parallel_tool_calls"] = True

    chat_request.update(reasoning_request_options(request, model))
    return chat_request, bindings


def chat_response_to_sse(
    response: object,
    bindings: ToolBindings,
) -> bytes:
    """Convert one non-streaming Chat Completions result to Responses SSE."""

    if not isinstance(response, dict):
        raise GatewayError(
            502, "invalid_chat_response", "Provider returned invalid JSON"
        )
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise GatewayError(
            502,
            "invalid_chat_response",
            "Provider response contains no completion choice",
        )
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise GatewayError(
            502,
            "invalid_chat_response",
            "Provider response contains no assistant message",
        )

    response_id = f"resp_sudhir_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": response_id}}
    ]
    output_count = 0
    text = _chat_content_text(message.get("content"))
    if text:
        events.append(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": f"msg_{uuid.uuid4().hex}",
                    "content": [{"type": "output_text", "text": text}],
                },
            }
        )
        output_count += 1

    tool_calls = message.get("tool_calls")
    if tool_calls is None and isinstance(message.get("function_call"), dict):
        tool_calls = [
            {
                "id": f"call_{uuid.uuid4().hex}",
                "type": "function",
                "function": message["function_call"],
            }
        ]
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            raise GatewayError(
                502,
                "invalid_chat_response",
                "Provider returned invalid tool_calls",
            )
        for call in tool_calls:
            events.append(_tool_call_event(call, bindings))
            output_count += 1
    if output_count == 0:
        raise GatewayError(
            502,
            "empty_chat_response",
            "Provider returned neither text nor tool calls",
        )

    usage = _responses_usage(response.get("usage"))
    events.append(
        {
            "type": "response.completed",
            "response": {"id": response_id, "usage": usage},
        }
    )
    return _encode_sse(events)


def _translate_input(
    input_items: object,
    *,
    instructions: object,
    bindings: ToolBindings,
    model: OpenModel,
) -> list[dict[str, Any]]:
    if not isinstance(input_items, list):
        raise GatewayError(400, "invalid_input", "Responses input must be a list")
    messages: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})

    pending_calls: list[dict[str, Any]] = []
    call_bindings: dict[str, ToolBinding] = {}

    def flush_pending() -> None:
        if not pending_calls:
            return
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": list(pending_calls),
        }
        if model.compat.get("requiresReasoningContentOnAssistantMessages"):
            assistant["reasoning_content"] = ""
        messages.append(assistant)
        pending_calls.clear()

    for item in input_items:
        if not isinstance(item, dict):
            raise GatewayError(
                400, "invalid_input", "Responses input item is not an object"
            )
        kind = item.get("type")
        if kind in {"function_call", "custom_tool_call", "tool_search_call"}:
            binding_kind = {
                "function_call": "function",
                "custom_tool_call": "custom",
                "tool_search_call": "tool_search",
            }[kind]
            namespace = item.get("namespace")
            if not isinstance(namespace, str):
                namespace = None
            name = str(item.get("name", "tool_search"))
            binding = bindings.for_original(namespace, name, binding_kind)
            if binding is None:
                raise GatewayError(
                    400,
                    "unknown_history_tool",
                    f"History references unknown tool {name!r}",
                )
            call_id = str(item.get("call_id") or f"call_{uuid.uuid4().hex}")
            arguments = item.get("arguments", "{}")
            if kind == "custom_tool_call":
                arguments = json.dumps({"input": str(item.get("input", ""))})
            elif kind == "tool_search_call" and not isinstance(arguments, str):
                arguments = json.dumps(arguments, separators=(",", ":"))
            elif not isinstance(arguments, str):
                arguments = json.dumps(arguments, separators=(",", ":"))
            pending_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": binding.encoded_name,
                        "arguments": arguments,
                    },
                }
            )
            call_bindings[call_id] = binding
            continue

        flush_pending()
        if kind == "message":
            role = str(item.get("role", "user"))
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant"}:
                role = "user"
            content = _response_content_to_chat(
                item.get("content", []),
                allow_images="image" in model.input_modalities,
                assistant=role == "assistant",
            )
            message: dict[str, Any] = {"role": role, "content": content}
            if role == "assistant" and model.compat.get(
                "requiresReasoningContentOnAssistantMessages"
            ):
                message["reasoning_content"] = ""
            messages.append(message)
        elif kind == "agent_message":
            text_parts = []
            for part in item.get("content", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            if text_parts:
                author = str(item.get("author", "agent"))
                messages.append(
                    {
                        "role": "user",
                        "content": f"[Message from {author}]\n" + "\n".join(text_parts),
                    }
                )
        elif kind in {
            "function_call_output",
            "custom_tool_call_output",
            "mcp_tool_call_output",
            "tool_search_output",
        }:
            call_id = str(item.get("call_id") or "")
            if not call_id:
                raise GatewayError(
                    400, "invalid_tool_output", "Tool output has no call_id"
                )
            output = item.get("output")
            if kind == "tool_search_output":
                output = item.get("tools", [])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_output_text(output),
                }
            )
            call_bindings.pop(call_id, None)
        elif kind in IGNORED_INPUT_TYPES:
            continue
        elif kind == "additional_tools":
            continue
        else:
            raise GatewayError(
                400,
                "unsupported_input_item",
                f"Responses input item type {kind!r} is unsupported",
            )
    flush_pending()
    return messages


def _response_content_to_chat(
    content: object,
    *,
    allow_images: bool,
    assistant: bool,
) -> str | list[dict[str, Any]]:
    if not isinstance(content, list):
        raise GatewayError(400, "invalid_content", "Message content must be a list")
    text_parts: list[str] = []
    multimodal: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in {"input_text", "output_text"} and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
            multimodal.append({"type": "text", "text": part["text"]})
        elif kind == "input_image":
            if assistant:
                continue
            if not allow_images:
                raise GatewayError(
                    400,
                    "image_unsupported",
                    "Selected open model is not registered for image input",
                )
            image_url = part.get("image_url")
            if not isinstance(image_url, str):
                raise GatewayError(400, "invalid_image", "Image input has no URL")
            image: dict[str, Any] = {"url": image_url}
            detail = part.get("detail")
            if detail in {"auto", "low", "high"}:
                image["detail"] = detail
            multimodal.append({"type": "image_url", "image_url": image})
        elif kind == "input_audio":
            raise GatewayError(
                400,
                "audio_unsupported",
                "Generic Chat Completions audio translation is unsupported",
            )
    has_non_text = any(part.get("type") != "text" for part in multimodal)
    if not has_non_text:
        return "\n".join(text_parts)
    return multimodal


def _tool_call_event(call: object, bindings: ToolBindings) -> dict[str, Any]:
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        raise GatewayError(
            502, "invalid_tool_call", "Provider returned an invalid tool call"
        )
    function = call["function"]
    encoded_name = function.get("name")
    if not isinstance(encoded_name, str):
        raise GatewayError(502, "invalid_tool_call", "Provider tool call has no name")
    binding = bindings.for_encoded(encoded_name)
    call_id = str(call.get("id") or f"call_{uuid.uuid4().hex}")
    arguments = function.get("arguments", "{}")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, separators=(",", ":"))

    if binding.kind == "custom":
        raw_input = arguments
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, dict) and isinstance(decoded.get("input"), str):
                raw_input = decoded["input"]
        except json.JSONDecodeError:
            pass
        item: dict[str, Any] = {
            "type": "custom_tool_call",
            "call_id": call_id,
            "name": binding.name,
            "input": raw_input,
        }
    elif binding.kind == "tool_search":
        try:
            decoded_arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise GatewayError(
                502,
                "invalid_tool_call",
                "Provider returned invalid tool_search arguments",
            ) from exc
        item = {
            "type": "tool_search_call",
            "call_id": call_id,
            "status": "completed",
            "execution": "client",
            "arguments": decoded_arguments,
        }
    else:
        item = {
            "type": "function_call",
            "call_id": call_id,
            "name": binding.name,
            "arguments": arguments,
        }
    if binding.namespace:
        item["namespace"] = binding.namespace
    return {"type": "response.output_item.done", "item": item}


def _responses_usage(usage: object) -> dict[str, Any]:
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _nonnegative_int(
        usage.get("prompt_tokens", usage.get("input_tokens", 0))
    )
    output_tokens = _nonnegative_int(
        usage.get("completion_tokens", usage.get("output_tokens", 0))
    )
    total_tokens = _nonnegative_int(
        usage.get("total_tokens", input_tokens + output_tokens)
    )
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": None,
        "output_tokens": output_tokens,
        "output_tokens_details": None,
        "total_tokens": total_tokens,
    }


def _chat_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def _tool_output_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        text_parts = [
            item.get("text", "")
            for item in output
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if text_parts:
            return "\n".join(text_parts)
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))


def _encode_sse(events: list[dict[str, Any]]) -> bytes:
    chunks = []
    for event in events:
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        chunks.append(f"event: {event['type']}\ndata: {data}\n\n")
    return "".join(chunks).encode("utf-8")


def _encoded_tool_name(qualified: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", qualified).strip("_")
    if not cleaned:
        cleaned = f"tool_{index}"
    if FUNCTION_NAME.fullmatch(cleaned):
        return cleaned
    digest = hashlib.sha256(qualified.encode()).hexdigest()[:8]
    return f"{cleaned[:54]}_{digest}"[:64]


def _schema(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return _chat_schema_value(value)
    return {"type": "object", "properties": {}}


def _chat_schema_value(value: Any) -> Any:
    """Copy a Responses schema while dropping markers unsupported by Chat Completions."""

    if isinstance(value, dict):
        return {
            key: _chat_schema_value(child)
            for key, child in value.items()
            if key != "encrypted"
        }
    if isinstance(value, list):
        return [_chat_schema_value(child) for child in value]
    return value


def _required_string(value: dict[str, Any], key: str, context: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise GatewayError(400, "invalid_tool", f"{context} has no {key}")
    return result


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
