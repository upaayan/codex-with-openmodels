"""A private one-turn process boundary around the pinned Cursor SDK."""

import json
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol

from .errors import GatewayError
from .platform_support import ensure_private_directory
from .platform_support import is_windows

DEFAULT_CURSOR_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class CursorTurnResult:
    text: str
    input_tokens: int
    output_tokens: int
    tool_calls: int


class CursorWorker(Protocol):
    def turn(
        self,
        *,
        model_id: str,
        cwd: Path,
        prompt: str,
        thread_id: str,
    ) -> CursorTurnResult: ...

    def close(self) -> None: ...


class CursorWorkerClient:
    """Launch each native turn in the task's real OS working directory.

    Cursor's local shell inherits the worker process directory. A per-turn
    process therefore preserves correct tool behavior while allowing six
    independent Codex agent threads to run concurrently, even across repos.
    """

    def __init__(
        self,
        *,
        worker_script: Path,
        state_dir: Path,
        auth_path: Path,
        node_binary: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float = DEFAULT_CURSOR_TIMEOUT_SECONDS,
    ) -> None:
        self.worker_script = worker_script
        self.state_dir = state_dir
        self.auth_path = auth_path
        self.node_binary = node_binary
        self.environment = environment
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen[str]] = set()
        self._closed = False

    def turn(
        self,
        *,
        model_id: str,
        cwd: Path,
        prompt: str,
        thread_id: str,
    ) -> CursorTurnResult:
        node = self._prepare(cwd)
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "op": "turn",
            "model": model_id,
            "cwd": str(cwd),
            "prompt": prompt,
            "threadId": thread_id,
        }
        environment = (
            dict(self.environment)
            if self.environment is not None
            else os.environ.copy()
        )
        environment.update(
            {
                "SUDHIR_CURSOR_AUTH_PATH": str(self.auth_path),
                "SUDHIR_CURSOR_STATE_DIR": str(self.state_dir),
            }
        )

        try:
            process = subprocess.Popen(
                [str(node), str(self.worker_script)],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                encoding="utf-8",
                env=environment,
            )
        except OSError as exc:
            raise GatewayError(
                503,
                "cursor_worker_start_failed",
                "Cursor SDK worker could not be started",
            ) from exc
        with self._lock:
            if self._closed:
                process.terminate()
                process.wait(timeout=5)
                raise GatewayError(
                    503,
                    "cursor_worker_closed",
                    "Cursor SDK worker is closed",
                )
            self._active.add(process)

        encoded = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            stdout, _stderr = process.communicate(
                input=f"{encoded}\n",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise GatewayError(
                504,
                "cursor_worker_timeout",
                "Cursor SDK turn timed out",
            ) from exc
        finally:
            with self._lock:
                self._active.discard(process)

        response = _protocol_response(stdout, request_id)
        if response is None:
            raise GatewayError(
                502,
                "cursor_worker_unavailable",
                (
                    "Cursor SDK worker returned no valid response"
                    if process.returncode == 0
                    else f"Cursor SDK worker exited with status {process.returncode}"
                ),
            )
        return _parse_turn_result(response)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = list(self._active)
        for process in active:
            if process.poll() is None:
                process.terminate()

    def _prepare(self, cwd: Path) -> Path:
        with self._lock:
            if self._closed:
                raise GatewayError(
                    503,
                    "cursor_worker_closed",
                    "Cursor SDK worker is closed",
                )
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GatewayError(
                400,
                "cursor_cwd_invalid",
                "Cursor SDK turn has an invalid working directory",
            )
        if not self.worker_script.is_file():
            raise GatewayError(
                503,
                "cursor_worker_missing",
                f"Cursor SDK worker is missing at {self.worker_script}",
            )
        ensure_private_directory(self.state_dir)
        return self.node_binary or _find_node()


def _find_node() -> Path:
    configured = os.environ.get("SUDHIR_CODEX_NODE")
    resolved = shutil.which("node")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(resolved) if resolved else None,
        None if is_windows() else Path("/opt/homebrew/bin/node"),
    ]
    for candidate in candidates:
        if (
            candidate is not None
            and candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            return candidate
    raise GatewayError(
        503,
        "cursor_node_missing",
        "Cursor SDK requires Node.js 22.13 or newer",
    )


def _protocol_response(stdout: str, request_id: str) -> dict[str, Any] | None:
    response: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id") == request_id:
            response = value
    return response


def _parse_turn_result(response: dict[str, Any]) -> CursorTurnResult:
    if response.get("ok") is not True:
        message = response.get("error")
        if not isinstance(message, str) or not message:
            message = "Cursor SDK turn failed"
        raise GatewayError(502, "cursor_sdk_error", message)
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GatewayError(
            502,
            "cursor_empty_response",
            "Cursor SDK returned no assistant text",
        )
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return CursorTurnResult(
        text=text,
        input_tokens=_nonnegative_int(usage.get("inputTokens")),
        output_tokens=_nonnegative_int(usage.get("outputTokens")),
        tool_calls=_nonnegative_int(response.get("toolCalls")),
    )


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
