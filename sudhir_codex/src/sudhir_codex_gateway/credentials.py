"""Resolve Pi provider credentials without exposing them to diagnostics."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .catalog import OpenModel
from .errors import GatewayError
from .pi_auth_worker import PiAuthWorker
from .platform_support import is_windows

ENV_REFERENCE = re.compile(
    r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$"
)
FORBIDDEN_AUTH_HEADERS = frozenset(
    {"connection", "content-length", "host", "transfer-encoding"}
)


@dataclass(frozen=True)
class ResolvedRequestAuth:
    headers: dict[str, str]
    base_url: str | None = None


class CredentialResolver:
    """Resolve one provider key using explicit Pi config, then Pi auth.json."""

    def __init__(
        self,
        auth_path: Path,
        command_timeout_seconds: float = 5.0,
        *,
        oauth_worker: PiAuthWorker | None = None,
    ) -> None:
        self.auth_path = auth_path
        self.command_timeout_seconds = command_timeout_seconds
        self.oauth_worker = oauth_worker

    def authorization_headers(self, model: OpenModel) -> dict[str, str]:
        return self.resolve(model).headers

    def resolve(self, model: OpenModel) -> ResolvedRequestAuth:
        key = self._from_expression(model)
        if key:
            return ResolvedRequestAuth(_default_headers(model, key))

        entry = self._auth_entry(model.provider_id)
        if entry is None:
            if _is_loopback(model.base_url):
                return ResolvedRequestAuth({})
            raise GatewayError(
                503,
                "pi_credential_missing",
                f"No credential is available for Pi provider {model.provider_id!r}",
            )
        auth_type = entry.get("type")
        if auth_type == "oauth":
            if self.oauth_worker is None:
                raise GatewayError(
                    503,
                    "pi_auth_worker_missing",
                    f"Pi OAuth resolver is unavailable for provider {model.provider_id!r}",
                )
            resolved = self.oauth_worker.resolve(model)
            headers = _merge_auth_headers(
                _default_headers(model, resolved.api_key),
                resolved.headers,
            )
            if not headers and not _is_loopback(resolved.base_url or model.base_url):
                raise GatewayError(
                    503,
                    "pi_credential_missing",
                    f"No credential is available for Pi provider {model.provider_id!r}",
                )
            if resolved.base_url is not None:
                _validate_base_url(model.provider_id, resolved.base_url)
            return ResolvedRequestAuth(headers, resolved.base_url)
        if auth_type != "api_key":
            raise GatewayError(
                503,
                "pi_auth_type_unsupported",
                f"Pi provider {model.provider_id!r} has unsupported auth type {auth_type!r}",
            )
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise GatewayError(
                503,
                "pi_credential_missing",
                f"Pi provider {model.provider_id!r} has no API key",
            )
        return ResolvedRequestAuth(_default_headers(model, key))

    def _from_expression(self, model: OpenModel) -> str | None:
        expression = model.api_key_expression
        if not expression:
            return None
        match = ENV_REFERENCE.fullmatch(expression)
        if match:
            name = match.group(1) or match.group(2)
            value = os.environ.get(name, "")
            return value.strip() or None
        if expression.startswith("!"):
            command = expression[1:].strip()
            if not command:
                return None
            try:
                completed = subprocess.run(
                    _credential_command(command),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.command_timeout_seconds,
                    env=os.environ.copy(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise GatewayError(
                    503,
                    "pi_credential_command_failed",
                    f"Credential command failed for Pi provider {model.provider_id!r}",
                ) from exc
            if completed.returncode != 0:
                raise GatewayError(
                    503,
                    "pi_credential_command_failed",
                    f"Credential command failed for Pi provider {model.provider_id!r}",
                )
            return completed.stdout.rstrip("\r\n") or None
        return expression

    def _auth_entry(self, provider_id: str) -> dict[str, object] | None:
        document = self._read_auth_document()
        if document is None:
            return None
        entry = document.get(provider_id)
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise GatewayError(
                503,
                "pi_auth_invalid",
                f"Pi auth entry for {provider_id!r} is invalid",
            )
        return entry

    def _read_auth_document(self) -> dict[str, object] | None:
        try:
            document = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise GatewayError(
                503,
                "pi_auth_invalid",
                "Pi auth file could not be read as JSON",
            ) from exc
        if not isinstance(document, dict):
            raise GatewayError(
                503,
                "pi_auth_invalid",
                "Pi auth file must contain an object",
            )
        return document


def _is_loopback(base_url: str) -> bool:
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname in {"127.0.0.1", "::1", "localhost"}


def _default_headers(model: OpenModel, key: str | None) -> dict[str, str]:
    if not key:
        return {}
    if model.api == "anthropic-messages":
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    return {"Authorization": f"Bearer {key}"}


def _merge_auth_headers(
    defaults: dict[str, str],
    configured: dict[str, str],
) -> dict[str, str]:
    merged = dict(defaults)
    for name, value in configured.items():
        lower_name = name.lower()
        if lower_name in FORBIDDEN_AUTH_HEADERS:
            continue
        for existing in list(merged):
            if existing.lower() == lower_name:
                del merged[existing]
        merged[name] = value
    return merged


def _validate_base_url(provider_id: str, base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GatewayError(
            503,
            "pi_provider_endpoint_invalid",
            f"Provider {provider_id!r} returned an unsafe base URL",
        )
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and hostname:
        return
    if parsed.scheme == "http" and hostname in {"127.0.0.1", "::1", "localhost"}:
        return
    raise GatewayError(
        503,
        "pi_provider_endpoint_invalid",
        f"Provider {provider_id!r} must use HTTPS or loopback HTTP",
    )


def _credential_command(command: str) -> list[str]:
    if is_windows():
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
    return ["/bin/sh", "-lc", command]
