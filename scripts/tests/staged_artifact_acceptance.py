#!/usr/bin/env python3
"""Evaluate registered CI, staged-binary, deployed, and rollback acceptance."""

from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACTS = ROOT / "scripts" / "tests" / "sudhir_fork_contracts.toml"
GATEWAY_HEADER = "X-Sudhir-Gateway-Token"
DIRECT_DEEPSEEK = "pi-deepseek/deepseek-v4-flash"
OPENCODE_DEEPSEEK = "pi-opencode-go/deepseek-v4-flash"
SOL_MODEL = "gpt-5.6-sol"
LUNA_MODEL = "gpt-5.6-luna"
DECRYPT_ERRORS = (
    "stream disconnected before completion",
    "Encrypted function output content could not be decrypted or decoded",
)
XAI_DECRYPT_ERROR = (
    "Could not decrypt the provided encrypted_content. Ensure the value is the "
    "unmodified encrypted_content from a previous response."
)

# Literal tables are intentionally audited by verify_sudhir_fork_contracts.py.
CASE_IMPLEMENTATIONS = {
    "private-state-isolation": ["github-linux", "github-windows"],
    "gateway-auth-loopback": ["github-linux", "github-windows"],
    "standalone-web-search": ["github-linux", "github-windows"],
    "gateway-token-exclusion": ["staged-macos"],
    "arg0-identity": ["staged-macos"],
    "merged-catalog": ["github-linux", "github-windows"],
    "catalog-visibility": ["github-linux", "github-windows"],
    "credential-isolation": ["github-linux", "github-windows"],
    "route-reasoning-controls": ["github-linux", "github-windows"],
    "deepseek-opencode-replay": ["github-linux", "github-windows"],
    "reasoning-token-accounting": ["github-linux", "github-windows"],
    "encrypted-reasoning-route-scope": ["github-linux", "github-windows"],
    "history-exec-model-switch": ["github-linux", "github-windows"],
    "selected-model-pressure": ["staged-macos"],
    "same-model-cross-provider": ["staged-macos"],
    "legacy-rollout-resume": ["staged-macos"],
    "catalog-no-synthetic-hash": ["github-linux", "github-windows"],
    "generic-pi-oauth": ["github-linux", "github-windows"],
    "generic-pi-oauth-worker": ["github-linux", "github-windows"],
    "xai-responses-route": ["github-linux", "github-windows"],
    "sol-parent-luna-child": ["staged-macos"],
    "six-agent-capacity": ["github-linux", "github-windows"],
    "pi-hosted-tool-suppression": ["staged-macos"],
    "pi-hosted-tool-gateway-rejection": ["github-linux", "github-windows"],
    "merged-model-picker": ["github-linux"],
    "max-ultra-effort": ["github-linux"],
    "active-task-only-config": ["github-linux"],
    "public-source-boundary": ["github-linux"],
    "native-archive-integrity": ["staged-macos"],
    "staged-native-startup": ["staged-macos"],
    "windows-v8-preflight": ["github-windows"],
    "windows-native-startup": ["github-windows"],
}
POST_ACTIVATION_CASES = [
    "deepseek-opencode-replay",
    "same-model-cross-provider",
    "selected-model-pressure",
    "history-exec-model-switch",
    "sol-parent-luna-child",
]
ROLLBACK_CASES = ["sol-parent-luna-child"]
DEFERRED_WINDOWS_JOBS = {
    "SC-WINDOWS-001": "Windows Python and rusty_v8 dependency preflight",
    "SC-WINDOWS-002": "Windows x64 bundle",
}


class AcceptanceError(RuntimeError):
    def __init__(self, message: str, *, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.evidence = dict(evidence or {})


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run_external(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AcceptanceError(
            f"external acceptance command failed ({result.returncode}): {command[0]} {command[1]}"
        )
    return result


def _load_rows(path: Path) -> dict[str, dict[str, Any]]:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = document.get("contract", [])
    return {row["id"]: row for row in rows}


def _private_regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError(
            f"required private state file is not a regular file: {path}"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise AcceptanceError(
            f"required private state file is accessible outside its owner: {path}"
        )


def _gateway_health(url: str, token: str) -> None:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/health",
        headers={GATEWAY_HEADER: token},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            if response.status != 200:
                raise AcceptanceError(f"gateway health returned HTTP {response.status}")
            if (
                body
                and b"healthy" not in body.lower()
                and b"running" not in body.lower()
                and b"ok" not in body.lower()
            ):
                raise AcceptanceError("gateway health response was not recognizable")
    except OSError as exc:
        raise AcceptanceError(
            f"private gateway is not healthy at {url}: {exc}"
        ) from exc


def _forced_config(gateway_url: str) -> tuple[list[str], str]:
    launcher = ROOT / "sudhir_codex/src/sudhir_codex_gateway/launcher.py"
    tree = ast.parse(launcher.read_text(encoding="utf-8"), launcher.as_posix())
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_forced_config"
        ),
        None,
    )
    if function is None:
        raise AcceptanceError("candidate launcher has no _forced_config implementation")
    module = ast.Module(body=[function], type_ignores=[])
    namespace: dict[str, Any] = {"GATEWAY_TOKEN_HEADER": GATEWAY_HEADER}
    exec(compile(module, launcher.as_posix(), "exec"), namespace)
    values = namespace["_forced_config"](gateway_url)
    if not isinstance(values, list) or not values:
        raise AcceptanceError("candidate launcher returned no forced configuration")
    joined = "\n".join(values)
    for required in (
        'model_provider="sudhir_gateway"',
        "features.enable_request_compression=false",
        'features.multi_agent_v2.tool_namespace="sudhir_agents"',
        "agents.max_concurrent_threads_per_session=6",
        'SUDHIR_CODEX_GATEWAY_TOKEN="exclude"',
    ):
        if required not in joined:
            raise AcceptanceError(
                f"candidate launcher forced configuration is missing {required}"
            )
    return values, _sha256_file(launcher)


def _walk_values(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _nonempty_encrypted_count(value: Any) -> int:
    count = 0
    if isinstance(value, dict):
        encrypted = value.get("encrypted_content")
        if encrypted not in (None, "", [], {}):
            count += 1
        count += sum(_nonempty_encrypted_count(child) for child in value.values())
    elif isinstance(value, list):
        count += sum(_nonempty_encrypted_count(child) for child in value)
    return count


def _input_items(request: dict[str, Any]) -> list[dict[str, Any]]:
    body = request.get("json")
    if not isinstance(body, dict) or not isinstance(body.get("input"), list):
        return []
    return [item for item in body["input"] if isinstance(item, dict)]


def _inter_agent_encrypted_count(requests: list[dict[str, Any]]) -> int:
    return sum(
        _nonempty_encrypted_count(item)
        for request in requests
        for item in _input_items(request)
        if item.get("type") == "agent_message"
    )


class CaptureState:
    def __init__(self, upstream: str):
        parsed = urllib.parse.urlparse(upstream)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise AcceptanceError("capture proxy accepts only a loopback HTTP gateway")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.base_path = parsed.path.rstrip("/")
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def record(self, path: str, body: bytes, content_encoding: str) -> None:
        if content_encoding:
            raise AcceptanceError(
                f"capture proxy received unexpected request encoding {content_encoding}"
            )
        try:
            value = json.loads(body) if body else None
        except json.JSONDecodeError:
            value = None
        with self.lock:
            self.requests.append({"path": path, "json": value})

    def redacted(self) -> list[dict[str, Any]]:
        result = []
        with self.lock:
            requests = list(self.requests)
        for request in requests:
            body = request["json"] if isinstance(request["json"], dict) else {}
            inputs = body.get("input") if isinstance(body.get("input"), list) else []
            tools = body.get("tools") if isinstance(body.get("tools"), list) else []
            result.append(
                {
                    "path": request["path"],
                    "model": body.get("model"),
                    "input_types": sorted(
                        str(item.get("type"))
                        for item in inputs
                        if isinstance(item, dict) and item.get("type")
                    ),
                    "tool_types": sorted(
                        str(tool.get("type"))
                        for tool in tools
                        if isinstance(tool, dict) and tool.get("type")
                    ),
                    "agent_message_count": sum(
                        1
                        for item in inputs
                        if isinstance(item, dict)
                        and item.get("type") == "agent_message"
                    ),
                    "inter_agent_encrypted_content_count": sum(
                        _nonempty_encrypted_count(item)
                        for item in inputs
                        if isinstance(item, dict)
                        and item.get("type") == "agent_message"
                    ),
                }
            )
        return result


class CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _forward(self) -> None:
        state: CaptureState = self.server.capture_state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        state.record(self.path, body, self.headers.get("Content-Encoding", ""))
        connection = http.client.HTTPConnection(state.host, state.port, timeout=600)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower()
            not in {"host", "content-length", "connection", "transfer-encoding"}
        }
        try:
            connection.request(
                self.command,
                f"{state.base_path}{self.path}",
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {
                    "content-length",
                    "connection",
                    "transfer-encoding",
                }:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()


class CaptureProxy:
    def __init__(self, upstream: str):
        self.state = CaptureState(upstream)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.server.capture_state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TemporaryState:
    def __init__(self, source: Path):
        self.source = source
        self.root: Path | None = None

    def __enter__(self) -> Path:
        for name in ("config.toml", "auth.json"):
            _private_regular_file(self.source / name)
        self.root = Path(tempfile.mkdtemp(prefix="sudhir-acceptance-state-"))
        self.root.chmod(0o700)
        for name in ("config.toml", "auth.json"):
            target = self.root / name
            target.write_bytes((self.source / name).read_bytes())
            target.chmod(0o600)
        return self.root

    def __exit__(self, exc_type, exc, traceback):
        if self.root is not None:
            shutil.rmtree(self.root)


def _event_documents(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _event_summary(stdout: str, stderr: str) -> dict[str, Any]:
    events = _event_documents(stdout)
    output = f"{stdout}\n{stderr}"
    decrypt_signatures = [error for error in DECRYPT_ERRORS if error in output]
    return {
        "event_types": [event.get("type") for event in events if event.get("type")],
        "thread_ids": sorted(
            {
                str(value)
                for event in events
                for value in (event.get("thread_id"), event.get("threadId"))
                if value
            }
        ),
        "decrypt_error": bool(decrypt_signatures),
        "decrypt_signatures": decrypt_signatures,
        "xai_decrypt_error": XAI_DECRYPT_ERROR in output,
    }


def _thread_id(stdout: str) -> str:
    for event in _event_documents(stdout):
        if event.get("type") in {"thread.started", "thread_started"}:
            value = event.get("thread_id") or event.get("threadId")
            if isinstance(value, str):
                return value
    raise AcceptanceError("Codex JSONL emitted no thread ID")


def _base_environment(state: Path, token: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(state),
            "SUDHIR_CODEX_ROOT": str(ROOT),
            "SUDHIR_CODEX_STATE": str(state),
            "SUDHIR_CODEX_PI_AGENT_DIR": str(state / "pi-agent"),
            "SUDHIR_CODEX_GATEWAY_TOKEN": token,
            "SUDHIR_CODEX_LAUNCHER": "1",
        }
    )
    return environment


def _run_codex(
    binary: Path,
    forced: list[str],
    state: Path,
    token: str,
    model: str,
    prompt: str,
    *,
    ephemeral: bool,
    resume: str | None = None,
    extra_config: list[str] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    config = [*forced, *(extra_config or [])]
    command = [str(binary), *config, "exec"]
    if resume is None:
        command.extend(
            [
                "-m",
                model,
                "--json",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
            ]
        )
        if ephemeral:
            command.append("--ephemeral")
        command.append(prompt)
    else:
        command.extend(
            [
                "resume",
                "-m",
                model,
                "--json",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
            ]
        )
        if ephemeral:
            command.append("--ephemeral")
        command.extend([resume, prompt])
    return subprocess.run(
        command,
        cwd=ROOT,
        env=_base_environment(state, token),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _mcp_server(call_log: Path, expected_nonce: str) -> int:
    modern = False
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "server/discover":
            modern = True
            result = {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "ttlMs": 0,
                "cacheScope": "private",
                "_meta": {
                    "io.modelcontextprotocol/serverInfo": {
                        "name": "sudhir-acceptance",
                        "version": "1",
                    }
                },
            }
        elif method == "initialize":
            result = {
                "protocolVersion": message.get("params", {}).get(
                    "protocolVersion", "2025-06-18"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sudhir-acceptance", "version": "1"},
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Return the supplied acceptance nonce unchanged.",
                        "inputSchema": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {"value": {"type": "string"}},
                                    "required": ["value"],
                                },
                                {
                                    "type": "object",
                                    "properties": {"number": {"type": "integer"}},
                                    "required": ["number"],
                                },
                            ]
                        },
                    }
                ]
            }
            if modern:
                result.update(
                    {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
                )
        elif method == "tools/call":
            arguments = message.get("params", {}).get("arguments", {})
            nonce = arguments.get("value", "") if isinstance(arguments, dict) else ""
            call_log.parent.mkdir(parents=True, exist_ok=True)
            with call_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"called": True, "nonce_matched": nonce == expected_nonce}
                    )
                    + "\n"
                )
            result = {"content": [{"type": "text", "text": str(nonce)}]}
            if modern:
                result.update(
                    {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
                )
        elif "id" not in message:
            continue
        else:
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": "method not found"},
            }
            print(json.dumps(response), flush=True)
            continue
        response = {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
        print(json.dumps(response), flush=True)
    return 0


def _mcp_config(call_log: Path, nonce: str) -> list[str]:
    script = Path(__file__).resolve()
    command = json.dumps(sys.executable)
    args = json.dumps(
        [str(script), "--mcp-server", str(call_log), "--mcp-nonce", nonce]
    )
    return [
        "-c",
        f"mcp_servers.sudhir_acceptance.command={command}",
        "-c",
        f"mcp_servers.sudhir_acceptance.args={args}",
    ]


def _require_success(
    result: subprocess.CompletedProcess[str], nonce: str
) -> dict[str, Any]:
    summary = _event_summary(result.stdout, result.stderr)
    if result.returncode != 0:
        raise AcceptanceError(
            f"Codex exited {result.returncode}; events={summary['event_types']}",
            evidence={"event_summary": summary},
        )
    if nonce not in result.stdout:
        raise AcceptanceError(
            "Codex completed without the fixed acceptance nonce",
            evidence={"event_summary": summary},
        )
    if summary["decrypt_error"] or summary["xai_decrypt_error"]:
        raise AcceptanceError(
            "Codex emitted a decrypt/decode error",
            evidence={"event_summary": summary},
        )
    return summary


def _body_contains(value: Any, text: str) -> bool:
    return any(isinstance(item, str) and text in item for item in _walk_values(value))


def _live_agent_case(
    binary: Path, state_source: Path, gateway_url: str, token: str
) -> dict[str, Any]:
    nonce = "SUDHIR_LUNA_PLAINTEXT_7F35D2"
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        prompt = (
            f"Use sudhir_agents.spawn_agent exactly once with model {LUNA_MODEL}. "
            f"Tell that child to return exactly {nonce}. Wait for it, then return exactly {nonce}."
        )
        result = _run_codex(
            binary, forced, state, token, SOL_MODEL, prompt, ephemeral=True
        )
        try:
            event_summary = _require_success(result, nonce)
        except AcceptanceError as exc:
            requests = list(proxy.state.requests)
            evidence = dict(exc.evidence)
            evidence.update(
                {
                    "encrypted_content_count": _inter_agent_encrypted_count(requests),
                    "requests": proxy.state.redacted(),
                }
            )
            raise AcceptanceError(str(exc), evidence=evidence) from exc
        requests = list(proxy.state.requests)
    child = [
        request
        for request in requests
        if isinstance(request.get("json"), dict)
        and request["json"].get("model") == LUNA_MODEL
        and any(
            item.get("type") == "agent_message" and _body_contains(item, nonce)
            for item in _input_items(request)
        )
    ]
    returned = [
        request
        for request in requests
        if isinstance(request.get("json"), dict)
        and request["json"].get("model") == SOL_MODEL
        and any(
            item.get("type") == "agent_message" and _body_contains(item, nonce)
            for item in _input_items(request)
        )
    ]
    encrypted = _inter_agent_encrypted_count(requests)
    if len(child) != 1:
        raise AcceptanceError(f"expected one Luna child request, observed {len(child)}")
    if not returned:
        raise AcceptanceError(
            "parent never received the Luna nonce through agent_message"
        )
    if encrypted:
        raise AcceptanceError(
            f"inter-agent capture contained {encrypted} encrypted_content values"
        )
    return {
        "parent_model": SOL_MODEL,
        "child_model": LUNA_MODEL,
        "child_request_count": len(child),
        "parent_result_delivery_count": len(returned),
        "encrypted_content_count": encrypted,
        "event_types": event_summary["event_types"],
        "launcher_sha256": launcher_hash,
        "requests": proxy.state.redacted(),
    }


def _live_tool_replay_case(
    binary: Path, state_source: Path, gateway_url: str, token: str
) -> dict[str, Any]:
    outcomes = []
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        for index, model in enumerate((DIRECT_DEEPSEEK, OPENCODE_DEEPSEEK), start=1):
            nonce = f"SUDHIR_DEEPSEEK_TOOL_{index}_A91C"
            call_log = state / f"mcp-call-{index}.jsonl"
            prompt = (
                "Call mcp__sudhir_acceptance__echo exactly once with the string value "
                f"{nonce}. After receiving the tool output, return exactly {nonce}."
            )
            result = _run_codex(
                binary,
                forced,
                state,
                token,
                model,
                prompt,
                ephemeral=True,
                extra_config=_mcp_config(call_log, nonce),
            )
            summary = _require_success(result, nonce)
            calls = (
                [
                    json.loads(line)
                    for line in call_log.read_text(encoding="utf-8").splitlines()
                ]
                if call_log.is_file()
                else []
            )
            if len(calls) != 1 or calls[0] != {"called": True, "nonce_matched": True}:
                raise AcceptanceError(
                    f"{model} did not call the root-oneOf MCP tool exactly once"
                )
            model_requests = [
                request
                for request in proxy.state.requests
                if isinstance(request.get("json"), dict)
                and request["json"].get("model") == model
            ]
            has_tool_output = any(
                any("output" in str(item.get("type")) for item in _input_items(request))
                for request in model_requests
            )
            if len(model_requests) < 2 or not has_tool_output:
                raise AcceptanceError(
                    f"{model} did not complete a tool-output replay turn"
                )
            outcomes.append(
                {
                    "model": model,
                    "request_count": len(model_requests),
                    "event_types": summary["event_types"],
                }
            )
    return {
        "routes": outcomes,
        "launcher_sha256": launcher_hash,
        "requests": proxy.state.redacted(),
    }


def _run_two_turns(
    binary: Path,
    state_source: Path,
    gateway_url: str,
    token: str,
    *,
    pressure: bool,
    inject_legacy_hash: bool,
) -> dict[str, Any]:
    first_nonce = "SUDHIR_CONTINUITY_FIRST_118B"
    second_nonce = "SUDHIR_CONTINUITY_SECOND_73E4"
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        first = _run_codex(
            binary,
            forced,
            state,
            token,
            DIRECT_DEEPSEEK,
            f"Return exactly {first_nonce}.",
            ephemeral=False,
        )
        _require_success(first, first_nonce)
        thread_id = _thread_id(first.stdout)
        first_request_count = len(proxy.state.requests)
        if inject_legacy_hash:
            rollout_files = list(state.rglob("*.jsonl"))
            replaced = 0
            for rollout in rollout_files:
                lines = []
                changed = False
                for line in rollout.read_text(encoding="utf-8").splitlines():
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get("type") == "turn_context":
                        payload = value.get("payload")
                        if isinstance(payload, dict):
                            payload["comp_hash"] = "legacy-synthetic-provider-hash"
                            changed = True
                            replaced += 1
                    lines.append(json.dumps(value, separators=(",", ":")))
                if changed:
                    rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
            if replaced == 0:
                raise AcceptanceError(
                    "could not inject a legacy comp_hash into the temporary rollout"
                )
        extra = ["-c", "model_auto_compact_token_limit=1"] if pressure else []
        second = _run_codex(
            binary,
            forced,
            state,
            token,
            OPENCODE_DEEPSEEK,
            f"Return exactly {second_nonce}.",
            ephemeral=False,
            resume=thread_id,
            extra_config=extra,
        )
        _require_success(second, second_nonce)
        second_requests = proxy.state.requests[first_request_count:]
    wrong_route = [
        request
        for request in second_requests
        if isinstance(request.get("json"), dict)
        and request["json"].get("model") == DIRECT_DEEPSEEK
    ]
    compact_paths = [
        request["path"] for request in second_requests if "compact" in request["path"]
    ]
    selected_requests = [
        request
        for request in second_requests
        if isinstance(request.get("json"), dict)
        and request["json"].get("model") == OPENCODE_DEEPSEEK
    ]
    if wrong_route:
        raise AcceptanceError(
            "the resumed selected-model turn contacted the previous provider"
        )
    if pressure and len(selected_requests) < 2 and not compact_paths:
        raise AcceptanceError(
            "the low token limit did not trigger selected-model pressure handling"
        )
    if not pressure and compact_paths:
        raise AcceptanceError("provider/model change compacted without pressure")
    return {
        "from_model": DIRECT_DEEPSEEK,
        "to_model": OPENCODE_DEEPSEEK,
        "selected_request_count": len(selected_requests),
        "previous_route_request_count": len(wrong_route),
        "compact_paths": compact_paths,
        "legacy_hash_injected": inject_legacy_hash,
        "launcher_sha256": launcher_hash,
        "requests": proxy.state.redacted(),
    }


def _live_history_case(
    binary: Path, state_source: Path, gateway_url: str, token: str
) -> dict[str, Any]:
    first_nonce = "SUDHIR_EXEC_HISTORY_FIRST_42A1"
    second_nonce = "SUDHIR_EXEC_HISTORY_SECOND_91B7"
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        first_prompt = (
            "Use exec_command exactly once with cmd `pwd`. After it succeeds, return exactly "
            f"{first_nonce}."
        )
        first = _run_codex(
            binary, forced, state, token, DIRECT_DEEPSEEK, first_prompt, ephemeral=False
        )
        _require_success(first, first_nonce)
        thread_id = _thread_id(first.stdout)
        before = len(proxy.state.requests)
        second = _run_codex(
            binary,
            forced,
            state,
            token,
            OPENCODE_DEEPSEEK,
            f"Do not use tools. Return exactly {second_nonce}.",
            ephemeral=False,
            resume=thread_id,
        )
        summary = _require_success(second, second_nonce)
        selected = proxy.state.requests[before:]
    combined = f"{second.stdout}\n{second.stderr}"
    if (
        "unknown tool 'exec_command'" in combined
        or 'unknown tool "exec_command"' in combined
    ):
        raise AcceptanceError("model switch rejected retained exec_command history")
    history_seen = any(
        _body_contains(request.get("json"), "exec_command") for request in selected
    )
    if not history_seen:
        raise AcceptanceError(
            "selected-model request did not retain exec_command history"
        )
    return {
        "history_replayed": history_seen,
        "unknown_tool_error": False,
        "event_types": summary["event_types"],
        "launcher_sha256": launcher_hash,
        "requests": proxy.state.redacted(),
    }


def _gateway_token_case(
    binary: Path, state_source: Path, gateway_url: str, token: str
) -> dict[str, Any]:
    nonce = "SUDHIR_GATEWAY_ENV_ABSENT_611D"
    prompt = (
        "Use exec_command exactly once with cmd `python3 -c 'import os; "
        'print("PRESENT" if "SUDHIR_CODEX_GATEWAY_TOKEN" in os.environ else "ABSENT")\'`. '
        f"If and only if its output is ABSENT, return exactly {nonce}."
    )
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        result = _run_codex(
            binary, forced, state, token, SOL_MODEL, prompt, ephemeral=True
        )
        summary = _require_success(result, nonce)
    command_outputs = []
    for event in _event_documents(result.stdout):
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            command_outputs.append(str(item.get("aggregated_output", "")).strip())
    if "ABSENT" not in command_outputs:
        raise AcceptanceError("model shell did not prove gateway-token exclusion")
    return {
        "shell_observation": "ABSENT",
        "event_types": summary["event_types"],
        "launcher_sha256": launcher_hash,
    }


def _arg0_case(binary: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sudhir-arg0-") as temp:
        alias = Path(temp) / "sudhir-contract-alias"
        os.link(binary, alias)
        same_inode = os.stat(binary).st_ino == os.stat(alias).st_ino
        result = subprocess.run(
            [str(alias), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    if not same_inode or result.returncode != 0 or "codex" not in result.stdout.lower():
        raise AcceptanceError(
            "hard-link invocation did not preserve normal CLI identity"
        )
    return {"hard_link": True, "version_command": "pass"}


def _pi_tools_case(
    binary: Path, state_source: Path, gateway_url: str, token: str
) -> dict[str, Any]:
    nonce = "SUDHIR_PI_TOOLS_73D9"
    with CaptureProxy(gateway_url) as proxy, TemporaryState(state_source) as state:
        forced, launcher_hash = _forced_config(proxy.url)
        result = _run_codex(
            binary,
            forced,
            state,
            token,
            DIRECT_DEEPSEEK,
            f"Return exactly {nonce}.",
            ephemeral=True,
        )
        _require_success(result, nonce)
        requests = [
            request
            for request in proxy.state.requests
            if isinstance(request.get("json"), dict)
            and request["json"].get("model") == DIRECT_DEEPSEEK
        ]
    forbidden = []
    for request in requests:
        tools = request["json"].get("tools", [])
        for tool in tools if isinstance(tools, list) else []:
            if isinstance(tool, dict) and tool.get("type") in {
                "web_search",
                "image_generation",
            }:
                forbidden.append(tool.get("type"))
    if forbidden:
        raise AcceptanceError(
            f"Pi request advertised provider-hosted tools: {forbidden}"
        )
    return {
        "forbidden_tool_count": 0,
        "launcher_sha256": launcher_hash,
        "requests": proxy.state.redacted(),
    }


def _archive_integrity(artifact_dir: Path) -> dict[str, Any]:
    expected = {
        "codex-with-openmodels-aarch64-apple-darwin.tar.gz",
        "codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz",
        "codex-with-openmodels-x86_64-pc-windows-msvc.zip",
    }
    sums = artifact_dir / "SHA256SUMS"
    if not sums.is_file():
        raise AcceptanceError("candidate SHA256SUMS is missing")
    observed = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not match:
            raise AcceptanceError("candidate SHA256SUMS contains an invalid line")
        observed[match.group(2)] = match.group(1).lower()
    if set(observed) != expected:
        raise AcceptanceError(
            "candidate archive set does not match the registered layout"
        )
    for name, digest in observed.items():
        path = artifact_dir / name
        if not path.is_file() or _sha256_file(path) != digest:
            raise AcceptanceError(f"candidate archive checksum failed: {name}")
    return {"archives": sorted(observed), "checksums": "pass"}


def _startup_case(binary: Path) -> dict[str, Any]:
    commands = (
        [str(binary), "--version"],
        [str(binary), "--help"],
        [str(binary), "app-server", "--help"],
        [str(binary), "mcp-server", "--help"],
    )
    for command in commands:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if result.returncode != 0:
            raise AcceptanceError(
                f"staged startup command failed: {' '.join(command[1:])}"
            )
    return {
        "commands": ["--version", "--help", "app-server --help", "mcp-server --help"],
        "status": "pass",
    }


def _ci_phase(
    args: argparse.Namespace, rows: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    evidence = json.loads(args.test_evidence.read_text(encoding="utf-8"))
    if (
        evidence.get("status") != "pass"
        or evidence.get("phase") != "prebuild"
        or evidence.get("platform") != args.platform
    ):
        raise AcceptanceError(
            "exact contract-test evidence did not pass on the requested platform"
        )
    if evidence.get("source_commit") != args.source_commit:
        raise AcceptanceError(
            "contract-test evidence commit does not match source-commit"
        )
    host = f"github-{args.platform}"
    results = []
    deferred = {"SC-WINDOWS-001", "SC-WINDOWS-002"}
    completed_ids = {
        result_id
        for item in evidence.get("results", [])
        if item.get("status") == "pass"
        for result_id in item.get("ids", [item.get("id")])
        if isinstance(result_id, str)
    }
    for row in rows.values():
        if args.platform not in row["platforms"] or host not in row["acceptance_hosts"]:
            continue
        if row["acceptance_mode"] == "artifact":
            continue
        if row["test_runner"] == "rust-nextest":
            status = "not-run-compile-free"
        else:
            status = "deferred" if row["id"] in deferred else "pass"
        if status == "pass" and f"{row['id']}:primary" not in completed_ids:
            raise AcceptanceError(
                f"CI evidence lacks the primary result for {row['id']}"
            )
        results.append(
            {
                "contract_id": row["id"],
                "case": row["acceptance_case"],
                "host": host,
                "mode": row["acceptance_mode"],
                "status": status,
            }
        )
    return {
        "schema_version": 1,
        "phase": "ci",
        "source_commit": args.source_commit,
        "platform": args.platform,
        "test_evidence_sha256": _sha256_file(args.test_evidence),
        "test_results": evidence.get("results", []),
        "status": "pass",
        "results": results,
    }


def _load_native_run_evidence(args: argparse.Namespace) -> dict[str, Any]:
    if not args.native_run_id or not re.fullmatch(
        r"[1-9][0-9]*", str(args.native_run_id)
    ):
        raise AcceptanceError("GitHub acceptance requires a numeric native run ID")
    if not args.release_repo or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.release_repo
    ):
        raise AcceptanceError(
            "GitHub acceptance requires release-repo as owner/repository"
        )
    if not args.source_commit or not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise AcceptanceError("GitHub acceptance requires the full source commit")

    run = json.loads(
        _run_external(
            [
                "gh",
                "run",
                "view",
                str(args.native_run_id),
                "--repo",
                args.release_repo,
                "--json",
                "databaseId,headSha,status,conclusion,workflowName,jobs",
            ]
        ).stdout
    )
    if str(run.get("databaseId")) != str(args.native_run_id):
        raise AcceptanceError("GitHub run evidence has the wrong run ID")
    if run.get("headSha") != args.source_commit:
        raise AcceptanceError("GitHub run evidence has the wrong source commit")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise AcceptanceError("GitHub native run is not complete and successful")
    if run.get("workflowName") != "native-release":
        raise AcceptanceError("GitHub run evidence is not from native-release")

    jobs = run.get("jobs")
    if not isinstance(jobs, list):
        raise AcceptanceError("GitHub native run has no job evidence")
    job_results: dict[str, dict[str, Any]] = {}
    for required_name in DEFERRED_WINDOWS_JOBS.values():
        matches = [
            job
            for job in jobs
            if isinstance(job, dict) and job.get("name") == required_name
        ]
        if len(matches) != 1:
            raise AcceptanceError(
                f"GitHub run has {len(matches)} jobs named {required_name}"
            )
        job = matches[0]
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise AcceptanceError(f"GitHub job did not pass: {required_name}")
        job_results[required_name] = {
            "name": required_name,
            "database_id": job.get("databaseId"),
            "status": job.get("status"),
            "conclusion": job.get("conclusion"),
        }

    documents: dict[str, dict[str, Any]] = {}
    document_hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="sudhir-native-evidence-") as temp:
        temp_root = Path(temp)
        for platform in ("linux", "windows"):
            artifact_name = f"fork-contract-evidence-{platform}"
            destination = temp_root / platform
            destination.mkdir()
            _run_external(
                [
                    "gh",
                    "run",
                    "download",
                    str(args.native_run_id),
                    "--repo",
                    args.release_repo,
                    "--name",
                    artifact_name,
                    "--dir",
                    str(destination),
                ]
            )
            expected_name = f"fork-contract-evidence-{platform}.json"
            matches = list(destination.rglob(expected_name))
            if len(matches) != 1:
                raise AcceptanceError(
                    f"GitHub artifact {artifact_name} contains {len(matches)} {expected_name} files"
                )
            evidence_path = matches[0]
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            if (
                document.get("phase") != "ci"
                or document.get("platform") != platform
                or document.get("source_commit") != args.source_commit
                or document.get("status") != "pass"
            ):
                raise AcceptanceError(
                    f"GitHub {platform} contract evidence does not match the candidate"
                )
            documents[platform] = document
            document_hashes[platform] = _sha256_file(evidence_path)

    return {
        "run": {
            "database_id": run.get("databaseId"),
            "head_sha": run.get("headSha"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "workflow_name": run.get("workflowName"),
        },
        "jobs": job_results,
        "documents": documents,
        "document_hashes": document_hashes,
    }


def _github_case(row: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    host_results = []
    for host in row["acceptance_hosts"]:
        if host not in {"github-linux", "github-windows"}:
            raise AcceptanceError(
                f"{row['id']} has a non-GitHub host in CI/source mode"
            )
        platform = host.removeprefix("github-")
        document = evidence["documents"].get(platform)
        if not isinstance(document, dict):
            raise AcceptanceError(f"missing GitHub {platform} acceptance evidence")
        matches = [
            item
            for item in document.get("results", [])
            if isinstance(item, dict) and item.get("contract_id") == row["id"]
        ]
        if len(matches) != 1:
            raise AcceptanceError(
                f"GitHub {platform} evidence has {len(matches)} results for {row['id']}"
            )
        result = matches[0]
        for field, expected in (
            ("case", row["acceptance_case"]),
            ("host", host),
            ("mode", row["acceptance_mode"]),
        ):
            if result.get(field) != expected:
                raise AcceptanceError(
                    f"GitHub result mismatch for {row['id']}: {field}"
                )
        if row["id"] in DEFERRED_WINDOWS_JOBS:
            if result.get("status") != "deferred":
                raise AcceptanceError(
                    f"GitHub matrix evidence did not defer {row['id']}"
                )
            job_name = DEFERRED_WINDOWS_JOBS[row["id"]]
            job = evidence["jobs"].get(job_name)
            if not isinstance(job, dict) or job.get("conclusion") != "success":
                raise AcceptanceError(
                    f"deferred GitHub acceptance job did not pass: {job_name}"
                )
            status = "pass"
        else:
            if result.get("status") != "pass":
                raise AcceptanceError(
                    f"GitHub acceptance result did not pass for {row['id']}"
                )
            job_name = None
            status = "pass"
        host_results.append(
            {
                "host": host,
                "status": status,
                "evidence_sha256": evidence["document_hashes"][platform],
                "deferred_job": job_name,
            }
        )
    return {"github_results": host_results}


def _required_cases(
    args: argparse.Namespace, rows: dict[str, dict[str, Any]]
) -> list[str]:
    explicit = list(args.cases or [])
    required = []
    if args.required_from:
        document = json.loads(args.required_from.read_text(encoding="utf-8"))
        required.extend(
            item["acceptance_case"] for item in document.get("contracts", [])
        )
    if explicit:
        if args.phase == "post-activation":
            eligible = set(explicit)
            required = [case for case in required if case in eligible]
        else:
            required.extend(explicit)
    if args.phase == "post-activation":
        required.extend(
            row["acceptance_case"]
            for row in rows.values()
            if row["always"] and row["acceptance_case"] in POST_ACTIVATION_CASES
        )
    if not required:
        raise AcceptanceError("no acceptance cases were selected")
    return list(dict.fromkeys(required))


def _artifact_case(case: str, args: argparse.Namespace) -> dict[str, Any]:
    binary = args.binary
    if binary is None or not binary.is_file():
        raise AcceptanceError(f"{case}: staged binary is missing")
    if case == "sol-parent-luna-child":
        return _live_agent_case(
            binary, args.state_source, args.gateway_url, args.gateway_token
        )
    if case == "gateway-token-exclusion":
        return _gateway_token_case(
            binary, args.state_source, args.gateway_url, args.gateway_token
        )
    if case == "arg0-identity":
        return _arg0_case(binary)
    if case == "selected-model-pressure":
        return _run_two_turns(
            binary,
            args.state_source,
            args.gateway_url,
            args.gateway_token,
            pressure=True,
            inject_legacy_hash=False,
        )
    if case == "same-model-cross-provider":
        return _run_two_turns(
            binary,
            args.state_source,
            args.gateway_url,
            args.gateway_token,
            pressure=False,
            inject_legacy_hash=False,
        )
    if case == "legacy-rollout-resume":
        return _run_two_turns(
            binary,
            args.state_source,
            args.gateway_url,
            args.gateway_token,
            pressure=False,
            inject_legacy_hash=True,
        )
    if case == "pi-hosted-tool-suppression":
        return _pi_tools_case(
            binary, args.state_source, args.gateway_url, args.gateway_token
        )
    if case == "native-archive-integrity":
        if args.artifact_dir is None:
            raise AcceptanceError("native-archive-integrity requires --artifact-dir")
        return _archive_integrity(args.artifact_dir)
    if case == "staged-native-startup":
        return _startup_case(binary)
    raise AcceptanceError(f"no staged artifact implementation for {case}")


def _live_post_case(case: str, args: argparse.Namespace) -> dict[str, Any]:
    if case == "deepseek-opencode-replay":
        return _live_tool_replay_case(
            args.binary, args.state_source, args.gateway_url, args.gateway_token
        )
    if case == "same-model-cross-provider":
        return _run_two_turns(
            args.binary,
            args.state_source,
            args.gateway_url,
            args.gateway_token,
            pressure=False,
            inject_legacy_hash=False,
        )
    if case == "selected-model-pressure":
        return _run_two_turns(
            args.binary,
            args.state_source,
            args.gateway_url,
            args.gateway_token,
            pressure=True,
            inject_legacy_hash=False,
        )
    if case == "history-exec-model-switch":
        return _live_history_case(
            args.binary, args.state_source, args.gateway_url, args.gateway_token
        )
    if case == "sol-parent-luna-child":
        return _live_agent_case(
            args.binary, args.state_source, args.gateway_url, args.gateway_token
        )
    raise AcceptanceError(f"post-activation case is not registered: {case}")


def _prepare_live_args(args: argparse.Namespace) -> None:
    if (
        args.state_source is None
        or args.gateway_token_file is None
        or args.gateway_url is None
    ):
        raise AcceptanceError(
            "live acceptance requires state source, gateway URL, and token file"
        )
    if args.phase == "rollback" and (
        args.operational_root is None or not args.operational_root.is_dir()
    ):
        raise AcceptanceError(
            "rollback acceptance requires the operational repository root"
        )
    _private_regular_file(args.gateway_token_file)
    args.gateway_token = args.gateway_token_file.read_text(encoding="utf-8").strip()
    if not args.gateway_token:
        raise AcceptanceError("gateway token file is empty")
    _gateway_health(args.gateway_url, args.gateway_token)


def _run_selected(
    args: argparse.Namespace, rows: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    cases = _required_cases(args, rows)
    by_case = {row["acceptance_case"]: row for row in rows.values()}
    unknown = sorted(set(cases) - set(by_case))
    if unknown:
        raise AcceptanceError(f"unregistered acceptance cases: {unknown}")
    if args.binary is None or not args.binary.is_file():
        raise AcceptanceError("acceptance binary is missing")
    binary_hash = _sha256_file(args.binary)
    if args.expected_binary_sha256 and binary_hash != args.expected_binary_sha256:
        raise AcceptanceError("acceptance binary hash does not match the recorded hash")
    _prepare_live_args(args)
    github_evidence = None
    if args.phase == "pre-activation" and any(
        by_case[case]["acceptance_mode"] in {"ci", "source"} for case in cases
    ):
        github_evidence = _load_native_run_evidence(args)
    results = []
    for case in cases:
        row = by_case[case]
        if args.phase == "post-activation":
            if case not in POST_ACTIVATION_CASES:
                raise AcceptanceError(
                    f"post-activation case is outside the fixed live set: {case}"
                )
            details = _live_post_case(case, args)
            host = "deployed-macos"
        elif args.phase == "rollback":
            if case not in ROLLBACK_CASES:
                raise AcceptanceError(
                    f"rollback case is outside the fixed rollback set: {case}"
                )
            if (
                args.gateway_manifest is None
                or args.expected_gateway_state != "preimage"
            ):
                raise AcceptanceError(
                    "rollback requires the recorded gateway manifest and preimage expectation"
                )
            verifier = ROOT / "scripts/tests/verify_sudhir_fork_contracts.py"
            check = subprocess.run(
                [
                    sys.executable,
                    str(verifier),
                    "gateway-deploy-verify",
                    "--manifest",
                    str(args.gateway_manifest),
                    "--operational-root",
                    str(args.operational_root),
                    "--expect",
                    "preimage",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if check.returncode != 0:
                raise AcceptanceError("rollback gateway preimage verification failed")
            try:
                details = _live_agent_case(
                    args.binary, args.state_source, args.gateway_url, args.gateway_token
                )
                outcome = (
                    "INTERMITTENT_RC8_CHILD_COMPLETED"
                    if args.expected_outcome == "pre-existing-rc8-agent-result"
                    else "PASS"
                )
            except AcceptanceError as exc:
                if args.expected_outcome != "pre-existing-rc8-agent-result":
                    raise
                event_summary = exc.evidence.get("event_summary", {})
                if event_summary.get("xai_decrypt_error"):
                    raise
                signatures = set(event_summary.get("decrypt_signatures", []))
                encrypted_count = int(exc.evidence.get("encrypted_content_count", 0))
                if encrypted_count == 0 and not set(DECRYPT_ERRORS).issubset(
                    signatures
                ):
                    raise
                details = {
                    "pre_existing_failure_signature": True,
                    "encrypted_content_count": encrypted_count,
                    "decrypt_signatures": [
                        signature
                        for signature in DECRYPT_ERRORS
                        if signature in signatures
                    ],
                    "requests": exc.evidence.get("requests", []),
                }
                outcome = "PRE_EXISTING_RC8_AGENT_FAILURE"
            details["rollback_outcome"] = outcome
            host = "deployed-macos"
        else:
            if row["acceptance_mode"] == "artifact":
                details = _artifact_case(case, args)
                host = "staged-macos"
            else:
                if github_evidence is None:
                    raise AcceptanceError(f"{case} requires exact GitHub evidence")
                details = _github_case(row, github_evidence)
                host = "github"
        results.append(
            {
                "contract_id": row["id"],
                "case": case,
                "host": host,
                "status": "pass",
                "details": details,
            }
        )
    return {
        "schema_version": 1,
        "phase": args.phase,
        "baseline_commit": args.baseline_commit,
        "source_commit": args.source_commit,
        "native_run_id": args.native_run_id,
        "binary_sha256": binary_hash,
        "github_run": None if github_evidence is None else github_evidence["run"],
        "status": "pass",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-server", type=Path)
    parser.add_argument("--mcp-nonce")
    parser.add_argument(
        "--phase",
        choices=("ci", "pre-activation", "post-activation", "rollback"),
        default="pre-activation",
    )
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--platform", choices=("linux", "windows"))
    parser.add_argument("--source-commit")
    parser.add_argument("--test-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--baseline-commit")
    parser.add_argument("--native-run-id")
    parser.add_argument("--release-repo")
    parser.add_argument("--state-source", type=Path)
    parser.add_argument("--gateway-url")
    parser.add_argument("--gateway-token-file", type=Path)
    parser.add_argument("--case", dest="cases", action="append")
    parser.add_argument("--required-from", type=Path)
    parser.add_argument("--expected-binary-sha256")
    parser.add_argument("--gateway-manifest", type=Path)
    parser.add_argument("--operational-root", type=Path)
    parser.add_argument("--expected-gateway-state", choices=("preimage", "candidate"))
    parser.add_argument(
        "--expected-outcome", choices=("pass", "pre-existing-rc8-agent-result")
    )
    args = parser.parse_args()

    if args.mcp_server is not None:
        if not args.mcp_nonce:
            parser.error("--mcp-nonce is required with --mcp-server")
        return _mcp_server(args.mcp_server, args.mcp_nonce)
    if args.output is None:
        parser.error("--output is required")
    try:
        rows = _load_rows(args.contracts)
        if args.phase == "ci":
            if (
                args.platform is None
                or args.source_commit is None
                or args.test_evidence is None
            ):
                raise AcceptanceError(
                    "CI phase requires platform, source commit, and test evidence"
                )
            result = _ci_phase(args, rows)
        else:
            result = _run_selected(args, rows)
        _write_json(args.output, result)
        return 0
    except (
        AcceptanceError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        failure = {
            "schema_version": 1,
            "phase": args.phase,
            "status": "fail",
            "error": str(exc),
        }
        _write_json(args.output, failure)
        print(f"staged-artifact-acceptance: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
