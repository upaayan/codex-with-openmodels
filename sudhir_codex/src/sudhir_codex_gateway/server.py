"""Threaded loopback HTTP server for the Sudhir-Codex gateway."""

import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .app import BufferedResponse
from .app import GATEWAY_TOKEN_HEADER
from .app import GatewayApp
from .app import GatewaySettings
from .app import StreamingResponse
from .errors import GatewayError
from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 32_179
MAX_REQUEST_BYTES = 64 * 1024 * 1024
PASSTHROUGH_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "etag",
    "openai-model",
    "x-codex-turn-state",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
}


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Permit a verified private restart to reclaim the loopback port while the
    # previous socket is still in TIME_WAIT. A live listener still owns the bind.
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: GatewayApp) -> None:
        if address[0] != LOOPBACK_HOST:
            raise ValueError("Sudhir-Codex gateway may bind only to 127.0.0.1")
        self.app = app
        super().__init__(address, GatewayRequestHandler)


class GatewayRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SudhirCodexGateway/0.1"
    sys_version = ""

    @property
    def gateway(self) -> GatewayHTTPServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/healthz":
                self._send_buffered(self.gateway.app.health())
                return
            if parsed.path == "/v1/models":
                result = self.gateway.app.list_models(
                    self._incoming_headers(),
                    parsed.query,
                )
                self._send_buffered(result)
                return
            raise GatewayError(404, "not_found", "Gateway route not found")
        except GatewayError as exc:
            self._send_error(exc)
        except Exception:
            self._send_error(
                GatewayError(500, "internal_error", "Gateway request failed")
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        parsed = urlsplit(self.path)
        try:
            if parsed.path not in {
                "/v1/responses",
                "/v1/alpha/search",
                "/v1/images/generations",
                "/v1/images/edits",
            }:
                raise GatewayError(404, "not_found", "Gateway route not found")
            content_encoding = self.headers.get("Content-Encoding", "identity").lower()
            if content_encoding not in {"", "identity"}:
                raise GatewayError(
                    415,
                    "content_encoding_unsupported",
                    "Compressed gateway requests are disabled",
                )
            body = self._read_body()
            if parsed.path == "/v1/alpha/search":
                result = self.gateway.app.search(self._incoming_headers(), body)
            elif parsed.path == "/v1/images/generations":
                result = self.gateway.app.generate_image(
                    self._incoming_headers(),
                    body,
                )
            elif parsed.path == "/v1/images/edits":
                result = self.gateway.app.edit_image(
                    self._incoming_headers(),
                    body,
                )
            else:
                result = self.gateway.app.responses(self._incoming_headers(), body)
            if isinstance(result, BufferedResponse):
                self._send_buffered(result)
            else:
                self._send_streaming(result)
        except GatewayError as exc:
            self._send_error(exc)
        except Exception:
            self._send_error(
                GatewayError(500, "internal_error", "Gateway request failed")
            )

    def log_message(self, _format: str, *args: Any) -> None:
        # Never let BaseHTTPRequestHandler print paths or headers.
        return

    def _authorized(self) -> bool:
        provided = self.headers.get(GATEWAY_TOKEN_HEADER)
        if not provided:
            authorization = self.headers.get("Authorization")
            if authorization and authorization.lower().startswith("bearer "):
                provided = authorization[7:].strip()
        if self.gateway.app.authenticate(provided):
            return True
        self._send_error(
            GatewayError(401, "unauthorized", "Gateway authentication failed")
        )
        return False

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise GatewayError(411, "length_required", "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise GatewayError(400, "invalid_length", "Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise GatewayError(413, "request_too_large", "Gateway request is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise GatewayError(400, "truncated_request", "Request body was truncated")
        return body

    def _incoming_headers(self) -> dict[str, str]:
        return {name: value for name, value in self.headers.items()}

    def _send_error(self, error: GatewayError) -> None:
        body = json.dumps(
            error.as_openai_error(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_buffered(
            BufferedResponse(
                status=error.status,
                headers={"Content-Type": "application/json"},
                body=body,
            )
        )

    def _send_buffered(self, response: BufferedResponse) -> None:
        self.send_response(response.status)
        for name, value in response.headers.items():
            if name.lower() not in {"content-length", "connection"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)
            self.wfile.flush()
        self.close_connection = True

    def _send_streaming(self, result: StreamingResponse) -> None:
        upstream = result.response
        try:
            self.send_response(upstream.status_code)
            for name, value in upstream.headers.items():
                if name.lower() in PASSTHROUGH_RESPONSE_HEADERS:
                    self.send_header(name, value)
            if "content-type" not in upstream.headers:
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in upstream.iter_raw():
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        finally:
            upstream.close()
            self.close_connection = True


def run_server(
    settings: GatewaySettings,
    *,
    port: int = DEFAULT_PORT,
    pid_file: Path | None = None,
) -> None:
    """Run until SIGTERM/SIGINT, owning an optional exclusive PID file."""

    ensure_private_directory(settings.gateway_state_dir)
    pid_file = pid_file or settings.gateway_state_dir / "gateway.pid"
    pid_fd = _claim_pid_file(pid_file)
    app = GatewayApp(settings)
    server: GatewayHTTPServer | None = None
    previous_handlers: dict[int, Any] = {}
    try:
        server = GatewayHTTPServer((LOOPBACK_HOST, port), app)

        def request_shutdown(_signum: int, _frame: object) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_shutdown)
        server.serve_forever(poll_interval=0.2)
    finally:
        if server is not None:
            server.server_close()
        app.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        os.close(pid_fd)
        _remove_owned_pid_file(pid_file)


def _claim_pid_file(pid_file: Path) -> int:
    ensure_private_directory(pid_file.parent)
    try:
        fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GatewayError(
            73,
            "gateway_already_running",
            f"Gateway PID file already exists at {pid_file}",
        ) from exc
    encoded = f"{os.getpid()}\n".encode()
    os.write(fd, encoded)
    os.fsync(fd)
    ensure_private_file(pid_file)
    return fd


def _remove_owned_pid_file(pid_file: Path) -> None:
    try:
        current = pid_file.read_text(encoding="ascii").strip()
        if current == str(os.getpid()):
            pid_file.unlink()
    except OSError:
        pass
