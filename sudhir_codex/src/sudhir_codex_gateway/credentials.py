"""Resolve Pi provider credentials without exposing them to diagnostics."""

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .catalog import OpenModel
from .errors import GatewayError
from .platform_support import is_windows

ENV_REFERENCE = re.compile(
    r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$"
)


class CredentialResolver:
    """Resolve one provider key using explicit Pi config, then Pi auth.json."""

    def __init__(self, auth_path: Path, command_timeout_seconds: float = 5.0) -> None:
        self.auth_path = auth_path
        self.command_timeout_seconds = command_timeout_seconds

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
