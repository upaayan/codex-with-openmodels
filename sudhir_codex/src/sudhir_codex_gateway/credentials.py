"""Resolve Pi provider credentials without exposing them to diagnostics."""

import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .catalog import OpenModel
from .errors import GatewayError
from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file
from .platform_support import is_windows

ENV_REFERENCE = re.compile(
    r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$"
)
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_OAUTH_REFRESH_SKEW_MS = 5 * 60 * 1000
XAI_OAUTH_DEFAULT_LIFETIME_SECONDS = 3600
XAI_OAUTH_TIMEOUT_SECONDS = 15.0


class CredentialResolver:
    """Resolve one provider key using explicit Pi config, then Pi auth.json."""

    def __init__(self, auth_path: Path, command_timeout_seconds: float = 5.0) -> None:
        self.auth_path = auth_path
        self.command_timeout_seconds = command_timeout_seconds
        self._oauth_lock = threading.Lock()

    def authorization_headers(self, model: OpenModel) -> dict[str, str]:
        key = self._from_expression(model)
        if not key:
            key = self._from_auth_file(model.provider_id)
        if not key:
            if _is_loopback(model.base_url):
                return {}
            raise GatewayError(
                503,
                "pi_credential_missing",
                f"No credential is available for Pi provider {model.provider_id!r}",
            )
        if model.api == "anthropic-messages":
            return {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
        return {"Authorization": f"Bearer {key}"}

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

    def _from_auth_file(self, provider_id: str) -> str | None:
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
        auth_type = entry.get("type")
        if auth_type == "oauth" and provider_id == "xai":
            return self._xai_oauth_access_token()
        if auth_type != "api_key":
            raise GatewayError(
                503,
                "pi_auth_type_unsupported",
                f"Pi provider {provider_id!r} has unsupported auth type {auth_type!r}",
            )
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise GatewayError(
                503,
                "pi_credential_missing",
                f"Pi provider {provider_id!r} has no API key",
            )
        return key

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

    def _xai_oauth_access_token(self) -> str:
        with self._oauth_lock:
            document = self._read_auth_document()
            if document is None:
                raise GatewayError(
                    503,
                    "pi_credential_missing",
                    "Pi provider 'xai' has no OAuth credential",
                )
            entry = document.get("xai")
            if not isinstance(entry, dict) or entry.get("type") != "oauth":
                raise GatewayError(
                    503,
                    "pi_auth_invalid",
                    "Pi OAuth entry for 'xai' is invalid",
                )

            current_access = _current_xai_access(entry)
            if current_access is not None:
                return current_access

            refresh_token = entry.get("refresh")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise GatewayError(
                    503,
                    "pi_credential_missing",
                    "Pi provider 'xai' has no OAuth refresh token",
                )
            refreshed = _refresh_xai_oauth(refresh_token)

            latest = self._read_auth_document()
            if latest is None:
                raise GatewayError(
                    503,
                    "pi_auth_update_failed",
                    "Pi auth file disappeared during xAI OAuth refresh",
                )
            latest_entry = latest.get("xai")
            if latest_entry != entry:
                if isinstance(latest_entry, dict):
                    concurrent_access = _current_xai_access(latest_entry)
                    if concurrent_access is not None:
                        return concurrent_access
                raise GatewayError(
                    503,
                    "pi_auth_update_conflict",
                    "Pi xAI auth changed during OAuth refresh; retry",
                )

            latest["xai"] = refreshed
            try:
                _atomic_write_auth_document(self.auth_path, latest)
            except OSError as exc:
                raise GatewayError(
                    503,
                    "pi_auth_update_failed",
                    "Pi auth file could not save refreshed xAI OAuth credentials",
                ) from exc
            return refreshed["access"]


def _current_xai_access(entry: dict[str, object]) -> str | None:
    access = entry.get("access")
    expires = entry.get("expires")
    if (
        isinstance(access, str)
        and access
        and isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and math.isfinite(expires)
        and expires > time.time() * 1000
    ):
        return access
    return None


def _refresh_xai_oauth(refresh_token: str) -> dict[str, object]:
    try:
        response = httpx.post(
            XAI_OAUTH_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "client_id": XAI_OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            },
            timeout=XAI_OAUTH_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh could not reach the authentication service",
        ) from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            f"xAI OAuth token refresh failed with HTTP {response.status_code}",
        )
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh returned invalid JSON",
        ) from exc
    if not isinstance(body, dict):
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh returned an invalid response",
        )

    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh returned no access token",
        )
    new_refresh = body.get("refresh_token", refresh_token)
    if not isinstance(new_refresh, str) or not new_refresh:
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh returned an invalid refresh token",
        )
    lifetime = body.get("expires_in", XAI_OAUTH_DEFAULT_LIFETIME_SECONDS)
    if (
        not isinstance(lifetime, (int, float))
        or isinstance(lifetime, bool)
        or not math.isfinite(lifetime)
        or lifetime <= 0
    ):
        raise GatewayError(
            503,
            "pi_oauth_refresh_failed",
            "xAI OAuth token refresh returned an invalid expiry",
        )
    expires = int(
        time.time() * 1000
        + lifetime * 1000
        - XAI_OAUTH_REFRESH_SKEW_MS
    )
    return {
        "type": "oauth",
        "access": access,
        "refresh": new_refresh,
        "expires": expires,
    }


def _atomic_write_auth_document(path: Path, document: dict[str, object]) -> None:
    ensure_private_directory(path.parent)
    content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ensure_private_file(path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _is_loopback(base_url: str) -> bool:
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname in {"127.0.0.1", "::1", "localhost"}


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
