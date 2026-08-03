"""Routing core for the private loopback gateway."""

import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlparse

import httpx

from .adapter import ToolBindings
from .adapter import chat_response_to_sse
from .adapter import responses_to_chat_request
from .anthropic import anthropic_response_to_chat
from .anthropic import chat_request_to_anthropic
from .catalog import Catalog
from .catalog import CatalogLoader
from .catalog import catalog_etag
from .catalog import merged_catalog_document
from .catalog import normalize_gpt_models
from .credentials import CredentialResolver
from .cursor_catalog import CURSOR_MODEL_PREFIX
from .cursor_catalog import cursor_route
from .cursor_prompt import build_cursor_turn
from .cursor_worker import CursorWorker
from .cursor_worker import CursorWorkerClient
from .deepseek_diagnostics import DeepSeekDiagnosticCapture
from .errors import GatewayError
from .openai_responses import openai_response_to_sse
from .openai_responses import responses_to_openai_request
from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file
from .visibility import apply_model_visibility

GATEWAY_TOKEN_HEADER = "X-Sudhir-Gateway-Token"
CHATGPT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
# Source checkouts use Cargo version 0.0.0. The authenticated catalog rejects
# that development version, so advertise the compatibility floor for this
# upstream snapshot when the gateway talks to ChatGPT.
CHATGPT_CLIENT_VERSION = "0.144.0"
PI_CATALOG_LOAD_ATTEMPTS = 3
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
CHATGPT_REQUEST_HEADERS = {
    "accept",
    "authorization",
    "chatgpt-account-id",
    "if-none-match",
    "openai-beta",
    "originator",
    "session-id",
    "thread-id",
    "user-agent",
    "x-client-request-id",
    "x-codex-beta-features",
    "x-codex-installation-id",
    "x-codex-turn-state",
    "x-oai-attestation",
    "x-openai-subagent",
}


@dataclass(frozen=True)
class GatewaySettings:
    repo_root: Path
    state_dir: Path
    pi_agent_dir: Path
    gateway_token: str
    chatgpt_base_url: str = CHATGPT_CODEX_BASE_URL

    @property
    def models_path(self) -> Path:
        return self.pi_agent_dir / "models.json"

    @property
    def pi_auth_path(self) -> Path:
        return self.pi_agent_dir / "auth.json"

    @property
    def model_visibility_path(self) -> Path:
        return self.state_dir / "model-visibility.json"

    @property
    def base_instructions_path(self) -> Path:
        return self.repo_root / "codex-rs" / "models-manager" / "prompt.md"

    @property
    def gateway_state_dir(self) -> Path:
        return self.state_dir / "gateway"

    @property
    def gpt_cache_path(self) -> Path:
        return self.gateway_state_dir / "gpt-models-cache.json"

    @property
    def route_audit_path(self) -> Path:
        return self.gateway_state_dir / "routes.jsonl"

    @property
    def cursor_worker_path(self) -> Path:
        return self.repo_root / "sudhir_codex" / "cursor_worker" / "worker.mjs"

    @property
    def cursor_state_dir(self) -> Path:
        return self.state_dir / "cursor-sdk"

    @property
    def instance_id(self) -> str:
        source = f"{self.repo_root.resolve()}|{self.state_dir.resolve()}"
        return hashlib.sha256(source.encode()).hexdigest()[:20]


@dataclass
class BufferedResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class StreamingResponse:
    response: httpx.Response


class RouteAudit:
    """Private metadata-only route journal; never accepts message content."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        model_id: str,
        provider_id: str,
        destination: str,
        status: int,
        duration_ms: int,
    ) -> None:
        entry = {
            "timestamp": int(time.time()),
            "model_id": model_id,
            "provider_id": provider_id,
            "destination": destination,
            "status": status,
            "duration_ms": duration_ms,
        }
        encoded = json.dumps(entry, separators=(",", ":"), ensure_ascii=True) + "\n"
        with self._lock:
            ensure_private_directory(self.path.parent)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, encoded.encode("utf-8"))
            finally:
                os.close(fd)
            ensure_private_file(self.path)


class GatewayApp:
    """Provider router shared by HTTP request threads."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        http_client: httpx.Client | None = None,
        cursor_worker: CursorWorker | None = None,
    ) -> None:
        self.settings = settings
        self.loader = CatalogLoader(
            settings.models_path,
            settings.base_instructions_path,
        )
        self.credentials = CredentialResolver(settings.pi_auth_path)
        self.client = http_client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(600.0, connect=15.0),
            trust_env=True,
        )
        self.route_audit = RouteAudit(settings.route_audit_path)
        self.deepseek_diagnostics = DeepSeekDiagnosticCapture.from_environment(
            settings.gateway_state_dir
        )
        self._catalog_lock = threading.Lock()
        self._catalog: Catalog | None = None
        self._catalog_identity: tuple[int, int, int, int] | None = None
        self._gpt_catalog_lock = threading.Lock()
        self._gpt_model_ids: frozenset[str] | None = None
        self._gpt_cache_lock = threading.Lock()
        self.cursor_worker = cursor_worker or CursorWorkerClient(
            worker_script=settings.cursor_worker_path,
            state_dir=settings.cursor_state_dir,
            auth_path=settings.pi_auth_path,
        )
        self._cursor_cwd_lock = threading.Lock()
        self._cursor_cwds: dict[str, Path] = {}

    def close(self) -> None:
        try:
            self.cursor_worker.close()
        finally:
            self.client.close()

    def authenticate(self, provided: str | None) -> bool:
        if not isinstance(provided, str):
            return False
        return hmac.compare_digest(provided, self.settings.gateway_token)

    def health(self) -> BufferedResponse:
        return self._json_response(
            200,
            {
                "ok": True,
                "service": "sudhir-codex-gateway",
                "instance_id": self.settings.instance_id,
                "pid": os.getpid(),
            },
        )

    def list_models(
        self,
        incoming_headers: dict[str, str],
        query_string: str,
    ) -> BufferedResponse:
        catalog = self.catalog()
        gpt_models, source = self._gpt_models(incoming_headers, query_string)
        document = merged_catalog_document(
            gpt_models,
            catalog,
            self.loader.base_instructions(),
        )
        apply_model_visibility(document, self.settings.model_visibility_path)
        response = self._json_response(200, document)
        response.headers["ETag"] = catalog_etag(document)
        response.headers["X-Sudhir-GPT-Catalog"] = source
        return response

    def responses(
        self,
        incoming_headers: dict[str, str],
        body: bytes,
    ) -> BufferedResponse | StreamingResponse:
        try:
            request = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GatewayError(
                400, "invalid_json", "Request body is not valid JSON"
            ) from exc
        if not isinstance(request, dict):
            raise GatewayError(400, "invalid_request", "Request body must be an object")
        model_id = request.get("model")
        if not isinstance(model_id, str) or not model_id:
            raise GatewayError(400, "model_missing", "Responses request has no model")

        if cursor_route(model_id) is not None:
            return self._cursor_response(request, incoming_headers, model_id)
        if model_id.startswith(CURSOR_MODEL_PREFIX):
            raise GatewayError(
                404,
                "model_not_found",
                f"Unknown Cursor model ID {model_id!r}; refresh the model catalog",
            )
        if model_id.startswith("pi-"):
            model = self.catalog().by_gateway_id.get(model_id)
            if model is not None:
                return self._open_model_response(request, model)
            raise GatewayError(
                404,
                "model_not_found",
                f"Unknown open-model ID {model_id!r}; refresh the model catalog",
            )
        self._require_known_gpt_model(model_id, incoming_headers)
        return self._gpt_response(request, incoming_headers, model_id)

    def catalog(self, *, refresh: bool = False) -> Catalog:
        with self._catalog_lock:
            identity = self._pi_catalog_identity()
            if refresh or self._catalog is None or identity != self._catalog_identity:
                catalog, identity = self._load_consistent_pi_catalog()
                self._catalog = catalog
                self._catalog_identity = identity
            return self._catalog

    def _pi_catalog_identity(self) -> tuple[int, int, int, int] | None:
        try:
            metadata = self.settings.models_path.stat()
        except OSError:
            return None
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def _load_consistent_pi_catalog(
        self,
    ) -> tuple[Catalog, tuple[int, int, int, int]]:
        for _attempt in range(PI_CATALOG_LOAD_ATTEMPTS):
            identity_before = self._pi_catalog_identity()
            catalog = self.loader.load()
            identity_after = self._pi_catalog_identity()
            if identity_before is not None and identity_before == identity_after:
                return catalog, identity_before
        raise GatewayError(
            503,
            "pi_models_unstable",
            "Shared model-definition file changed while it was being read; retry",
        )

    def _require_known_gpt_model(
        self,
        model_id: str,
        incoming_headers: dict[str, str],
    ) -> None:
        with self._gpt_catalog_lock:
            known_ids = self._gpt_model_ids
        if known_ids is None:
            _models, source = self._gpt_models(incoming_headers, "")
            if source == "unavailable":
                raise GatewayError(
                    503,
                    "gpt_catalog_unavailable",
                    "GPT model catalog is unavailable; refresh models and retry",
                )
            with self._gpt_catalog_lock:
                known_ids = self._gpt_model_ids
        if known_ids is None or model_id not in known_ids:
            raise GatewayError(
                404,
                "model_not_found",
                f"Unknown GPT model ID {model_id!r}; refresh the model catalog",
            )

    def _open_model_response(
        self,
        request: dict[str, Any],
        model: Any,
    ) -> BufferedResponse:
        if model.api == "openai-responses":
            upstream_request, bindings = responses_to_openai_request(request, model)
        else:
            chat_request, bindings = responses_to_chat_request(request, model)
            upstream_request = (
                chat_request_to_anthropic(chat_request, model)
                if model.api == "anthropic-messages"
                else chat_request
            )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "sudhir-codex-gateway/0.1",
            **self.credentials.authorization_headers(model),
        }
        destination = urlparse(model.request_url).hostname or "invalid"
        started = time.monotonic()
        try:
            upstream = self.client.post(
                model.request_url,
                headers=headers,
                json=upstream_request,
            )
        except httpx.HTTPError as exc:
            self._audit(
                model.gateway_id,
                model.provider_id,
                destination,
                0,
                started,
            )
            raise GatewayError(
                502,
                "pi_provider_unreachable",
                f"Provider {model.provider_id!r} could not be reached",
            ) from exc
        self._audit(
            model.gateway_id,
            model.provider_id,
            destination,
            upstream.status_code,
            started,
        )
        if upstream.is_redirect:
            raise GatewayError(
                502,
                "pi_provider_redirect",
                f"Provider {model.provider_id!r} attempted a redirect",
            )
        if upstream.status_code >= 400:
            raise GatewayError(
                502,
                "pi_provider_error",
                (
                    f"Provider {model.provider_id!r} returned HTTP "
                    f"{upstream.status_code}"
                ),
            )
        try:
            upstream_json = upstream.json()
        except ValueError as exc:
            raise GatewayError(
                502,
                "pi_provider_invalid_json",
                f"Provider {model.provider_id!r} returned invalid JSON",
            ) from exc
        capture_id = self.deepseek_diagnostics.record_upstream(
            model_id=model.gateway_id,
            provider_id=model.provider_id,
            upstream_model_id=model.upstream_id,
            api=model.api,
            request=upstream_request,
            response=upstream_json,
            response_body=upstream.content,
            response_headers=upstream.headers,
            known_tool_names=frozenset(bindings.by_encoded),
        )
        try:
            if model.api == "openai-responses":
                sse = openai_response_to_sse(upstream_json, bindings, model)
            else:
                if model.api == "anthropic-messages":
                    upstream_json = anthropic_response_to_chat(upstream_json)
                sse = chat_response_to_sse(upstream_json, bindings)
        except Exception as exc:
            self.deepseek_diagnostics.record_adapter(capture_id, error=exc)
            raise
        self.deepseek_diagnostics.record_adapter(capture_id, sse=sse)
        return BufferedResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
            body=sse,
        )

    def _cursor_response(
        self,
        request: dict[str, Any],
        incoming_headers: dict[str, str],
        model_id: str,
    ) -> BufferedResponse:
        thread_id = (
            _incoming_header(incoming_headers, "thread-id")
            or _incoming_header(incoming_headers, "session-id")
            or ""
        )
        fallback_cwd: Path | None = None
        if thread_id:
            with self._cursor_cwd_lock:
                fallback_cwd = self._cursor_cwds.get(thread_id)
        turn = build_cursor_turn(request, fallback_cwd=fallback_cwd)
        if thread_id:
            with self._cursor_cwd_lock:
                self._cursor_cwds[thread_id] = turn.cwd

        started = time.monotonic()
        try:
            result = self.cursor_worker.turn(
                model_id=model_id,
                cwd=turn.cwd,
                prompt=turn.prompt,
                thread_id=thread_id,
            )
        except GatewayError as exc:
            self._audit(model_id, "cursor", "cursor.com", exc.status, started)
            raise
        self._audit(model_id, "cursor", "cursor.com", 200, started)

        sse = chat_response_to_sse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": result.text,
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": result.input_tokens,
                    "completion_tokens": result.output_tokens,
                    "total_tokens": result.input_tokens + result.output_tokens,
                },
            },
            ToolBindings([]),
        )
        return BufferedResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Sudhir-Cursor-Tool-Calls": str(result.tool_calls),
            },
            body=sse,
        )

    def _gpt_response(
        self,
        request: dict[str, Any],
        incoming_headers: dict[str, str],
        model_id: str,
    ) -> StreamingResponse:
        target = f"{self.settings.chatgpt_base_url.rstrip('/')}/responses"
        headers = _chatgpt_headers(incoming_headers)
        destination = urlparse(target).hostname or "chatgpt.com"
        started = time.monotonic()
        try:
            upstream_request = self.client.build_request(
                "POST",
                target,
                headers=headers,
                json=request,
            )
            upstream = self.client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            self._audit(model_id, "openai-codex", destination, 0, started)
            raise GatewayError(
                502,
                "chatgpt_unreachable",
                "ChatGPT Codex backend could not be reached",
            ) from exc
        self._audit(
            model_id,
            "openai-codex",
            destination,
            upstream.status_code,
            started,
        )
        if upstream.is_redirect:
            upstream.close()
            raise GatewayError(
                502,
                "chatgpt_redirect",
                "ChatGPT Codex backend attempted a redirect",
            )
        return StreamingResponse(upstream)

    def _gpt_models(
        self,
        incoming_headers: dict[str, str],
        query_string: str,
    ) -> tuple[list[dict[str, Any]], str]:
        target = f"{self.settings.chatgpt_base_url.rstrip('/')}/models"
        query_pairs = [
            (name, value)
            for name, value in parse_qsl(query_string, keep_blank_values=True)
            if name != "client_version"
        ]
        query_pairs.append(("client_version", CHATGPT_CLIENT_VERSION))
        query = urlencode(query_pairs)
        if query:
            target = f"{target}?{query}"
        destination = urlparse(target).hostname or "chatgpt.com"
        started = time.monotonic()
        status = 0
        try:
            upstream = self.client.get(
                target,
                headers=_chatgpt_headers(incoming_headers),
                timeout=httpx.Timeout(4.0, connect=2.0),
            )
            status = upstream.status_code
            if upstream.is_redirect:
                raise GatewayError(
                    502,
                    "chatgpt_redirect",
                    "ChatGPT model catalog attempted a redirect",
                )
            upstream.raise_for_status()
            document = upstream.json()
            if not isinstance(document, dict):
                raise ValueError("catalog is not an object")
            models = normalize_gpt_models(document.get("models"))
            self._write_gpt_cache(models)
            self._remember_gpt_models(models)
            self._audit("catalog", "openai-codex", destination, status, started)
            return models, "live"
        except (httpx.HTTPError, ValueError, GatewayError):
            self._audit("catalog", "openai-codex", destination, status, started)
            cached = self._read_gpt_cache()
            if cached is not None:
                self._remember_gpt_models(cached)
                return cached, "cache"
            return [], "unavailable"

    def _write_gpt_cache(self, models: list[dict[str, Any]]) -> None:
        with self._gpt_cache_lock:
            ensure_private_directory(self.settings.gateway_state_dir)
            temporary = self.settings.gpt_cache_path.with_suffix(".tmp")
            encoded = json.dumps({"models": models}, separators=(",", ":"))
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                os.write(fd, encoded.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, self.settings.gpt_cache_path)
            ensure_private_file(self.settings.gpt_cache_path)

    def _remember_gpt_models(self, models: list[dict[str, Any]]) -> None:
        model_ids = frozenset(
            model["slug"] for model in models if isinstance(model.get("slug"), str)
        )
        with self._gpt_catalog_lock:
            self._gpt_model_ids = model_ids

    def _read_gpt_cache(self) -> list[dict[str, Any]] | None:
        try:
            document = json.loads(
                self.settings.gpt_cache_path.read_text(encoding="utf-8")
            )
            return normalize_gpt_models(document.get("models"))
        except (OSError, ValueError, GatewayError, AttributeError):
            return None

    def _audit(
        self,
        model_id: str,
        provider_id: str,
        destination: str,
        status: int,
        started: float,
    ) -> None:
        try:
            self.route_audit.record(
                model_id=model_id,
                provider_id=provider_id,
                destination=destination,
                status=status,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        except OSError:
            # Routing must not fail merely because a private diagnostic cannot be written.
            pass

    @staticmethod
    def _json_response(status: int, body: object) -> BufferedResponse:
        return BufferedResponse(
            status=status,
            headers={"Content-Type": "application/json"},
            body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
        )


def _chatgpt_headers(incoming: dict[str, str]) -> dict[str, str]:
    """Forward only operational GPT headers, never arbitrary client headers."""

    headers: dict[str, str] = {}
    for name, value in incoming.items():
        lowered = name.lower()
        if (
            lowered in HOP_BY_HOP_HEADERS
            or lowered == GATEWAY_TOKEN_HEADER.lower()
            or lowered not in CHATGPT_REQUEST_HEADERS
        ):
            continue
        headers[name] = value
    headers["Accept-Encoding"] = "identity"
    headers["Content-Type"] = "application/json"
    return headers


def _incoming_header(incoming: dict[str, str], target: str) -> str | None:
    target = target.lower()
    for name, value in incoming.items():
        if name.lower() == target and value:
            return value
    return None
