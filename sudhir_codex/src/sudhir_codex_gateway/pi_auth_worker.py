"""Resolve provider authentication through Pi's registered provider runtime."""

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .catalog import OpenModel
from .cursor_worker import _find_node
from .cursor_worker import _protocol_response
from .errors import GatewayError

DEFAULT_PI_AUTH_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class PiAuthResult:
    provider_id: str
    model_id: str
    api: str
    api_key: str | None
    headers: dict[str, str]
    base_url: str | None


class PiAuthWorker(Protocol):
    def resolve(self, model: OpenModel) -> PiAuthResult: ...

    def close(self) -> None: ...


class PiAuthWorkerClient:
    """Run Pi's auth resolver in an isolated one-request Node process."""

    def __init__(
        self,
        *,
        worker_script: Path,
        agent_dir: Path,
        node_binary: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float = DEFAULT_PI_AUTH_TIMEOUT_SECONDS,
    ) -> None:
        self.worker_script = worker_script
        self.agent_dir = agent_dir
        self.node_binary = node_binary
        self.environment = environment
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen[str]] = set()
        self._closed = False

    def resolve(self, model: OpenModel) -> PiAuthResult:
        node = self._prepare()
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "provider": model.provider_id,
            "model": model.upstream_id,
            "api": model.api,
        }
        environment = (
            dict(self.environment)
            if self.environment is not None
            else os.environ.copy()
        )
        environment["SUDHIR_PI_AGENT_DIR"] = str(self.agent_dir)

        try:
            process = subprocess.Popen(
                [str(node), str(self.worker_script)],
                cwd=self.agent_dir,
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
                "pi_auth_worker_start_failed",
                "Pi authentication worker could not be started",
            ) from exc
        with self._lock:
            if self._closed:
                process.terminate()
                process.wait(timeout=5)
                raise GatewayError(
                    503,
                    "pi_auth_worker_closed",
                    "Pi authentication worker is closed",
                )
            self._active.add(process)

        encoded = json.dumps(request, ensure_ascii=True, separators=(",", ":"))
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
                "pi_auth_worker_timeout",
                f"Pi authentication timed out for provider {model.provider_id!r}",
            ) from exc
        finally:
            with self._lock:
                self._active.discard(process)

        response = _protocol_response(stdout, request_id)
        if response is None:
            raise GatewayError(
                503,
                "pi_auth_worker_unavailable",
                f"Pi authentication is unavailable for provider {model.provider_id!r}",
            )
        if response.get("ok") is not True:
            raise GatewayError(
                503,
                "pi_auth_resolution_failed",
                f"Pi authentication failed for provider {model.provider_id!r}",
            )
        return _parse_auth_result(response, model)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = list(self._active)
        for process in active:
            if process.poll() is None:
                process.terminate()

    def _prepare(self) -> Path:
        with self._lock:
            if self._closed:
                raise GatewayError(
                    503,
                    "pi_auth_worker_closed",
                    "Pi authentication worker is closed",
                )
        if not self.worker_script.is_file():
            raise GatewayError(
                503,
                "pi_auth_worker_missing",
                f"Pi authentication worker is missing at {self.worker_script}",
            )
        if not self.agent_dir.is_dir():
            raise GatewayError(
                503,
                "pi_agent_dir_missing",
                "Pi agent configuration directory is missing",
            )
        return self.node_binary or _find_node()


def _parse_auth_result(
    response: dict[str, object],
    model: OpenModel,
) -> PiAuthResult:
    identity = (
        response.get("provider"),
        response.get("model"),
        response.get("api"),
    )
    expected = (model.provider_id, model.upstream_id, model.api)
    if identity != expected:
        raise GatewayError(
            503,
            "pi_auth_route_mismatch",
            f"Pi authentication returned the wrong route for {model.provider_id!r}",
        )
    api_key = response.get("apiKey")
    if api_key is not None and not isinstance(api_key, str):
        raise GatewayError(
            503,
            "pi_auth_response_invalid",
            f"Pi authentication returned invalid data for {model.provider_id!r}",
        )
    raw_headers = response.get("headers")
    if not isinstance(raw_headers, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in raw_headers.items()
    ):
        raise GatewayError(
            503,
            "pi_auth_response_invalid",
            f"Pi authentication returned invalid data for {model.provider_id!r}",
        )
    base_url = response.get("baseUrl")
    if base_url is not None and not isinstance(base_url, str):
        raise GatewayError(
            503,
            "pi_auth_response_invalid",
            f"Pi authentication returned invalid data for {model.provider_id!r}",
        )
    return PiAuthResult(
        provider_id=model.provider_id,
        model_id=model.upstream_id,
        api=model.api,
        api_key=api_key or None,
        headers=dict(raw_headers),
        base_url=base_url or None,
    )
