"""Opt-in, content-redacted DeepSeek gateway boundary diagnostics."""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file

CAPTURE_ENV = "SUDHIR_CODEX_DEEPSEEK_CAPTURE"
CAPTURE_FILENAME = "deepseek-v4-diagnostic.jsonl"
CAPTURE_MARKER_FILENAME = "deepseek-v4-diagnostic.enabled"
TARGET_MODEL_IDS = {
    "pi-deepseek/deepseek-v4-flash",
    "pi-opencode-go/deepseek-v4-flash",
}
SAFE_RESPONSE_HEADERS = {
    "content-encoding",
    "content-length",
    "content-type",
    "request-id",
    "x-request-id",
}
ENUM_FIELDS = {
    "finish_reason",
    "object",
    "role",
    "status",
    "stop_reason",
    "type",
}
MAX_COLLECTION_ITEMS = 32
MAX_DEPTH = 8


class DeepSeekDiagnosticCapture:
    """Append redacted request/response structure without affecting routing."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._sequence: dict[str, int] = {}

    @classmethod
    def from_environment(cls, gateway_state_dir: Path) -> "DeepSeekDiagnosticCapture":
        marker_path = gateway_state_dir / CAPTURE_MARKER_FILENAME
        enabled = os.environ.get(CAPTURE_ENV) == "1" or marker_path.is_file()
        path = gateway_state_dir / CAPTURE_FILENAME if enabled else None
        capture = cls(path)
        capture.record_ready()
        return capture

    def record_ready(self) -> None:
        if self.path is None:
            return
        entry = {
            "schema": 1,
            "event": "ready",
            "timestamp_ns": time.time_ns(),
            "pid": os.getpid(),
            "target_model_ids": sorted(TARGET_MODEL_IDS),
        }
        with self._lock:
            self._append_unlocked(entry)

    def record_upstream(
        self,
        *,
        model_id: str,
        provider_id: str,
        upstream_model_id: str,
        api: str,
        request: object,
        response: object,
        response_body: bytes,
        response_headers: object,
        known_tool_names: set[str] | frozenset[str],
    ) -> str | None:
        if self.path is None or model_id not in TARGET_MODEL_IDS:
            return None
        capture_id = uuid.uuid4().hex
        with self._lock:
            sequence = self._sequence.get(model_id, 0) + 1
            self._sequence[model_id] = sequence
            entry = {
                "schema": 1,
                "event": "upstream",
                "timestamp_ns": time.time_ns(),
                "capture_id": capture_id,
                "sequence": sequence,
                "model_id": model_id,
                "provider_id": provider_id,
                "upstream_model_id": upstream_model_id,
                "api": api,
                "request": _request_summary(request),
                "upstream_http": {
                    "body": _bytes_summary(response_body),
                    "headers": _safe_headers(response_headers),
                },
                "response": _response_summary(response, known_tool_names),
            }
            if not self._append_unlocked(entry):
                return None
        return capture_id

    def record_adapter(
        self,
        capture_id: str | None,
        *,
        sse: bytes | None = None,
        error: Exception | None = None,
    ) -> None:
        if self.path is None or capture_id is None:
            return
        entry: dict[str, Any] = {
            "schema": 1,
            "event": "adapter",
            "timestamp_ns": time.time_ns(),
            "capture_id": capture_id,
        }
        if sse is not None:
            entry["result"] = _sse_summary(sse)
        if error is not None:
            failure: dict[str, Any] = {"exception_type": type(error).__name__}
            status = getattr(error, "status", None)
            code = getattr(error, "code", None)
            if isinstance(status, int):
                failure["status"] = status
            if isinstance(code, str):
                failure["code"] = code
            entry["error"] = failure
        with self._lock:
            self._append_unlocked(entry)

    def _append_unlocked(self, entry: dict[str, Any]) -> bool:
        if self.path is None:
            return False
        try:
            ensure_private_directory(self.path.parent)
            if self.path.is_symlink():
                return False
            encoded = (
                json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    view = view[os.write(fd, view) :]
            finally:
                os.close(fd)
            ensure_private_file(self.path)
            return True
        except (OSError, TypeError, ValueError):
            return False


def _request_summary(request: object) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {"type": type(request).__name__}
    encoded = _json_bytes(request)
    messages = request.get("messages")
    if not isinstance(messages, list):
        messages = []
    roles: dict[str, int] = {}
    assistant_turns: list[dict[str, Any]] = []
    assistant_pairs = 0
    previous_role: str | None = None
    classifications = {
        "text_only": 0,
        "tool_only": 0,
        "text_and_tools": 0,
        "empty": 0,
    }
    reasoning_presence: dict[str, int] = {}
    reasoning_nonempty: dict[str, int] = {}
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            previous_role = None
            continue
        role = message.get("role")
        role_name = role if isinstance(role, str) else type(role).__name__
        roles[role_name] = roles.get(role_name, 0) + 1
        if role == "assistant" and previous_role == "assistant":
            assistant_pairs += 1
        previous_role = role if isinstance(role, str) else None
        if role != "assistant":
            continue
        has_text = _has_text(message.get("content"))
        has_tools = _has_calls(message)
        if has_text and has_tools:
            classification = "text_and_tools"
        elif has_text:
            classification = "text_only"
        elif has_tools:
            classification = "tool_only"
        else:
            classification = "empty"
        classifications[classification] += 1
        reasoning_fields = {
            key: _shape(value, key=key)
            for key, value in message.items()
            if _is_reasoning_key(str(key))
        }
        for key, value in reasoning_fields.items():
            reasoning_presence[key] = reasoning_presence.get(key, 0) + 1
            if _shape_is_nonempty(value):
                reasoning_nonempty[key] = reasoning_nonempty.get(key, 0) + 1
        assistant_turns.append(
            {
                "message_index": index,
                "classification": classification,
                "fields": sorted(str(key) for key in message),
                "content": _shape(message.get("content"), key="content"),
                "reasoning_fields": reasoning_fields,
                "tool_calls": _calls_summary(message.get("tool_calls"), set()),
                "legacy_function_call": _shape(
                    message.get("function_call"), key="function_call"
                ),
            }
        )
    tools = request.get("tools")
    options = {
        key: _shape(request[key], key=key)
        for key in (
            "max_tokens",
            "parallel_tool_calls",
            "reasoning_effort",
            "stream",
            "thinking",
            "tool_choice",
        )
        if key in request
    }
    return {
        "json": _bytes_summary(encoded),
        "root_fields": sorted(str(key) for key in request),
        "message_count": len(messages),
        "roles": roles,
        "assistant_classifications": classifications,
        "assistant_to_assistant_pairs": assistant_pairs,
        "assistant_reasoning_field_presence": reasoning_presence,
        "assistant_reasoning_field_nonempty": reasoning_nonempty,
        "assistant_turns": assistant_turns,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "options": options,
    }


def _response_summary(
    response: object, known_tool_names: set[str] | frozenset[str]
) -> dict[str, Any]:
    summary: dict[str, Any] = {"shape": _shape(response)}
    if not isinstance(response, dict):
        return summary
    summary["root_fields"] = sorted(str(key) for key in response)
    response_id = response.get("id")
    if isinstance(response_id, str):
        summary["provider_response_id"] = response_id
    choices = response.get("choices")
    summary["choice_count"] = len(choices) if isinstance(choices, list) else 0
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return summary
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    summary["choice_fields"] = sorted(str(key) for key in choice)
    summary["finish_reason"] = (
        finish_reason
        if isinstance(finish_reason, str) or finish_reason is None
        else _shape(finish_reason)
    )
    message = choice.get("message")
    if not isinstance(message, dict):
        summary["message"] = _shape(message)
        return summary
    summary["message_fields"] = sorted(str(key) for key in message)
    summary["message_field_types"] = {
        str(key): _type_name(value) for key, value in message.items()
    }
    role = message.get("role")
    if isinstance(role, str):
        summary["role"] = role
    summary["content"] = _shape(message.get("content"), key="content")
    summary["reasoning_fields"] = {
        str(key): _shape(value, key=str(key))
        for key, value in message.items()
        if _is_reasoning_key(str(key))
    }
    summary["tool_calls"] = _calls_summary(
        message.get("tool_calls"), known_tool_names
    )
    summary["legacy_function_call"] = _shape(
        message.get("function_call"), key="function_call"
    )
    summary["other_toolish_fields"] = {
        str(key): _shape(value, key=str(key))
        for key, value in message.items()
        if key not in {"tool_calls", "function_call"}
        and _is_toolish_key(str(key))
    }
    summary["reasoning_paths"] = _find_matching_fields(response, _is_reasoning_key)
    summary["toolish_paths"] = _find_matching_fields(response, _is_toolish_key)
    summary["usage"] = _shape(response.get("usage"), key="usage")
    return summary


def _calls_summary(
    calls: object, known_tool_names: set[str] | frozenset[str]
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "present": calls is not None,
        "type": _type_name(calls),
    }
    if not isinstance(calls, list):
        return summary
    summary["count"] = len(calls)
    items = []
    for call in calls[:MAX_COLLECTION_ITEMS]:
        item: dict[str, Any] = {"shape": _shape(call, key="tool_call")}
        if isinstance(call, dict):
            item["fields"] = sorted(str(key) for key in call)
            function = call.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str):
                    item["function_name"] = name
                    item["recognized_by_adapter"] = name in known_tool_names
                item["arguments"] = _shape(
                    function.get("arguments"), key="arguments"
                )
        items.append(item)
    summary["items"] = items
    if len(calls) > MAX_COLLECTION_ITEMS:
        summary["omitted_items"] = len(calls) - MAX_COLLECTION_ITEMS
    return summary


def _sse_summary(sse: bytes) -> dict[str, Any]:
    event_types: list[str] = []
    output_items: list[dict[str, Any]] = []
    for block in sse.decode("utf-8", errors="replace").split("\n\n"):
        if not block:
            continue
        event_name: str | None = None
        data: object = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except ValueError:
                    data = None
        if event_name is not None:
            event_types.append(event_name)
        if not isinstance(data, dict):
            continue
        item = data.get("item")
        if not isinstance(item, dict):
            continue
        item_summary: dict[str, Any] = {"type": item.get("type")}
        name = item.get("name")
        if isinstance(name, str):
            item_summary["name"] = name
        content = item.get("content")
        if isinstance(content, list):
            item_summary["content"] = [
                {
                    "type": part.get("type"),
                    "text": _shape(part.get("text"), key="text"),
                }
                for part in content
                if isinstance(part, dict)
            ]
        output_items.append(item_summary)
    return {
        "sse": _bytes_summary(sse),
        "event_types": event_types,
        "output_items": output_items,
    }


def _shape(
    value: object, *, key: str | None = None, depth: int = 0
) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "number", "value": value}
    if isinstance(value, str):
        summary: dict[str, Any] = {
            "type": "string",
            "characters": len(value),
            "utf8_bytes": len(value.encode("utf-8")),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
        if key in ENUM_FIELDS and len(value) <= 128:
            summary["value"] = value
        return summary
    if depth >= MAX_DEPTH:
        return {"type": _type_name(value), "depth_limited": True}
    if isinstance(value, list):
        items = [
            _shape(item, depth=depth + 1)
            for item in value[:MAX_COLLECTION_ITEMS]
        ]
        result: dict[str, Any] = {
            "type": "array",
            "count": len(value),
            "items": items,
        }
        if len(value) > MAX_COLLECTION_ITEMS:
            result["omitted_items"] = len(value) - MAX_COLLECTION_ITEMS
        return result
    if isinstance(value, dict):
        keys = sorted(value, key=lambda item: str(item))
        included_keys = keys[:MAX_COLLECTION_ITEMS]
        fields = {
            str(item): _shape(value[item], key=str(item), depth=depth + 1)
            for item in included_keys
        }
        result = {
            "type": "object",
            "field_count": len(value),
            "fields": fields,
        }
        if len(keys) > MAX_COLLECTION_ITEMS:
            result["omitted_fields"] = len(keys) - MAX_COLLECTION_ITEMS
        return result
    return {"type": _type_name(value)}


def _find_matching_fields(
    value: object,
    predicate: Any,
    *,
    path: str = "$",
    depth: int = 0,
) -> list[dict[str, Any]]:
    if depth >= MAX_DEPTH:
        return []
    matches: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if predicate(str(key)):
                matches.append({"path": child_path, "value": _shape(child, key=str(key))})
            matches.extend(
                _find_matching_fields(
                    child,
                    predicate,
                    path=child_path,
                    depth=depth + 1,
                )
            )
            if len(matches) >= MAX_COLLECTION_ITEMS:
                return matches[:MAX_COLLECTION_ITEMS]
    elif isinstance(value, list):
        for index, child in enumerate(value[:MAX_COLLECTION_ITEMS]):
            matches.extend(
                _find_matching_fields(
                    child,
                    predicate,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                )
            )
            if len(matches) >= MAX_COLLECTION_ITEMS:
                return matches[:MAX_COLLECTION_ITEMS]
    return matches


def _safe_headers(headers: object) -> dict[str, str]:
    if not hasattr(headers, "items"):
        return {}
    result = {}
    for name, value in headers.items():
        lowered = str(name).lower()
        if lowered in SAFE_RESPONSE_HEADERS:
            result[lowered] = str(value)
    return result


def _has_calls(message: dict[str, Any]) -> bool:
    calls = message.get("tool_calls")
    return (isinstance(calls, list) and bool(calls)) or isinstance(
        message.get("function_call"), dict
    )


def _has_text(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, list):
        return False
    return any(
        isinstance(item, str)
        and bool(item)
        or isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"])
        for item in value
    )


def _shape_is_nonempty(shape: dict[str, Any]) -> bool:
    if shape.get("type") == "string":
        return bool(shape.get("characters"))
    if shape.get("type") == "array":
        return bool(shape.get("count"))
    if shape.get("type") == "object":
        return bool(shape.get("field_count"))
    return shape.get("type") != "null"


def _is_reasoning_key(key: str) -> bool:
    lowered = key.lower()
    return "reason" in lowered or "thinking" in lowered


def _is_toolish_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("action", "call", "function", "tool"))


def _bytes_summary(value: bytes) -> dict[str, Any]:
    return {
        "bytes": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
    }


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError):
        return b""


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
