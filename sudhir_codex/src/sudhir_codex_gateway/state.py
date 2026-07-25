"""Create and update only Sudhir-Codex's private local state."""

import datetime as dt
import json
import os
import re
import secrets
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .app import GATEWAY_TOKEN_HEADER
from .errors import GatewayError
from .platform_support import core_binary_name
from .platform_support import ensure_private_directory
from .platform_support import ensure_private_file
from .platform_support import installed_launcher_path
from .platform_support import private_access_label
from .platform_support import venv_python_path
from .server import DEFAULT_PORT

MCP_BEGIN = "# BEGIN SUDHIR-CODEX IMPORTED MCP"
MCP_END = "# END SUDHIR-CODEX IMPORTED MCP"
BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
MULTI_AGENT_V2_SECTION = re.compile(
    r"(?m)^[ \t]*\[features\.multi_agent_v2\][ \t]*(?:#.*)?$"
)
TOML_SECTION = re.compile(r"(?m)^[ \t]*\[")
TOOL_NAMESPACE = re.compile(r"(?m)^[ \t]*tool_namespace[ \t]*=.*$")
SUDHIR_AGENT_NAMESPACE = 'tool_namespace = "sudhir_agents"'
SHELL_POLICY_SECTION = re.compile(
    r"(?m)^[ \t]*\[shell_environment_policy\][ \t]*(?:#.*)?$"
)
SHELL_FILTERS_SECTION = re.compile(
    r"(?m)^[ \t]*\[shell_environment_policy\.filters\][ \t]*(?:#.*)?$"
)
GATEWAY_TOKEN_FILTER = re.compile(
    r'(?mi)^[ \t]*(?:"SUDHIR_CODEX_GATEWAY_TOKEN"|'
    r"SUDHIR_CODEX_GATEWAY_TOKEN)[ \t]*=.*$"
)
LEGACY_EXCLUDE = re.compile(r"(?m)^[ \t]*exclude[ \t]*=.*$")
GATEWAY_TOKEN_FILTER_LINE = 'SUDHIR_CODEX_GATEWAY_TOKEN = "exclude"'


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    state_dir: Path
    pi_agent_dir: Path
    official_codex_home_override: Path | None = None

    @property
    def official_codex_home(self) -> Path:
        return self.official_codex_home_override or (Path.home() / ".codex")

    @property
    def gateway_dir(self) -> Path:
        return self.state_dir / "gateway"

    @property
    def token_file(self) -> Path:
        return self.gateway_dir / "client-token"

    @property
    def pid_file(self) -> Path:
        return self.gateway_dir / "gateway.pid"

    @property
    def start_lock_file(self) -> Path:
        return self.gateway_dir / "gateway-start.lock"

    @property
    def gateway_log(self) -> Path:
        return self.gateway_dir / "gateway.log"

    @property
    def config_file(self) -> Path:
        return self.state_dir / "config.toml"

    @property
    def private_auth_file(self) -> Path:
        return self.state_dir / "auth.json"

    @property
    def official_auth_file(self) -> Path:
        return self.official_codex_home / "auth.json"

    @property
    def core_binary(self) -> Path:
        return self.repo_root / "dist" / core_binary_name()

    @property
    def venv_python(self) -> Path:
        return venv_python_path(self.repo_root)

    @property
    def installed_launcher(self) -> Path:
        return installed_launcher_path(self.repo_root)

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{DEFAULT_PORT}"


def validate_isolation(paths: RuntimePaths) -> None:
    repo = paths.repo_root.expanduser().resolve()
    state = paths.state_dir.expanduser().resolve()
    official = paths.official_codex_home.expanduser().resolve()
    if state == official:
        raise GatewayError(
            78,
            "official_home_rejected",
            "Sudhir-Codex state may not be the official ~/.codex directory",
        )
    if _is_relative_to(state, official) or _is_relative_to(official, state):
        raise GatewayError(
            78,
            "state_overlap_rejected",
            "Sudhir-Codex and official Codex state directories may not overlap",
        )
    if repo == official or _is_relative_to(repo, official):
        raise GatewayError(
            78,
            "repo_overlap_rejected",
            "Sudhir-Codex source may not be inside official Codex state",
        )
    for path in (paths.state_dir, paths.token_file, paths.private_auth_file):
        if path.is_symlink():
            raise GatewayError(
                78,
                "symlink_rejected",
                f"Sudhir-Codex refuses symlinked private path {path}",
            )


def ensure_private_state(paths: RuntimePaths) -> str:
    validate_isolation(paths)
    ensure_private_directory(paths.state_dir)
    ensure_private_directory(paths.gateway_dir)
    token = ensure_gateway_token(paths)
    if not paths.config_file.exists():
        _atomic_write(
            paths.config_file,
            _base_config(paths).encode("utf-8"),
            mode=0o600,
        )
    elif paths.config_file.is_symlink():
        raise GatewayError(
            78,
            "symlink_rejected",
            "Sudhir-Codex config.toml may not be a symlink",
        )
    else:
        ensure_private_file(paths.config_file)
    _ensure_multi_agent_namespace(paths.config_file)
    _ensure_gateway_token_exclusion(paths.config_file)
    return token


def ensure_gateway_token(paths: RuntimePaths) -> str:
    if paths.token_file.exists():
        if paths.token_file.is_symlink():
            raise GatewayError(
                78,
                "symlink_rejected",
                "Gateway token file may not be a symlink",
            )
        token = paths.token_file.read_text(encoding="ascii").strip()
        if len(token) < 32:
            raise GatewayError(
                78,
                "gateway_token_invalid",
                "Existing gateway token is invalid",
            )
        ensure_private_file(paths.token_file)
        return token
    token = secrets.token_urlsafe(32)
    _atomic_write(paths.token_file, f"{token}\n".encode("ascii"), mode=0o600)
    return token


def rotate_gateway_token(paths: RuntimePaths) -> str:
    validate_isolation(paths)
    token = secrets.token_urlsafe(32)
    _atomic_write(paths.token_file, f"{token}\n".encode("ascii"), mode=0o600)
    return token


def import_official_auth(paths: RuntimePaths) -> Path | None:
    """Copy official auth atomically; return a backup path when one was made."""

    validate_isolation(paths)
    source = paths.official_auth_file
    destination = paths.private_auth_file
    if source.is_symlink() or not source.is_file():
        raise GatewayError(
            66,
            "official_auth_missing",
            f"Official Codex auth is not a regular file at {source}",
        )
    if destination.is_symlink():
        raise GatewayError(
            78,
            "symlink_rejected",
            "Private auth destination may not be a symlink",
        )
    ensure_private_directory(paths.state_dir)
    backup: Path | None = None
    if destination.exists():
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        backup = destination.with_name(f"auth.json.backup.{stamp}.{os.getpid()}")
        shutil.copyfile(destination, backup, follow_symlinks=False)
        ensure_private_file(backup)
    _atomic_write(destination, source.read_bytes(), mode=0o600)
    return backup


def import_official_mcp(paths: RuntimePaths) -> int:
    """Import only parsed mcp_servers tables into the private config."""

    validate_isolation(paths)
    official_config = paths.official_codex_home / "config.toml"
    if official_config.is_symlink():
        raise GatewayError(
            78,
            "symlink_rejected",
            "Official config symlinks are not imported",
        )
    if not official_config.exists():
        servers: dict[str, Any] = {}
    else:
        try:
            document = tomllib.loads(official_config.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise GatewayError(
                65,
                "official_config_invalid",
                "Official Codex config could not be parsed",
            ) from exc
        raw_servers = document.get("mcp_servers", {})
        if not isinstance(raw_servers, dict):
            raise GatewayError(
                65,
                "official_mcp_invalid",
                "Official mcp_servers configuration is not a table",
            )
        servers = raw_servers

    ensure_private_state(paths)
    current = paths.config_file.read_text(encoding="utf-8")
    current = _without_marked_mcp(current).rstrip() + "\n\n"
    block = [MCP_BEGIN]
    for name in sorted(servers):
        value = servers[name]
        if not isinstance(value, dict):
            raise GatewayError(
                65,
                "official_mcp_invalid",
                f"MCP server {name!r} is not a table",
            )
        block.extend(_toml_table(["mcp_servers", str(name)], value))
    block.append(MCP_END)
    block.append("")
    _atomic_write(
        paths.config_file,
        (current + "\n".join(block)).encode("utf-8"),
        mode=0o600,
    )
    return len(servers)


def file_mode(path: Path) -> str | None:
    return private_access_label(path)


def _base_config(paths: RuntimePaths) -> str:
    return f"""# Private configuration for Sudhir-Codex. Do not symlink to ~/.codex.
model_provider = "sudhir_gateway"
check_for_update_on_startup = false
cli_auth_credentials_store = "file"

[agents]
enabled = true
max_concurrent_threads_per_session = 6

[features.multi_agent_v2]
tool_namespace = "sudhir_agents"

[shell_environment_policy.filters]
SUDHIR_CODEX_GATEWAY_TOKEN = "exclude"

[analytics]
enabled = false

[feedback]
enabled = false

[otel]
log_user_prompt = false
exporter = "none"
trace_exporter = "none"
metrics_exporter = "none"

[features]
enable_request_compression = false

[model_providers.sudhir_gateway]
name = "Sudhir Gateway"
base_url = "{paths.gateway_url}/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0

[model_providers.sudhir_gateway.env_http_headers]
"{GATEWAY_TOKEN_HEADER}" = "SUDHIR_CODEX_GATEWAY_TOKEN"
"""


def _ensure_multi_agent_namespace(config_file: Path) -> None:
    current = config_file.read_text(encoding="utf-8")
    section = MULTI_AGENT_V2_SECTION.search(current)
    if section is None:
        updated = (
            current.rstrip()
            + "\n\n[features.multi_agent_v2]\n"
            + SUDHIR_AGENT_NAMESPACE
            + "\n"
        )
    else:
        body_start = section.end()
        next_section = TOML_SECTION.search(current, body_start)
        body_end = next_section.start() if next_section else len(current)
        body = current[body_start:body_end]
        if TOOL_NAMESPACE.search(body):
            body = TOOL_NAMESPACE.sub(SUDHIR_AGENT_NAMESPACE, body, count=1)
            updated = current[:body_start] + body + current[body_end:]
        else:
            line_end = current.find("\n", section.end())
            if line_end == -1:
                updated = current + "\n" + SUDHIR_AGENT_NAMESPACE + "\n"
            else:
                insert_at = line_end + 1
                updated = (
                    current[:insert_at]
                    + SUDHIR_AGENT_NAMESPACE
                    + "\n"
                    + current[insert_at:]
                )
    if updated == current:
        return
    tomllib.loads(updated)
    _atomic_write(config_file, updated.encode("utf-8"), mode=0o600)


def _ensure_gateway_token_exclusion(config_file: Path) -> None:
    current = config_file.read_text(encoding="utf-8")
    try:
        document = tomllib.loads(current)
    except tomllib.TOMLDecodeError as exc:
        raise GatewayError(
            65,
            "private_config_invalid",
            "Sudhir-Codex private config is not valid TOML",
        ) from exc

    policy = document.get("shell_environment_policy")
    if policy is not None and not isinstance(policy, dict):
        raise GatewayError(
            65,
            "private_config_invalid",
            "shell_environment_policy must be a table",
        )
    policy = policy or {}
    filters = policy.get("filters")
    uses_legacy_filters = "exclude" in policy or "include_only" in policy

    if isinstance(filters, dict):
        updated = _set_table_field(
            current,
            SHELL_FILTERS_SECTION,
            GATEWAY_TOKEN_FILTER,
            GATEWAY_TOKEN_FILTER_LINE,
        )
        if updated is None:
            raise GatewayError(
                65,
                "private_config_unsupported",
                "shell_environment_policy.filters must use its own TOML table",
            )
    elif filters is not None:
        raise GatewayError(
            65,
            "private_config_invalid",
            "shell_environment_policy.filters must be a table",
        )
    elif uses_legacy_filters:
        excludes = policy.get("exclude", [])
        if not isinstance(excludes, list) or not all(
            isinstance(value, str) for value in excludes
        ):
            raise GatewayError(
                65,
                "private_config_invalid",
                "shell_environment_policy.exclude must be a string list",
            )
        excludes = [
            value
            for value in excludes
            if value.casefold() != "sudhir_codex_gateway_token".casefold()
        ]
        excludes.append("SUDHIR_CODEX_GATEWAY_TOKEN")
        updated = _set_table_field(
            current,
            SHELL_POLICY_SECTION,
            LEGACY_EXCLUDE,
            f"exclude = {_toml_value(excludes)}",
        )
        if updated is None:
            raise GatewayError(
                65,
                "private_config_unsupported",
                "shell_environment_policy must use its own TOML table",
            )
    else:
        if policy and SHELL_POLICY_SECTION.search(current) is None:
            raise GatewayError(
                65,
                "private_config_unsupported",
                "shell_environment_policy must use its own TOML table",
            )
        updated = (
            current.rstrip()
            + "\n\n[shell_environment_policy.filters]\n"
            + GATEWAY_TOKEN_FILTER_LINE
            + "\n"
        )

    if updated == current:
        return
    tomllib.loads(updated)
    _atomic_write(config_file, updated.encode("utf-8"), mode=0o600)


def _set_table_field(
    text: str,
    section_pattern: re.Pattern[str],
    field_pattern: re.Pattern[str],
    replacement: str,
) -> str | None:
    section = section_pattern.search(text)
    if section is None:
        return None
    body_start = section.end()
    next_section = TOML_SECTION.search(text, body_start)
    body_end = next_section.start() if next_section else len(text)
    body = text[body_start:body_end]
    if field_pattern.search(body):
        body = field_pattern.sub(replacement, body, count=1)
        return text[:body_start] + body + text[body_end:]
    line_end = text.find("\n", section.end())
    if line_end == -1:
        return text + "\n" + replacement + "\n"
    insert_at = line_end + 1
    return text[:insert_at] + replacement + "\n" + text[insert_at:]


def _without_marked_mcp(text: str) -> str:
    start = text.find(MCP_BEGIN)
    if start < 0:
        return text
    end = text.find(MCP_END, start)
    if end < 0:
        raise GatewayError(
            65,
            "private_config_invalid",
            "Private config contains an unterminated imported MCP block",
        )
    end += len(MCP_END)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def _toml_table(path: list[str], table: dict[str, Any]) -> list[str]:
    lines = [f"[{'.'.join(_toml_key(part) for part in path)}]"]
    nested: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(table):
        value = table[key]
        if isinstance(value, dict):
            nested.append((str(key), value))
        elif value is not None:
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
    lines.append("")
    for key, value in nested:
        lines.extend(_toml_table([*path, key], value))
    return lines


def _toml_key(value: str) -> str:
    return value if BARE_TOML_KEY.fullmatch(value) else json.dumps(value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    raise GatewayError(
        65,
        "official_mcp_invalid",
        f"Unsupported MCP config value type {type(value).__name__}",
    )


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    ensure_private_directory(path.parent)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ensure_private_file(path, mode=mode)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
