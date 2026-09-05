"""Resolve provider authentication through Pi's registered provider runtime."""

import base64
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
DEFAULT_PI_INFERENCE_TIMEOUT_SECONDS = 660.0


@dataclass(frozen=True)
class PiAuthResult:
    provider_id: str
    model_id: str
    api: str
    api_key: str | None
    headers: dict[str, str]
    base_url: str | None


@dataclass(frozen=True)
class PiInferenceResult:
    provider_id: str
    model_id: str
    api: str
    destination: str
    response: dict[str, object]


class PiInferenceHTTPError(GatewayError):
    """An upstream HTTP response preserved by Pi's native provider worker."""

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        super().__init__(status, "pi_inference_upstream_error", "Pi inference failed")
        self.headers = headers
        self.body = body


class PiAuthWorker(Protocol):
    def resolve(self, model: OpenModel) -> PiAuthResult: ...

    def infer(
        self,
        model: OpenModel,
        chat_request: dict[str, object],
        reasoning: str,
    ) -> PiInferenceResult: ...

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
        inference_timeout_seconds: float = DEFAULT_PI_INFERENCE_TIMEOUT_SECONDS,
    ) -> None:
        self.worker_script = worker_script
        self.agent_dir = agent_dir
        self.node_binary = node_binary
        self.environment = environment
        self.timeout_seconds = timeout_seconds
        self.inference_timeout_seconds = inference_timeout_seconds
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen[str]] = set()
        self._closed = False

    def resolve(self, model: OpenModel) -> PiAuthResult:
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "provider": model.provider_id,
            "model": model.upstream_id,
            "api": model.api,
        }
        response = self._exchange(request, model, request_id, operation="authentication")
        if response.get("ok") is not True:
            raise GatewayError(
                503,
                "pi_auth_resolution_failed",
                f"Pi authentication failed for provider {model.provider_id!r}",
            )
        return _parse_auth_result(response, model)

    def infer(
        self,
        model: OpenModel,
        chat_request: dict[str, object],
        reasoning: str,
    ) -> PiInferenceResult:
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "op": "infer",
            "provider": model.provider_id,
            "model": model.upstream_id,
            "api": model.api,
            "chatRequest": chat_request,
            "reasoning": reasoning,
        }
        response = self._exchange(
            request,
            model,
            request_id,
            operation="inference",
            timeout_seconds=self.inference_timeout_seconds,
        )
        if response.get("ok") is not True:
            upstream = response.get("upstream")
            if isinstance(upstream, dict):
                raise _parse_inference_http_error(upstream, model)
            raise GatewayError(
                502,
                "pi_inference_failed",
                f"Pi inference failed for provider {model.provider_id!r}",
            )
        return _parse_inference_result(response, model)

    def _exchange(
        self,
        request: dict[str, object],
        model: OpenModel,
        request_id: str,
        *,
        operation: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        node = self._prepare()
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
                "Pi runtime worker could not be started",
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
                timeout=timeout_seconds or self.timeout_seconds,
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
                f"Pi {operation} timed out for provider {model.provider_id!r}",
            ) from exc
        finally:
            with self._lock:
                self._active.discard(process)

        response = _protocol_response(stdout, request_id)
        if response is None:
            raise GatewayError(
                503,
                "pi_auth_worker_unavailable",
                f"Pi {operation} is unavailable for provider {model.provider_id!r}",
            )
        return response

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


def _parse_inference_result(
    response: dict[str, object],
    model: OpenModel,
) -> PiInferenceResult:
    identity = (
        response.get("provider"),
        response.get("model"),
        response.get("api"),
    )
    expected = (model.provider_id, model.upstream_id, model.api)
    destination = response.get("destination")
    result = response.get("response")
    if identity != expected or not isinstance(destination, str) or not destination:
        raise GatewayError(
            502,
            "pi_inference_response_invalid",
            f"Pi inference returned invalid routing data for {model.provider_id!r}",
        )
    if not isinstance(result, dict):
        raise GatewayError(
            502,
            "pi_inference_response_invalid",
            f"Pi inference returned invalid data for {model.provider_id!r}",
        )
    return PiInferenceResult(
        provider_id=model.provider_id,
        model_id=model.upstream_id,
        api=model.api,
        destination=destination,
        response=result,
    )


def _parse_inference_http_error(
    upstream: dict[str, object],
    model: OpenModel,
) -> PiInferenceHTTPError:
    status = upstream.get("status")
    raw_headers = upstream.get("headers")
    encoded_body = upstream.get("bodyBase64")
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or status < 400
        or status > 599
        or not isinstance(raw_headers, dict)
        or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in raw_headers.items()
        )
        or not isinstance(encoded_body, str)
    ):
        raise GatewayError(
            502,
            "pi_inference_response_invalid",
            f"Pi inference returned invalid error data for {model.provider_id!r}",
        )
    try:
        body = base64.b64decode(encoded_body, validate=True)
    except ValueError as exc:
        raise GatewayError(
            502,
            "pi_inference_response_invalid",
            f"Pi inference returned invalid error data for {model.provider_id!r}",
        ) from exc
    allowed_headers = {"content-type", "request-id", "x-request-id"}
    headers = {
        name.lower(): value
        for name, value in raw_headers.items()
        if name.lower() in allowed_headers
    }
    headers.setdefault("content-type", "application/json")
    return PiInferenceHTTPError(status, headers, body)
