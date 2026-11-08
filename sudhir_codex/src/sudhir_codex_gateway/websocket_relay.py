"""GPT Responses relay; wsproto owns handshakes, framing and compression."""

import json
import socket
import ssl
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection, BytesMessage, CloseConnection, Ping, RejectConnection,
    RejectData, Request, TextMessage,
)
from wsproto.extensions import PerMessageDeflate

from .app import BufferedResponse, _gpt_compatible_request, _incoming_header
from .errors import GatewayError

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MODEL_HEADER = "x-sudhir-selected-model"
RESPONSE_HEADERS = {
    "x-codex-turn-state", "x-request-id", "openai-model", "x-server-model",
    "x-reasoning-included", "retry-after", "www-authenticate", "content-type",
}


class _Socket:
    def __init__(self, sock, protocol, stopped, pending=()):
        self.sock, self.protocol, self.stopped = sock, protocol, stopped
        self.pending, self.lock = deque(pending), threading.Lock()
        sock.settimeout(1)

    def send(self, event):
        with self.lock:
            self.sock.sendall(self.protocol.send(event))

    def receive(self):
        parts, size = [], 0
        while not self.stopped.is_set():
            if not self.pending:
                try:
                    data = self.sock.recv(16384)
                except socket.timeout:
                    continue
                if not data:
                    raise EOFError
                with self.lock:
                    self.protocol.receive_data(data)
                    self.pending.extend(self.protocol.events())
            while self.pending:
                event = self.pending.popleft()
                if isinstance(event, (TextMessage, BytesMessage)):
                    parts.append(event.data)
                    size += len(event.data.encode() if isinstance(event.data, str) else event.data)
                    if size > MAX_MESSAGE_BYTES:
                        raise GatewayError(413, "request_too_large", "WebSocket message is too large")
                    if event.message_finished:
                        return ("" if isinstance(event, TextMessage) else b"").join(parts)
                elif isinstance(event, Ping):
                    self.send(event.response())
                elif isinstance(event, CloseConnection):
                    self.send(event.response())
                    raise EOFError
        raise EOFError


def _connect(app, incoming, model, stopped):
    url = urlsplit(app.settings.chatgpt_base_url.rstrip("/") + "/responses")
    sock = socket.create_connection((url.hostname, url.port or (443 if url.scheme == "https" else 80)), 15)
    try:
        if url.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=url.hostname)
        protocol = WSConnection(ConnectionType.CLIENT)
        headers = [(k.encode(), v.encode()) for k, v in app._gpt_headers(incoming, model).items()]
        sock.sendall(protocol.send(Request(
            host=url.netloc, target=url.path, extra_headers=headers,
            extensions=[PerMessageDeflate()],
        )))
        rejected, body = None, bytearray()
        while True:
            data = sock.recv(16384)
            if not data:
                raise EOFError
            protocol.receive_data(data)
            events = list(protocol.events())
            for index, event in enumerate(events):
                if isinstance(event, AcceptConnection):
                    return _Socket(sock, protocol, stopped, events[index + 1:]), event.extra_headers
                if isinstance(event, RejectConnection):
                    rejected = event
                elif isinstance(event, RejectData):
                    body.extend(event.data)
                    if len(body) > MAX_MESSAGE_BYTES:
                        raise ValueError("Oversized upstream handshake")
                    if not event.body_finished:
                        continue
                else:
                    continue
                if rejected and (not rejected.has_body or isinstance(event, RejectData) and event.body_finished):
                    response = BufferedResponse(
                        rejected.status_code,
                        {k.decode(): v.decode() for k, v in rejected.headers if k.decode().lower() in RESPONSE_HEADERS},
                        bytes(body),
                    )
                    sock.close()
                    return response, ()
    except BaseException:
        sock.close()
        raise


def relay(handler):
    """Called only after the HTTP handler authenticates the loopback request."""
    incoming, app = handler._incoming_headers(), handler.gateway.app
    model = _incoming_header(incoming, MODEL_HEADER)
    if not model or model.startswith(("pi-", "cursor/")):
        raise GatewayError(426, "http_transport_required", "This route uses HTTP Responses")
    app._require_known_gpt_model(model, incoming)
    protocol = WSConnection(ConnectionType.SERVER)
    protocol.initiate_upgrade_connection([(k.encode(), v.encode()) for k, v in incoming.items()], handler.path)
    list(protocol.events())
    stopped = threading.Event()
    started = time.monotonic()
    destination_host = urlsplit(app.settings.chatgpt_base_url).hostname or "chatgpt.com"
    try:
        upstream, headers = _connect(app, incoming, model, stopped)
    except (OSError, EOFError, ValueError) as exc:
        app._audit(model, "openai-codex", destination_host, 0, started)
        raise GatewayError(502, "chatgpt_unreachable", "ChatGPT WebSocket backend could not be reached") from exc
    if isinstance(upstream, BufferedResponse):
        app._audit(model, "openai-codex", destination_host, upstream.status, started)
        handler._send_buffered(upstream)
        return
    downstream = _Socket(handler.connection, protocol, stopped)
    handler.close_connection = True
    worker = None
    pending_audits = deque()

    def stop():
        stopped.set()
        for endpoint in (downstream, upstream):
            try:
                endpoint.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def pump(source, destination, *, requests=False):
        try:
            while not stopped.is_set():
                message = source.receive()
                audit_request = False
                started = time.monotonic()
                if requests:
                    payload = json.loads(message)
                    if not isinstance(payload, dict):
                        raise GatewayError(400, "invalid_request", "Responses events must be objects")
                    if payload.get("type") == "response.create":
                        if payload.get("model") != model:
                            raise GatewayError(400, "model_mismatch", "Response model differs from its authenticated connection")
                        audit_request = payload.get("generate") is not False
                        payload = _gpt_compatible_request(payload)
                    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if audit_request:
                    pending_audits.append(started)
                elif not requests and pending_audits and isinstance(message, str):
                    event = json.loads(message)
                    if event.get("type") in {"response.created", "response.completed", "error"}:
                        status = event.get("status", 502) if event.get("type") == "error" else 200
                        app._audit(model, "openai-codex", destination_host, status, pending_audits.popleft())
                destination.send(TextMessage(data=message) if isinstance(message, str) else BytesMessage(data=message))
        except GatewayError as exc:
            try:
                downstream.send(TextMessage(data=json.dumps({"type": "error", "status": exc.status, **exc.as_openai_error()})))
            except OSError:
                pass
        except (OSError, EOFError, ValueError):
            pass
        finally:
            stop()

    try:
        downstream.send(AcceptConnection(
            extensions=[PerMessageDeflate()],
            extra_headers=[(k, v) for k, v in headers if k.decode().lower() in RESPONSE_HEADERS],
        ))
        worker = threading.Thread(target=pump, args=(downstream, upstream), kwargs={"requests": True}, daemon=True)
        worker.start()
        pump(upstream, downstream)
    finally:
        stop()
        upstream.sock.close()
        if worker:
            worker.join(timeout=2)
        while pending_audits:
            app._audit(model, "openai-codex", destination_host, 0, pending_audits.popleft())
